"""Rerank provider seam — normalized scored indexes, ABC, errors.

The seam is JOIN-SHAPED on purpose: ``rerank`` takes one query plus a
list of candidate documents and returns ``(index, score)`` pairs sorted
by score descending, where ``index`` is the position in the INPUT
documents list — the caller joins scores back onto its own candidates
positionally. Documents never round-trip in the response (they are the
caller's data; echoing them back would only bloat the wire and the
ledger's blast radius). Retrieval, chunking and final cutoff policies
are client-app know-how and stay out of this core.

Errors join the house hierarchy: ``RerankError(ProviderError)`` (fatal —
bad input/auth) and ``RetryableRerankError`` (429/5xx/timeouts), same
taxonomy as STT/images/diarization/embeddings.

This module is deliberately Django-free.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Optional

from ..providers.base import ProviderError


class RerankError(ProviderError):
    """Permanent rerank failure (empty/invalid input, auth, ...)."""

    def __init__(self, message: str, *, provider: str = "", status_code: int | None = None):
        super().__init__(message)
        self.provider = provider
        self.status_code = status_code


class RetryableRerankError(RerankError):
    """Transient rerank failure (network, 429, 5xx, timeout)."""


# ─── Normalized rerank schema ──────────────────────────────────────────


@dataclass
class RerankResult:
    """One scored candidate.

    Attributes:
        index: Position of the document in the INPUT documents list —
            the caller's join key. Never an identifier the provider
            invented.
        score: Provider relevance score (higher = more relevant). Scores
            are provider-scale — comparable within one response, not
            across providers.
    """

    index: int
    score: float


@dataclass
class NormalizedRerank:
    """Output every rerank provider must return.

    Attributes:
        provider: Adapter id (e.g. ``deepinfra-rerank``). Recorded on
            the PromptLog row for observability.
        model: Model that produced the scores — the provider's response
            echo when it ships one (real attribution), else the requested
            model; None when the server names no model at all.
        results: ``(index, score)`` pairs **sorted by score descending**
            (ties keep input order). ``index`` points into the input
            documents list; the documents themselves never round-trip.
        usage: Provider-reported usage (e.g. ``{"input_tokens": ...}``);
            None when the server reports none.
        raw: Response leftovers for debugging — the adapter strips the
            score payload out of it (the scores live in ``results``
            ONLY, never twice).
    """

    provider: str
    model: Optional[str]
    results: list[RerankResult] = field(default_factory=list)
    usage: Optional[dict] = None
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def require_rerank_inputs(query, documents, *, top_n=None, provider: str = ""):
    """Validate the request BEFORE any provider call — the shared fatal
    gate every adapter runs first.

    Rejects (fatal ``RerankError``, never a provider round-trip): a
    non-string or empty/whitespace query; a non-list, empty or
    non-string/empty-entry documents batch (an empty document cannot be
    scored meaningfully and would silently poison positional joins); a
    non-positive ``top_n``. Returns ``(query, documents-as-list)``.
    """
    if not isinstance(query, str):
        raise RerankError(
            f"query must be a string, got {type(query).__name__}",
            provider=provider,
        )
    if not query.strip():
        raise RerankError("query is empty — nothing to rank against", provider=provider)
    if not isinstance(documents, (list, tuple)):
        raise RerankError(
            f"documents must be a list of strings, got {type(documents).__name__}",
            provider=provider,
        )
    if not documents:
        raise RerankError("documents is empty — nothing to rerank", provider=provider)
    for idx, doc in enumerate(documents):
        if not isinstance(doc, str):
            raise RerankError(
                f"documents[{idx}] is not a string ({type(doc).__name__})",
                provider=provider,
            )
        if not doc.strip():
            raise RerankError(
                f"documents[{idx}] is empty — empty documents cannot be ranked",
                provider=provider,
            )
    if top_n is not None and int(top_n) < 1:
        raise RerankError(
            f"top_n must be a positive integer, got {top_n!r}", provider=provider
        )
    return query, list(documents)


def rank_results(
    results: list[RerankResult],
    *,
    n_documents: int,
    top_n: Optional[int] = None,
    provider: str = "",
) -> list[RerankResult]:
    """Validate provider-returned results, sort by score descending and
    apply ``top_n`` — the shared normalization every adapter runs last.

    Fatal (loud, never a misaligned join): more results than input
    documents, an index outside ``[0, n_documents)``, or a duplicate
    index (two scores claiming the same document would make the join
    ambiguous). Sorting is stable, so ties keep the providers' order.
    ``top_n`` truncation happens HERE, after the sort, uniformly for
    every adapter — providers without a native top-n parameter behave
    identically to those with one.
    """
    if len(results) > n_documents:
        raise RerankError(
            f"rerank result count mismatch: sent {n_documents} documents, got "
            f"{len(results)} results — refusing a misaligned join",
            provider=provider,
        )
    seen: set[int] = set()
    for res in results:
        if not 0 <= res.index < n_documents:
            raise RerankError(
                f"rerank result index {res.index} out of range for "
                f"{n_documents} documents — refusing a misaligned join",
                provider=provider,
            )
        if res.index in seen:
            raise RerankError(
                f"rerank result index {res.index} appears twice — refusing an "
                "ambiguous join",
                provider=provider,
            )
        seen.add(res.index)
    ranked = sorted(results, key=lambda res: -res.score)
    if top_n is not None:
        ranked = ranked[: int(top_n)]
    return ranked


# ─── Provider ABC ──────────────────────────────────────────────────────


class RerankProvider(ABC):
    """Adapter for a single rerank engine.

    ``name`` is the stable id stored on the PromptLog row.

    ``rerank_model`` is the **per-registration model pin** — the rerank
    mirror of the STT ``speech_model`` / embeddings ``embedding_model``
    canon: a host that registers this adapter under a name and wants one
    specific model for that name sets the class-attr on a subclass;
    ``None`` = fall back to the provider's configured default, so
    unpinned registrations keep the settings-driven behaviour. Two
    registrations of the same adapter class can thus carry different
    pinned models without a settings change or a fork.
    """

    name: str = ""
    rerank_model: Optional[str] = None

    def default_rerank_model(self) -> Optional[str]:
        """The provider's configured model (from settings) *before* the
        pin. Providers with a settings-backed model override this; the
        base (and single-model self-hosted servers) return None."""
        return None

    def effective_model(self) -> Optional[str]:
        """The model this registration would request right now: the
        pinned ``rerank_model`` class-attr when set, else
        ``default_rerank_model()``."""
        return self.rerank_model or self.default_rerank_model()

    @abstractmethod
    def rerank(
        self,
        *,
        query: str,
        documents: list[str],
        top_n: Optional[int] = None,
        timeout_seconds: Optional[int] = None,
        provider_options: Optional[dict] = None,
    ) -> NormalizedRerank:
        """Score *documents* against *query*.

        The returned ``results`` MUST be sorted by score descending with
        each ``index`` pointing into the input documents list — adapters
        run ``rank_results`` and fail loudly on out-of-range/duplicate
        indexes or a count above the input size instead of returning a
        misaligned join. Every adapter runs ``require_rerank_inputs``
        first (empty query/documents are fatal, never sent). ``top_n``
        truncates AFTER the sort; None returns every document scored.

        ``provider_options`` is the house free-form per-provider
        passthrough, applied AFTER the adapter's own request params —
        pin provider specifics without a core release. Unknown keys go
        to the provider as-is; the adapter must NEVER silently drop them.

        Raises ``RetryableRerankError`` on transient failure (network,
        429, 5xx, timeout) and ``RerankError`` on permanent failure
        (bad input, auth).
        """
        raise NotImplementedError


__all__ = [
    "NormalizedRerank",
    "RerankError",
    "RerankProvider",
    "RerankResult",
    "RetryableRerankError",
    "rank_results",
    "require_rerank_inputs",
]
