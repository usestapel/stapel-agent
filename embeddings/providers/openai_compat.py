"""OpenAI-compatible embeddings adapter.

One adapter covers everything speaking the OpenAI embeddings dialect
(``POST {base}/embeddings`` — the base URL includes the ``/v1`` prefix,
exactly like the LLM ``openai_compat`` provider's ``/chat/completions``):
OpenAI itself, DeepSeek, Together, vLLM/TEI in OpenAI mode, and other
compatibles.

Request: ``{"model": <model>, "input": [<texts>...]}`` (+ any
``provider_options`` merged AFTER, so a caller can pin ``dimensions``,
``encoding_format`` etc. without a core release).

Response: ``{"data": [{"embedding": [...], "index": n}, ...], "model",
"usage"}``. Entries are re-ordered by ``index`` before returning — the
input-order invariant must not depend on the server's serialization
order — and the count must equal the input count (a mismatch is a loud
fatal failure, never a misaligned batch). ``raw`` keeps the response
minus ``data`` (vectors are never stored twice).

Settings (all read lazily): ``EMBEDDINGS_BASE_URL`` (falls back to
``OPENAI_COMPAT_BASE_URL`` — a host already on an OpenAI-flavoured stack
configures nothing extra), ``EMBEDDINGS_API_KEY`` (falls back to
``OPENAI_COMPAT_API_KEY``), ``EMBEDDINGS_MODEL`` (default
``text-embedding-3-small``), ``EMBEDDINGS_TIMEOUT``.
"""
from __future__ import annotations

from typing import Optional

import requests

from ...conf import agent_settings
from ..base import (
    EmbeddingError,
    EmbeddingProvider,
    NormalizedEmbeddings,
    RetryableEmbeddingError,
    require_texts,
)


class OpenAIEmbeddingsProvider(EmbeddingProvider):
    name = "openai-embeddings"

    def default_embedding_model(self) -> Optional[str]:
        return agent_settings.EMBEDDINGS_MODEL

    def embed(
        self,
        *,
        texts: list[str],
        timeout_seconds: Optional[int] = None,
        provider_options: Optional[dict] = None,
    ) -> NormalizedEmbeddings:
        batch = require_texts(texts, provider=self.name)
        base_url = (
            agent_settings.EMBEDDINGS_BASE_URL
            or agent_settings.OPENAI_COMPAT_BASE_URL
            or ""
        ).rstrip("/")
        if not base_url:
            raise EmbeddingError(
                "Embeddings endpoint not configured — set "
                "STAPEL_AGENT['EMBEDDINGS_BASE_URL'] (or OPENAI_COMPAT_BASE_URL)",
                provider=self.name,
            )
        api_key = (
            agent_settings.EMBEDDINGS_API_KEY or agent_settings.OPENAI_COMPAT_API_KEY
        )
        timeout = (
            int(agent_settings.EMBEDDINGS_TIMEOUT)
            if timeout_seconds is None
            else int(timeout_seconds)
        )

        model = self.effective_model()
        body: dict = {"model": model, "input": batch}
        if provider_options:
            # The passthrough seam: caller-pinned provider specifics win
            # over (are applied after) the adapter's own params.
            body.update(provider_options)
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        try:
            resp = requests.post(
                f"{base_url}/embeddings",
                json=body,
                headers=headers,
                timeout=timeout,
            )
        except requests.Timeout as exc:
            raise RetryableEmbeddingError(
                f"embeddings request timed out: {exc}", provider=self.name
            ) from exc
        except requests.RequestException as exc:
            raise RetryableEmbeddingError(
                f"embeddings transport error: {exc}", provider=self.name
            ) from exc

        if resp.status_code == 429:
            raise RetryableEmbeddingError(
                "embeddings endpoint rate-limited", provider=self.name, status_code=429
            )
        if resp.status_code >= 500:
            raise RetryableEmbeddingError(
                f"embeddings endpoint {resp.status_code}: {resp.text[:300]}",
                provider=self.name,
                status_code=resp.status_code,
            )
        if resp.status_code >= 400:
            raise EmbeddingError(
                f"embeddings endpoint {resp.status_code}: {resp.text[:300]}",
                provider=self.name,
                status_code=resp.status_code,
            )

        try:
            payload = resp.json()
        except ValueError as exc:
            raise RetryableEmbeddingError(
                f"embeddings endpoint returned non-JSON: {resp.text[:300]}",
                provider=self.name,
            ) from exc

        data = payload.get("data")
        if not isinstance(data, list):
            raise EmbeddingError(
                f"embeddings response has no 'data' list: {str(payload)[:300]}",
                provider=self.name,
            )
        try:
            # Re-order by the wire ``index`` — input order must not depend
            # on the server's serialization order.
            ordered = sorted(data, key=lambda entry: int(entry["index"]))
            vectors = [
                [float(v) for v in entry["embedding"]] for entry in ordered
            ]
        except (KeyError, TypeError, ValueError) as exc:
            raise EmbeddingError(
                f"embeddings response entry malformed: {exc}", provider=self.name
            ) from exc
        if len(vectors) != len(batch):
            raise EmbeddingError(
                f"embeddings count mismatch: sent {len(batch)} texts, got "
                f"{len(vectors)} vectors — refusing a misaligned batch",
                provider=self.name,
            )

        return NormalizedEmbeddings(
            provider=self.name,
            model=payload.get("model") or model,
            dim=len(vectors[0]),
            vectors=vectors,
            usage=payload.get("usage"),
            # Never the vectors twice: raw carries everything BUT data.
            raw={k: v for k, v in payload.items() if k != "data"},
        )


__all__ = ["OpenAIEmbeddingsProvider"]
