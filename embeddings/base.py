"""Embedding provider seam — normalized vectors, ABC, errors.

The seam is BATCH-SHAPED on purpose: ``embed`` takes a list of texts and
returns one vector per text, **in input order** — that invariant is the
whole contract (RAG indexers zip texts with vectors positionally).
Chunking policies, similarity/ranking, vector storage — all of that is
client-app know-how and stays out of this core.

Errors join the house hierarchy: ``EmbeddingError(ProviderError)``
(fatal — bad input/auth) and ``RetryableEmbeddingError`` (429/5xx/
timeouts), same taxonomy as STT/images/diarization.

This module is deliberately Django-free.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Optional

from ..providers.base import ProviderError


class EmbeddingError(ProviderError):
    """Permanent embedding failure (empty/invalid texts, auth, ...)."""

    def __init__(self, message: str, *, provider: str = "", status_code: int | None = None):
        super().__init__(message)
        self.provider = provider
        self.status_code = status_code


class RetryableEmbeddingError(EmbeddingError):
    """Transient embedding failure (network, 429, 5xx, timeout)."""


# ─── Normalized embeddings schema ──────────────────────────────────────


@dataclass
class NormalizedEmbeddings:
    """Output every embedding provider must return.

    Attributes:
        provider: Adapter id (e.g. ``openai-embeddings``). Recorded on
            the PromptLog row for observability.
        model: Model that produced the vectors — the provider's response
            echo when it ships one (real attribution), else the requested
            model; None when the server names no model at all.
        dim: Vector dimensionality (every vector has this length).
        vectors: One embedding per input text, **input order preserved**.
        usage: Provider-reported usage (e.g. ``{"prompt_tokens": ...,
            "total_tokens": ...}``); None when the server reports none.
        raw: Response leftovers for debugging — the adapter strips the
            vector payload out of it (the vectors live in ``vectors``
            ONLY, never twice).
    """

    provider: str
    model: Optional[str]
    dim: int
    vectors: list[list[float]] = field(default_factory=list)
    usage: Optional[dict] = None
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def require_texts(texts, *, provider: str = "") -> list[str]:
    """Validate the batch BEFORE any provider call — the shared fatal
    gate every adapter runs first.

    Rejects (fatal ``EmbeddingError``, never a provider round-trip):
    a non-list, an empty batch, non-string entries and empty/whitespace
    entries (embedding APIs reject empty input server-side; an
    empty-string vector would silently poison positional zips anyway).
    Returns the batch as a plain list.
    """
    if not isinstance(texts, (list, tuple)):
        raise EmbeddingError(
            f"texts must be a list of strings, got {type(texts).__name__}",
            provider=provider,
        )
    if not texts:
        raise EmbeddingError("texts is empty — nothing to embed", provider=provider)
    for idx, text in enumerate(texts):
        if not isinstance(text, str):
            raise EmbeddingError(
                f"texts[{idx}] is not a string ({type(text).__name__})",
                provider=provider,
            )
        if not text.strip():
            raise EmbeddingError(
                f"texts[{idx}] is empty — empty texts cannot be embedded",
                provider=provider,
            )
    return list(texts)


# ─── Provider ABC ──────────────────────────────────────────────────────


class EmbeddingProvider(ABC):
    """Adapter for a single embedding engine.

    ``name`` is the stable id stored on the PromptLog row.

    ``embedding_model`` is the **per-registration model pin** — the
    embeddings mirror of the STT ``speech_model`` canon: a host that
    registers this adapter under a name and wants one specific model for
    that name sets the class-attr on a subclass; ``None`` = fall back to
    the provider's configured default, so unpinned registrations keep the
    settings-driven behaviour. Two registrations of the same adapter
    class can thus carry different pinned models without a settings
    change or a fork.
    """

    name: str = ""
    embedding_model: Optional[str] = None

    def default_embedding_model(self) -> Optional[str]:
        """The provider's configured model (from settings) *before* the
        pin. Providers with a settings-backed model override this; the
        base (and single-model self-hosted servers) return None."""
        return None

    def effective_model(self) -> Optional[str]:
        """The model this registration would request right now: the
        pinned ``embedding_model`` class-attr when set, else
        ``default_embedding_model()``."""
        return self.embedding_model or self.default_embedding_model()

    @abstractmethod
    def embed(
        self,
        *,
        texts: list[str],
        timeout_seconds: Optional[int] = None,
        provider_options: Optional[dict] = None,
    ) -> NormalizedEmbeddings:
        """Embed *texts* (a non-empty batch of non-empty strings).

        The returned ``vectors`` list MUST preserve input order and carry
        exactly ``len(texts)`` entries — adapters verify the count and
        fail loudly on a mismatch instead of returning a misaligned
        batch. Every adapter runs ``require_texts`` first (empty batches
        and empty texts are fatal, never sent).

        ``provider_options`` is the house free-form per-provider
        passthrough, applied AFTER the adapter's own request params —
        pin provider specifics without a core release. Unknown keys go
        to the provider as-is; the adapter must NEVER silently drop them.

        Raises ``RetryableEmbeddingError`` on transient failure (network,
        429, 5xx, timeout) and ``EmbeddingError`` on permanent failure
        (bad input, auth).
        """
        raise NotImplementedError


__all__ = [
    "EmbeddingError",
    "EmbeddingProvider",
    "NormalizedEmbeddings",
    "RetryableEmbeddingError",
    "require_texts",
]
