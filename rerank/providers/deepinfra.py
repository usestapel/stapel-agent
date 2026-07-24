"""DeepInfra reranker adapter.

One synchronous JSON POST to the DeepInfra **inference** endpoint (the
model-specific dialect — reranker models on DeepInfra are NOT behind the
OpenAI-compat prefix)::

    POST {RERANK_BASE_URL}/inference/{model}
        JSON (built by ``build_rerank_request`` below):
            {"queries": [query, query, ...],   # len(documents) copies
             "documents": [doc0, doc1, ...]}   # paired arrays
            (+ any provider_options keys merged into the body, as-is)
        headers:
            Authorization: Bearer {RERANK_API_KEY}

    -> 200 JSON {
        "scores": [float, ...],   # one per pair, INPUT ORDER (required)
        "input_tokens"?: int,     # → usage {"input_tokens": ...}
        "request_id"?: str,
        "inference_status"?: {...}
    }

DeepInfra's documented reranker interface takes **paired arrays**: entry
``i`` scores ``queries[i]`` against ``documents[i]``. The single-query
rerank case therefore repeats the query once per document — that
repetition is the wire contract, not a bug. [LIVE-VERIFIED
2026-07-24 against Qwen/Qwen3-Reranker-8B: paired arrays accepted,
``scores`` come back in input order.]

``top_n`` is applied client-side after the sort (the inference endpoint
scores every pair; there is no server-side cutoff parameter). ``raw``
keeps the response minus ``scores`` (scores are never stored twice).

Settings (all read lazily): ``RERANK_BASE_URL`` (default
``https://api.deepinfra.com/v1``), ``RERANK_API_KEY`` (required — the
DeepInfra key; app layers alias their ``DEEPINFRA_API_KEY`` onto it),
``RERANK_MODEL`` (default ``Qwen/Qwen3-Reranker-8B``), ``RERANK_TIMEOUT``.
"""
from __future__ import annotations

from typing import Optional

import requests

from ...conf import agent_settings
from ..base import (
    NormalizedRerank,
    RerankError,
    RerankProvider,
    RerankResult,
    RetryableRerankError,
    rank_results,
    require_rerank_inputs,
)


def build_rerank_request(
    query: str,
    documents: list[str],
    provider_options: Optional[dict] = None,
) -> dict:
    """The DeepInfra reranker request body — a pure function so a live
    verification fix is one edit.

    Paired arrays per the documented reranker interface: ``queries[i]``
    is scored against ``documents[i]``, so the single query is repeated
    ``len(documents)`` times. ``provider_options`` merges AFTER (the
    house passthrough seam: caller-pinned provider specifics win).
    """
    body: dict = {
        "queries": [query] * len(documents),
        "documents": list(documents),
    }
    if provider_options:
        body.update(provider_options)
    return body


class DeepInfraRerankProvider(RerankProvider):
    name = "deepinfra-rerank"

    def default_rerank_model(self) -> Optional[str]:
        return agent_settings.RERANK_MODEL

    def rerank(
        self,
        *,
        query: str,
        documents: list[str],
        top_n: Optional[int] = None,
        timeout_seconds: Optional[int] = None,
        provider_options: Optional[dict] = None,
    ) -> NormalizedRerank:
        query, docs = require_rerank_inputs(
            query, documents, top_n=top_n, provider=self.name
        )
        base_url = (agent_settings.RERANK_BASE_URL or "").rstrip("/")
        if not base_url:
            raise RerankError(
                "STAPEL_AGENT['RERANK_BASE_URL'] is not configured",
                provider=self.name,
            )
        model = self.effective_model()
        if not model:
            raise RerankError(
                "STAPEL_AGENT['RERANK_MODEL'] is not configured",
                provider=self.name,
            )
        api_key = agent_settings.RERANK_API_KEY
        if not api_key:
            raise RerankError(
                "STAPEL_AGENT['RERANK_API_KEY'] is not configured",
                provider=self.name,
            )
        timeout = (
            int(agent_settings.RERANK_TIMEOUT)
            if timeout_seconds is None
            else int(timeout_seconds)
        )

        body = build_rerank_request(query, docs, provider_options)
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

        try:
            resp = requests.post(
                f"{base_url}/inference/{model}",
                json=body,
                headers=headers,
                timeout=timeout,
            )
        except requests.Timeout as exc:
            raise RetryableRerankError(
                f"rerank request timed out: {exc}", provider=self.name
            ) from exc
        except requests.RequestException as exc:
            raise RetryableRerankError(
                f"rerank transport error: {exc}", provider=self.name
            ) from exc

        if resp.status_code == 429:
            raise RetryableRerankError(
                "rerank endpoint rate-limited", provider=self.name, status_code=429
            )
        if resp.status_code >= 500:
            raise RetryableRerankError(
                f"rerank endpoint {resp.status_code}: {resp.text[:300]}",
                provider=self.name,
                status_code=resp.status_code,
            )
        if resp.status_code >= 400:
            raise RerankError(
                f"rerank endpoint {resp.status_code}: {resp.text[:300]}",
                provider=self.name,
                status_code=resp.status_code,
            )

        try:
            payload = resp.json()
        except ValueError as exc:
            raise RetryableRerankError(
                f"rerank endpoint returned non-JSON: {resp.text[:300]}",
                provider=self.name,
            ) from exc

        raw_scores = payload.get("scores")
        if not isinstance(raw_scores, list):
            raise RerankError(
                f"rerank response has no 'scores' list: {str(payload)[:300]}",
                provider=self.name,
            )
        if len(raw_scores) != len(docs):
            raise RerankError(
                f"rerank count mismatch: sent {len(docs)} documents, got "
                f"{len(raw_scores)} scores — refusing a misaligned join",
                provider=self.name,
            )
        try:
            results = [
                RerankResult(index=idx, score=float(score))
                for idx, score in enumerate(raw_scores)
            ]
        except (TypeError, ValueError) as exc:
            raise RerankError(
                f"rerank response score malformed: {exc}", provider=self.name
            ) from exc

        input_tokens = payload.get("input_tokens")
        return NormalizedRerank(
            provider=self.name,
            # The inference endpoint echoes no model — the requested one
            # IS the attribution (the URL pins it).
            model=model,
            results=rank_results(
                results, n_documents=len(docs), top_n=top_n, provider=self.name
            ),
            usage={"input_tokens": input_tokens} if input_tokens is not None else None,
            # Never the scores twice: raw carries everything BUT scores.
            raw={k: v for k, v in payload.items() if k != "scores"},
        )


__all__ = ["DeepInfraRerankProvider", "build_rerank_request"]
