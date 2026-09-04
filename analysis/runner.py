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
* **A superseded run writes nothing.** One key holds one document, and a
  refresh starts a second run over it while the first is still working.
  Every write a run makes is fenced on the fingerprint it started under, so
  the run that is no longer being asked about cannot overwrite the one that
  is — nor announce ``done`` on its behalf.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Iterator, Sequence

from .state import (
    DONE,
    ERROR_EMPTY_INPUT,
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


class SupersededRun(RuntimeError):
    """This run's key belongs to a newer question; its writes are refused.

    Raised by the fenced write and handled by :func:`run`, which stops the
    run where it stands. Never surfaced to a caller: a superseded run is
    not a failure of the job, it is the job having moved on.
    """


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
    #: Asked before the stage runs: is there anything here for it to do?
    #: A stage that answers "no" is ``skipped`` — which is a different
    #: statement from ``done`` with an empty result, and the difference is
    #: the whole point: a screening stage that ran over nothing and said
    #: "not allowed, the content is empty" has published a verdict about a
    #: question nobody asked. The predicate must not raise; one that does
    #: is logged and treated as "do not skip".
    skip_when: Callable[["JobContext"], bool] | None = None


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
    if fingerprint.is_empty:
        # Nothing was read: no photos, no words. Every stage would run, cost
        # a provider call apiece and answer about a listing that is not
        # there — and the screening stage would answer "not allowed, this is
        # empty", which is a verdict on OUR failure to read the draft. The
        # job says so instead, once, in the one place a caller looks.
        state = _empty_input_state(fingerprint, stages, seller_filled)
        store.save(key, state)
        return state, False

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


def _empty_input_state(
    fingerprint: Fingerprint, stages: Sequence[Stage], seller_filled: Iterable[str]
) -> AnalysisState:
    """A job that was asked over nothing: failed, with the reason named."""
    state = AnalysisState.fresh(
        fingerprint, [s.name for s in stages], seller_filled=seller_filled
    )
    state.status = FAILED
    state.error = ERROR_EMPTY_INPUT
    for stage in stages:
        state.set_stage(stage.name, StageState(status=SKIPPED))
    return state


def run(
    *,
    store: StateStore,
    key: str,
    stages: Sequence[Stage],
    inputs: dict | None = None,
    fingerprint: str | None = None,
) -> AnalysisState:
    """Run every stage of the job that is not already answered.

    ``fingerprint`` is the question this run was started for — the value a
    host's ``execute()`` was handed by :func:`start`. Pass it and the run is
    fenced: it stops the moment the key belongs to a newer question, and a
    run already superseded before the worker picked it up costs nothing at
    all. Omit it and the fence is taken from the document as loaded, which
    still keeps a run from overwriting the one that displaced it.
    """
    state = store.load(key)
    if state is None:  # pragma: no cover - start() commits before this
        raise LookupError(key)
    fence = fingerprint if fingerprint is not None else state.fingerprint
    if state.fingerprint != fence:
        # A newer start replaced the document between the enqueue and the
        # worker. Its own run is answering; this one has nothing to add.
        logger.info("analysis: run of %s was superseded before it started", key)
        return state
    ctx = JobContext(state=state, inputs=dict(inputs or {}))

    try:
        state.status = RUNNING
        _save(store, key, state, fence)

        failed_blocking = False
        for stage in stages:
            if state.stage(stage.name).status == DONE:
                continue  # carried over by a refresh
            if failed_blocking or _skips(stage, ctx):
                state.set_stage(stage.name, StageState(status=SKIPPED))
                _save(store, key, state, fence)
                continue
            _run_stage(store, key, state, ctx, stage, fence)
            if state.stage(stage.name).status == FAILED and stage.blocking:
                failed_blocking = True

        state.status = FAILED if failed_blocking else DONE
        _save(store, key, state, fence)
    except SupersededRun:
        # Not an error: the answer this run was producing is not the one
        # being waited for any more. Everything it would have written —
        # its batches, and its terminal status — is dropped, so the run
        # that owns the key now is the only one a poller ever sees.
        logger.info("analysis: run of %s superseded; its writes are dropped", key)
        return store.load(key) or state
    return state


def _save(store: StateStore, key: str, state: AnalysisState, fence: str) -> None:
    """One write of the document, refused when this run no longer owns the key.

    ``save_if_current`` is the atomic form and the one a durable store
    should offer. Without it the fence is a read then a write, which is
    narrower than nothing: it closes the long window (a whole stage) and
    leaves only the instant between the two statements.
    """
    saver = getattr(store, "save_if_current", None)
    if saver is None:
        current = store.load(key)
        if current is not None and current.fingerprint != fence:
            raise SupersededRun(key)
        store.save(key, state)
        return
    if not saver(key, state, fence):
        raise SupersededRun(key)


def _skips(stage: Stage, ctx: "JobContext") -> bool:
    """Does this stage have nothing to do? Never raises."""
    if stage.skip_when is None:
        return False
    try:
        return bool(stage.skip_when(ctx))
    except Exception as exc:  # noqa: BLE001 - a broken predicate must not skip
        logger.warning(
            "analysis: skip_when of stage %s raised %s: %s - running it",
            stage.name,
            type(exc).__name__,
            exc,
        )
        return False


def _run_stage(store, key, state, ctx, stage: Stage, fence: str) -> None:
    state.set_stage(stage.name, StageState(status=RUNNING))
    _save(store, key, state, fence)
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
                _save(store, key, state, fence)
            state.set_stage(
                stage.name,
                StageState(status=DONE, result=last.result, progress=last.progress),
            )
        else:
            state.set_stage(stage.name, StageState(status=DONE, result=produced))
    except SupersededRun:
        # The fence, not a stage fault: it must not be recorded as one, and
        # it must stop the run rather than the stage.
        raise
    except Exception as exc:  # noqa: BLE001 - a stage failure is data, not a crash
        logger.warning(
            "analysis: stage %s failed: %s: %s", stage.name, type(exc).__name__, exc
        )
        state.set_stage(
            stage.name, StageState(status=FAILED, error=f"{type(exc).__name__}: {exc}")
        )
    _save(store, key, state, fence)


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
    "ERROR_EMPTY_INPUT",
    "FAILED",
    "ON_PHOTOS",
    "ON_TEXT",
    "QUEUED",
    "RUNNING",
    "JobContext",
    "Stage",
    "StageBatch",
    "SupersededRun",
    "run",
    "start",
    "wait_for_stage",
]
