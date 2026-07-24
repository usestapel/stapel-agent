"""Generic self-hosted embeddings HTTP adapter.

One synchronous JSON POST to a self-hosted embedding server (gigaam-style
plain HTTP — a thin FastAPI/Flask shim around a local sentence-transformers
model, class bge-m3 / multilingual-e5, where the model is FIXED
server-side and there is no model request parameter).

Wire contract (documented here because the server side is a thin shim the
host deploys)::

    POST {EMBEDDINGS_HTTP_BASE_URL}/embed
        JSON {"texts": ["...", ...]}     (+ any provider_options keys
                                          merged into the body, as-is)
        headers:
            Authorization: Bearer {EMBEDDINGS_HTTP_API_KEY}   (only when
                                          set — self-hosted often has none)

    -> 200 JSON {
        "vectors": [[...], ...],   # one per input text, INPUT ORDER (required)
        "model"?:  str,            # server echo — real attribution
        "dim"?:    int,            # inferred from vectors[0] if absent
        "usage"?:  {...}           # optional server-reported usage
    }

The vector count must equal the input count (a mismatch is a loud fatal
failure, never a misaligned batch). ``raw`` keeps the response minus
``vectors`` (vectors are never stored twice). ``model`` attribution is
the server's echo when present, else None — never a pretended request
value (there is no model request parameter to pretend with).

Settings (all read lazily): ``EMBEDDINGS_HTTP_BASE_URL`` (required),
``EMBEDDINGS_HTTP_API_KEY`` (optional), ``EMBEDDINGS_TIMEOUT``.
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


class HttpServerEmbeddingsProvider(EmbeddingProvider):
    name = "embeddings-http"

    def embed(
        self,
        *,
        texts: list[str],
        timeout_seconds: Optional[int] = None,
        provider_options: Optional[dict] = None,
    ) -> NormalizedEmbeddings:
        batch = require_texts(texts, provider=self.name)
        base_url = (agent_settings.EMBEDDINGS_HTTP_BASE_URL or "").rstrip("/")
        if not base_url:
            raise EmbeddingError(
                "STAPEL_AGENT['EMBEDDINGS_HTTP_BASE_URL'] is not configured",
                provider=self.name,
            )
        timeout = (
            int(agent_settings.EMBEDDINGS_TIMEOUT)
            if timeout_seconds is None
            else int(timeout_seconds)
        )

        body: dict = {"texts": batch}
        if provider_options:
            # The passthrough seam: caller-pinned provider specifics win
            # over (are applied after) the adapter's own params.
            body.update(provider_options)
        headers = {"Content-Type": "application/json"}
        api_key = agent_settings.EMBEDDINGS_HTTP_API_KEY
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        try:
            resp = requests.post(
                f"{base_url}/embed",
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

        raw_vectors = payload.get("vectors")
        if not isinstance(raw_vectors, list):
            raise EmbeddingError(
                f"embeddings response has no 'vectors' list: {str(payload)[:300]}",
                provider=self.name,
            )
        try:
            vectors = [[float(v) for v in vec] for vec in raw_vectors]
        except (TypeError, ValueError) as exc:
            raise EmbeddingError(
                f"embeddings response vector malformed: {exc}", provider=self.name
            ) from exc
        if len(vectors) != len(batch):
            raise EmbeddingError(
                f"embeddings count mismatch: sent {len(batch)} texts, got "
                f"{len(vectors)} vectors — refusing a misaligned batch",
                provider=self.name,
            )

        dim = payload.get("dim")
        try:
            dim = int(dim) if dim is not None else len(vectors[0])
        except (TypeError, ValueError):
            dim = len(vectors[0])

        return NormalizedEmbeddings(
            provider=self.name,
            model=payload.get("model"),
            dim=dim,
            vectors=vectors,
            usage=payload.get("usage"),
            # Never the vectors twice: raw carries everything BUT vectors.
            raw={k: v for k, v in payload.items() if k != "vectors"},
        )


__all__ = ["HttpServerEmbeddingsProvider"]
