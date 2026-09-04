"""The state document of a staged analysis job, and its fingerprint.

One job, one document, three properties the rest of the module exists to
keep true:

* **Every stage is visible on its own.** A caller reads the fast stage's
  result while the slow one is still running, instead of waiting for the
  whole chain — which is the difference between a form that fills in front
  of a person and a spinner.
* **A stage writes as it goes.** ``progress`` and a partial ``result`` are
  part of the contract, so a long stage is observable rather than opaque.
* **The document is data, not an object graph.** It round-trips through
  JSON unchanged, so it can live in a row, a cache entry or another
  service's metadata without a second representation.

The fingerprint is split in two on purpose. Photos and text change for
different reasons and cost different amounts to re-analyse: new photos
invalidate everything, edited text invalidates only what was derived from
it. A single opaque hash cannot express that, so :class:`Fingerprint`
carries both halves and ``value`` is what identifies the job.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

#: Job-level states. ``queued`` is before the first stage starts running.
QUEUED = "queued"
RUNNING = "running"
DONE = "done"
FAILED = "failed"

#: Stage-level states. A stage that was deliberately not run is ``skipped``
#: — distinct from ``done`` (it produced nothing) and from ``queued`` (it is
#: not waiting for its turn).
SKIPPED = "skipped"

_TERMINAL = frozenset({DONE, FAILED, SKIPPED})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Fingerprint:
    """What a job is *of*: the photo set and the text, hashed apart.

    ``photos`` is over the image BYTES, not their count or their names —
    the same person re-submitting the same pictures is exactly the case a
    count-based key would miss. ``text`` is over the seller's own words.
    """

    photos: str
    text: str

    @property
    def value(self) -> str:
        return f"{self.photos}:{self.text}"

    @classmethod
    def of(cls, *, photos: Iterable[bytes] = (), text: str = "") -> "Fingerprint":
        photo_digest = hashlib.sha256()
        for blob in photos:
            photo_digest.update(b"\0")
            photo_digest.update(blob or b"")
        return cls(
            photos=photo_digest.hexdigest()[:32],
            text=hashlib.sha256((text or "").strip().encode()).hexdigest()[:32],
        )

    @classmethod
    def parse(cls, value: str | None) -> "Fingerprint | None":
        if not value or ":" not in str(value):
            return None
        photos, _, text = str(value).partition(":")
        return cls(photos=photos, text=text)


@dataclass
class StageState:
    """One stage of the job, as the caller reads it."""

    status: str = QUEUED
    result: Any = None
    error: str | None = None
    progress: dict | None = None

    def as_dict(self) -> dict:
        out: dict = {
            "status": self.status,
            "result": self.result,
            "error": self.error,
        }
        if self.progress is not None:
            out["progress"] = dict(self.progress)
        return out

    @classmethod
    def from_dict(cls, raw: Mapping | None) -> "StageState":
        raw = raw or {}
        progress = raw.get("progress")
        return cls(
            status=str(raw.get("status") or QUEUED),
            result=raw.get("result"),
            error=raw.get("error"),
            progress=dict(progress) if isinstance(progress, Mapping) else None,
        )


@dataclass
class AnalysisState:
    """The whole document. Serialises to exactly what the endpoint returns."""

    fingerprint: str = ""
    status: str = QUEUED
    stages: dict = field(default_factory=dict)
    updated_at: str = field(default_factory=_now)
    #: Slugs the person filled themselves. Carried on the document rather
    #: than in the caller's memory: a refresh that forgets them re-asks
    #: fields somebody already answered, which is the one thing an
    #: assistant must never do.
    seller_filled: list = field(default_factory=list)

    # ── stage access ────────────────────────────────────────────────────
    def stage(self, name: str) -> StageState:
        return StageState.from_dict(self.stages.get(name))

    def set_stage(self, name: str, state: StageState) -> None:
        self.stages[name] = state.as_dict()
        self.updated_at = _now()

    # ── serialisation ───────────────────────────────────────────────────
    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "fingerprint": self.fingerprint,
            "stages": {name: dict(raw) for name, raw in self.stages.items()},
            "seller_filled": list(self.seller_filled),
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, raw: Mapping | None) -> "AnalysisState":
        raw = raw or {}
        stages = raw.get("stages")
        return cls(
            fingerprint=str(raw.get("fingerprint") or ""),
            status=str(raw.get("status") or QUEUED),
            stages=dict(stages) if isinstance(stages, Mapping) else {},
            updated_at=str(raw.get("updated_at") or _now()),
            seller_filled=list(raw.get("seller_filled") or []),
        )

    @classmethod
    def fresh(
        cls,
        fingerprint: Fingerprint,
        stage_names: Sequence[str],
        *,
        seller_filled: Iterable[str] = (),
    ) -> "AnalysisState":
        state = cls(fingerprint=fingerprint.value, seller_filled=list(seller_filled))
        for name in stage_names:
            state.stages[name] = StageState().as_dict()
        return state

    @property
    def settled(self) -> bool:
        return self.status in (DONE, FAILED)

    def stage_settled(self, name: str) -> bool:
        return self.stage(name).status in _TERMINAL


__all__ = [
    "DONE",
    "FAILED",
    "QUEUED",
    "RUNNING",
    "SKIPPED",
    "AnalysisState",
    "Fingerprint",
    "StageState",
]
