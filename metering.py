"""What one Function call cost, summed over the ledger rows it wrote.

A comm Function opens a :func:`scope`; every ``PromptLog`` row inserted
inside it (``PromptLog.save`` calls :func:`stamp` and :func:`record`) is
stamped with the caller's ``context`` and counted. The reply then carries
:meth:`Meter.summary` as ``metering`` — the same numbers the rows hold, so
the caller's figure and the ledger cannot disagree. Django-free.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from decimal import Decimal

CONTEXT_KEYS = ("recording_id", "stage", "run_id", "attempt")

CACHED = "cached"
UNPRICED = "unpriced"
PRICING_ESTIMATE = "pricing_estimate"

_SUMMED = (
    "input_tokens",
    "output_tokens",
    "thinking_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "audio_duration_ms",
    "duration_ms",
)

_ACTIVE: ContextVar[tuple] = ContextVar("stapel_agent_meters", default=())

#: The ``context`` object every ``llm.*`` request schema accepts.
CONTEXT_SCHEMA = {
    "type": "object",
    "description": "Who this call is FOR inside the caller's own pipeline "
    "(a recording, the stage, the run and the attempt). Stored on every "
    "PromptLog row the call writes, under metadata.context; recorded "
    "only, never acted on.",
    "properties": {
        "recording_id": {"type": "string", "maxLength": 128},
        "stage": {"type": "string", "maxLength": 64},
        "run_id": {"type": "string", "maxLength": 128},
        "attempt": {"type": "integer", "minimum": 0},
    },
    "additionalProperties": False,
}


def clean_context(context) -> dict | None:
    """The known keys of *context*, ``None`` values dropped."""
    if not isinstance(context, dict):
        return None
    cleaned = {k: context[k] for k in CONTEXT_KEYS if context.get(k) is not None}
    return cleaned or None


class Meter:
    """The rows (and row-less cache serves) of one Function call."""

    def __init__(self, context: dict | None = None):
        self.context = clean_context(context)
        self.entries: list[dict] = []

    def add(
        self,
        *,
        provider: str | None,
        model: str | None,
        cost_usd,
        cost_basis: str | None,
        priced_by: str | None = None,
        success: bool = True,
        fallback_used: bool = False,
        **counters,
    ) -> None:
        self.entries.append(
            {
                "provider": provider,
                "model": model,
                "cost_usd": cost_usd,
                "cost_basis": cost_basis,
                "priced_by": priced_by,
                "success": success,
                "fallback_used": fallback_used,
                **{k: counters.get(k) for k in _SUMMED},
            }
        )

    def summary(self, *, fallback_used: bool = False) -> dict | None:
        """The metering object, or ``None`` when nothing was metered.

        Cost and counters are SUMS over every entry; ``provider`` /
        ``model`` / ``priced_by`` are the last successful entry's (the one
        that produced the result), else the last entry's.
        """
        if not self.entries:
            return None
        producer = next(
            (e for e in reversed(self.entries) if e["success"]), self.entries[-1]
        )
        out = {
            "provider": producer["provider"],
            "model": producer["model"],
            "priced_by": producer["priced_by"],
            "fallback_used": bool(
                fallback_used or any(e["fallback_used"] for e in self.entries)
            ),
            "calls": len(self.entries),
        }
        costs = [e["cost_usd"] for e in self.entries if e["cost_usd"] is not None]
        out["cost_usd"] = (
            float(round(sum(Decimal(str(c)) for c in costs), 8)) if costs else None
        )
        out["cost_basis"] = _combined_basis(e["cost_basis"] for e in self.entries)
        for field in _SUMMED:
            values = [e[field] for e in self.entries if e[field] is not None]
            out[field] = sum(int(v) for v in values) if values else None
        return out


def _combined_basis(bases) -> str | None:
    """One basis for a sum: all-cached is ``cached``; any unpriced part
    makes the sum ``unpriced`` (a lower bound); cached parts are true
    zeros and do not change a priced sum's basis."""
    seen = {b for b in bases if b}
    if not seen:
        return None
    if seen == {CACHED}:
        return CACHED
    seen.discard(CACHED)
    if UNPRICED in seen:
        return UNPRICED
    if len(seen) == 1:
        return seen.pop()
    return PRICING_ESTIMATE


@contextmanager
def scope(context=None):
    """Meter every ledger row written inside the block."""
    meter = Meter(context)
    token = _ACTIVE.set(_ACTIVE.get() + (meter,))
    try:
        yield meter
    finally:
        _ACTIVE.reset(token)


def active_context() -> dict | None:
    """The innermost scope's context, if any."""
    for meter in reversed(_ACTIVE.get()):
        if meter.context:
            return meter.context
    return None


def stamp(row) -> None:
    """Put the active context on a row about to be inserted."""
    context = active_context()
    if context and not (row.metadata or {}).get("context"):
        row.metadata = {**(row.metadata or {}), "context": context}


def record(row) -> None:
    """Count an inserted ledger row in every active scope."""
    meters = _ACTIVE.get()
    if not meters:
        return
    meta = row.metadata or {}
    basis = row.cost_basis or (CACHED if meta.get("cached") else None)
    cost = row.cost_usd
    if cost is None and basis == CACHED:
        cost = 0
    entry = dict(
        provider=meta.get("provider") or row.model,
        model=meta.get("model") or row.model,
        cost_usd=cost,
        cost_basis=basis,
        priced_by=meta.get("priced_by"),
        success=row.status == "success",
        fallback_used=bool(meta.get("fallback_used")),
        **{field: getattr(row, field, None) for field in _SUMMED},
    )
    for meter in meters:
        meter.add(**entry)


def record_cached(*, provider: str | None, model: str | None) -> None:
    """Count an answer served from the prompt cache, which writes no row."""
    for meter in _ACTIVE.get():
        meter.add(
            provider=provider,
            model=model,
            cost_usd=0,
            cost_basis=CACHED,
            input_tokens=0,
            output_tokens=0,
        )
