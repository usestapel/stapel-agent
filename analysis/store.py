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


class FencedStateStore(StateStore, Protocol):
    """A store that can refuse the write of a run that has been SUPERSEDED.

    One key, one document, and a refresh starts a second run over the same
    key while the first is still working. With a plain ``save`` the last
    write wins, which is a coin toss between the answer to the question the
    caller is asking now and the answer to the one it asked a second ago —
    and a stale run's terminal ``done`` also tells every poller the job is
    over. So a run writes only while the row still carries the fingerprint
    it started under; ``save_if_current`` is that write, and returning
    False is "somebody else owns this key now".

    Optional: :func:`runner.run` uses it when the store has it and falls
    back to load-then-save otherwise.
    """

    def save_if_current(
        self, key: str, state: AnalysisState, fingerprint: str
    ) -> bool: ...


class MemoryStateStore:
    """Process-local store. Never for production — a poller outlives it."""

    def __init__(self) -> None:
        self._rows: dict[str, dict] = {}

    def load(self, key: str) -> AnalysisState | None:
        raw = self._rows.get(key)
        return AnalysisState.from_dict(raw) if raw is not None else None

    def save(self, key: str, state: AnalysisState) -> None:
        self._rows[key] = state.as_dict()

    def save_if_current(
        self, key: str, state: AnalysisState, fingerprint: str
    ) -> bool:
        row = self._rows.get(key)
        if row is not None and str(row.get("fingerprint") or "") != fingerprint:
            return False
        self._rows[key] = state.as_dict()
        return True


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

    def save_if_current(
        self, key: str, state: AnalysisState, fingerprint: str
    ) -> bool:
        """Write only while the row still belongs to this run.

        ONE statement, so the check and the write cannot be interleaved by
        the other worker: a conditional ``UPDATE`` whose ``WHERE`` carries
        the fingerprint the caller started under. Zero rows matched means
        either a newer run has claimed the key or the row is gone — both
        are "this run's answer is no longer wanted".
        """
        from django.utils import timezone

        from ..models import AnalysisJob

        matched = AnalysisJob.objects.filter(key=key, fingerprint=fingerprint).update(
            fingerprint=state.fingerprint,
            status=state.status,
            document=state.as_dict(),
            # `.update()` is not `.save()`, so `auto_now` never fires — and a
            # document whose row says it has not moved since the job started
            # is unreadable to anything watching the queue.
            updated_at=timezone.now(),
        )
        return bool(matched)


__all__ = ["FencedStateStore", "MemoryStateStore", "ModelStateStore", "StateStore"]
