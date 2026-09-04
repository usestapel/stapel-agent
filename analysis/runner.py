"""The stage runner: start or refresh a job, then run its stages in order.

What the runner guarantees, so no stage has to:

* **The document is written after every batch.** A stage yields; the runner
  stores. A caller polling the job therefore sees ``1/17`` and the first
  block's answers while the second block is still being asked — the
  property that makes a slow stage usable instead of merely bounded.
* **A stage's result is visible the moment that stage ends**, not when the
  job does. The fast stage is a whole answer by itself.
* **A refresh re-runs only what the change invalidated.** Each stage
  declares which halves of the fingerprint it depends on; a stage no
  longer invalidated keeps its result and is not paid for twice.
* **A non-blocking stage cannot fail the job.** Screening runs last and
  decides nothing here; its failure is recorded on its own stage.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Iterator, Sequence

from .state import (
    DONE,
    FAILED,
    QUEUED,
    RUNNING,
    SKIPPED,
    AnalysisState,
    Fingerprint,
    StageState,
)
from .store import StateStore

logger = logging.getLogger(__name__)

#: The two halves of a fingerprint a stage can depend on.
ON_PHOTOS = "photos"
ON_TEXT = "text"


@dataclass
class StageBatch:
    """One increment of a stage's answer, written the moment it is yielded."""

    result: Any
    done: int = 1
    total: int = 1


@dataclass
class Stage:
    """One stage of the pipeline.

    ``run`` receives the :class:`JobContext` and either returns a result or
    yields :class:`StageBatch` values. Yielding is what makes the stage
    observable; returning is the short form for a stage with one step.
    """

    name: str
    run: Callable[["JobContext"], Any]
    depends_on: frozenset = field(default_factory=lambda: frozenset({ON_PHOTOS, ON_TEXT}))
    blocking: bool = True


@dataclass
class JobContext:
    """What a stage is given: the inputs, and what earlier stages produced."""

    state: AnalysisState
    inputs: dict = field(default_factory=dict)

    def result_of(self, stage_name: str) -> Any:
        return self.state.stage(stage_name).result

    @property
    def seller_filled(self) -> set:
        return {str(s) for s in self.state.seller_filled}


def start(
    *,
    store: StateStore,
    key: str,
    fingerprint: Fingerprint,
    stages: Sequence[Stage],
    seller_filled: Iterable[str] = (),
) -> tuple[AnalysisState, bool]:
    """Start the job, or hand back the one already answering this question.

    Returns ``(state, started)``. ``started`` is False when an identical
    request is already in flight or already answered — the same photos and
    the same text are the same question, and asking a model twice for it
    buys nothing but an invoice.
    """
    previous = store.load(key)
    if (
        previous is not None
        and previous.fingerprint == fingerprint.value
        and previous.status != FAILED
    ):
        return previous, False

    changed = _changed_halves(previous, fingerprint)
    state = AnalysisState.fresh(
        fingerprint, [s.name for s in stages], seller_filled=seller_filled
    )
    if previous is not None:
        for stage in stages:
            if changed & stage.depends_on:
                continue
            carried = previous.stage(stage.name)
            if carried.status == DONE:
                # Nothing this stage reads has changed. Its answer stands.
                state.set_stage(stage.name, carried)
    store.save(key, state)
    return state, True


def _changed_halves(previous: AnalysisState | None, fingerprint: Fingerprint) -> set:
    if previous is None:
        return {ON_PHOTOS, ON_TEXT}
    before = Fingerprint.parse(previous.fingerprint)
    if before is None:
        return {ON_PHOTOS, ON_TEXT}
    changed = set()
    if before.photos != fingerprint.photos:
        changed.add(ON_PHOTOS)
    if before.text != fingerprint.text:
        changed.add(ON_TEXT)
    return changed


def run(
    *,
    store: StateStore,
    key: str,
    stages: Sequence[Stage],
    inputs: dict | None = None,
) -> AnalysisState:
    """Run every stage of the job that is not already answered."""
    state = store.load(key)
    if state is None:  # pragma: no cover - start() commits before this
        raise LookupError(key)
    state.status = RUNNING
    store.save(key, state)
    ctx = JobContext(state=state, inputs=dict(inputs or {}))

    failed_blocking = False
    for stage in stages:
        if state.stage(stage.name).status == DONE:
            continue  # carried over by a refresh
        if failed_blocking:
            state.set_stage(stage.name, StageState(status=SKIPPED))
            store.save(key, state)
            continue
        _run_stage(store, key, state, ctx, stage)
        if state.stage(stage.name).status == FAILED and stage.blocking:
            failed_blocking = True

    state.status = FAILED if failed_blocking else DONE
    store.save(key, state)
    return state


def _run_stage(store, key, state, ctx, stage: Stage) -> None:
    state.set_stage(stage.name, StageState(status=RUNNING))
    store.save(key, state)
    try:
        produced = stage.run(ctx)
        if isinstance(produced, Iterator):
            last = StageState(status=RUNNING)
            for batch in produced:
                last = StageState(
                    status=RUNNING,
                    result=batch.result,
                    progress={"done": batch.done, "total": batch.total},
                )
                state.set_stage(stage.name, last)
                store.save(key, state)
            state.set_stage(
                stage.name,
                StageState(status=DONE, result=last.result, progress=last.progress),
            )
        else:
            state.set_stage(stage.name, StageState(status=DONE, result=produced))
    except Exception as exc:  # noqa: BLE001 - a stage failure is data, not a crash
        logger.warning(
            "analysis: stage %s failed: %s: %s", stage.name, type(exc).__name__, exc
        )
        state.set_stage(
            stage.name, StageState(status=FAILED, error=f"{type(exc).__name__}: {exc}")
        )
    store.save(key, state)


def wait_for_stage(
    store: StateStore,
    key: str,
    stage_name: str,
    *,
    budget_seconds: float,
    poll_seconds: float = 0.1,
) -> AnalysisState | None:
    """Wait for ONE stage to settle. Giving up waiting never stops the work.

    Returns the state once the stage is settled (or the job is), else None
    when the budget runs out — the caller answers with whatever the
    document holds and the job keeps running.
    """
    deadline = time.monotonic() + max(0.0, budget_seconds)
    while True:
        state = store.load(key)
        if state is not None and (state.stage_settled(stage_name) or state.settled):
            return state
        if time.monotonic() >= deadline:
            return None
        time.sleep(poll_seconds)


__all__ = [
    "DONE",
    "FAILED",
    "ON_PHOTOS",
    "ON_TEXT",
    "QUEUED",
    "RUNNING",
    "JobContext",
    "Stage",
    "StageBatch",
    "run",
    "start",
    "wait_for_stage",
]
