"""Where a job's state document lives between two HTTP requests.

The document is written after every batch, so the store is on the hot path
of a running stage and must be cheap and total: a write that fails must not
lose the work already done, and a read must never invent a document that
was never written.

Two implementations ship:

* :class:`ModelStateStore` — a row per job, the default. Durable across a
  worker restart, which is the property a poller depends on.
* :class:`MemoryStateStore` — for tests and for a host that keeps the
  document somewhere else entirely (another service's draft metadata).

A host with its own home for the document implements :class:`StateStore`
and hands it to the runner; nothing else in the module knows the
difference.
"""
from __future__ import annotations

from typing import Protocol

from .state import AnalysisState


class StateStore(Protocol):
    """Load and save one job document by key."""

    def load(self, key: str) -> AnalysisState | None: ...

    def save(self, key: str, state: AnalysisState) -> None: ...


class MemoryStateStore:
    """Process-local store. Never for production — a poller outlives it."""

    def __init__(self) -> None:
        self._rows: dict[str, dict] = {}

    def load(self, key: str) -> AnalysisState | None:
        raw = self._rows.get(key)
        return AnalysisState.from_dict(raw) if raw is not None else None

    def save(self, key: str, state: AnalysisState) -> None:
        self._rows[key] = state.as_dict()


class ModelStateStore:
    """The default: one ``AnalysisJob`` row per key.

    ``save`` is an upsert rather than a read-modify-write, so two workers
    writing batches of the same job cannot lose one another's row — the
    LAST write of a document wins, and every document is a full snapshot,
    never a delta.
    """

    def load(self, key: str) -> AnalysisState | None:
        from ..models import AnalysisJob

        row = AnalysisJob.objects.filter(key=key).first()
        return AnalysisState.from_dict(row.document) if row is not None else None

    def save(self, key: str, state: AnalysisState) -> None:
        from ..models import AnalysisJob

        document = state.as_dict()
        AnalysisJob.objects.update_or_create(
            key=key,
            defaults={
                "fingerprint": state.fingerprint,
                "status": state.status,
                "document": document,
            },
        )


__all__ = ["MemoryStateStore", "ModelStateStore", "StateStore"]
