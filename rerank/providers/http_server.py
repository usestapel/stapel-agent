"""Generic self-hosted rerank HTTP adapter — the TEI ``/rerank`` dialect.

One synchronous JSON POST to a self-hosted reranker speaking the
HuggingFace **text-embeddings-inference** (TEI) rerank contract (a TEI
container running bge-reranker / Qwen3-Reranker class models, or any
thin shim mimicking it — the keyless self-host fallback).

Wire contract (TEI's documented ``/rerank`` shape)::

    POST {RERANK_HTTP_BASE_URL}/rerank
        JSON {"query": "...", "texts": ["...", ...]}
             (+ any provider_options keys merged into the body, as-is —
              e.g. TEI's "raw_scores"/"truncate")

    -> 200 JSON [
        {"index": n, "score": f},   # index → position in `texts`
        ...
    ]

The model is fixed server-side (one TEI container serves one model) and
the response names no model — attribution stays None, never a pretended
request value. Indexes are the server's join keys into the input list;
an out-of-range or duplicate index and a count above the input size are
loud fatal failures, never a misaligned join. Results are re-sorted by
score descending here — the contract must not depend on the server's
serialization order — and ``top_n`` is applied client-side after the
sort (uniform with the other adapters).

Settings (all read lazily): ``RERANK_HTTP_BASE_URL`` (required),
``RERANK_TIMEOUT``. No key setting — this is the keyless self-host
fallback; a fronting proxy owns auth if the host needs it.
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


class HttpServerRerankProvider(RerankProvider):
    name = "rerank-http"

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
        base_url = (agent_settings.RERANK_HTTP_BASE_URL or "").rstrip("/")
        if not base_url:
            raise RerankError(
                "STAPEL_AGENT['RERANK_HTTP_BASE_URL'] is not configured",
                provider=self.name,
            )
        timeout = (
            int(agent_settings.RERANK_TIMEOUT)
            if timeout_seconds is None
            else int(timeout_seconds)
        )

        body: dict = {"query": query, "texts": docs}
        if provider_options:
            # The passthrough seam: caller-pinned provider specifics win
            # over (are applied after) the adapter's own params.
            body.update(provider_options)
        headers = {"Content-Type": "application/json"}

        try:
            resp = requests.post(
                f"{base_url}/rerank",
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

        if not isinstance(payload, list):
            raise RerankError(
                f"rerank response is not a list of scored entries: "
                f"{str(payload)[:300]}",
                provider=self.name,
            )
        try:
            results = [
                RerankResult(index=int(entry["index"]), score=float(entry["score"]))
                for entry in payload
            ]
        except (KeyError, TypeError, ValueError) as exc:
            raise RerankError(
                f"rerank response entry malformed: {exc}", provider=self.name
            ) from exc

        return NormalizedRerank(
            provider=self.name,
            model=None,  # fixed server-side, no echo → no pretended attribution
            results=rank_results(
                results, n_documents=len(docs), top_n=top_n, provider=self.name
            ),
            usage=None,
            # The TEI response is the score list and nothing else — with
            # scores living in `results` ONLY, nothing is left for raw.
            raw={},
        )


__all__ = ["HttpServerRerankProvider"]
