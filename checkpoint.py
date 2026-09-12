"""A priced provider call is a checkpoint, not a step a retry repeats.

The rule this module implements: *a retry must be bound to each
significant stage; re-running the whole cycle after a paid step with
nothing stored is unacceptable.* It was written against a measured
incident — one 148-minute recording transcribed at the provider SIX
times (two tasks, three attempts each) because the step that failed was
the REPLY, downstream of the money, and every retry re-entered the
handler from the top.

So the result of the provider call is persisted under a key derived from
that call's inputs the instant the provider answers, and BEFORE anything
downstream runs (post-processing, the ledger write, a reply that has to
cross a broker, a handoff PUT). A retry from any layer — the task
runner, the host's stage, a redelivered message, a person clicking again
— recomputes the same key, finds the stored result, and resumes AFTER
the spend.

**This is not** :mod:`stapel_agent.cache`. That module is a PRODUCT
decision ("an identical translation may be served from an earlier one"),
off by default, per source, keyed on prose. This one is a SAFETY
decision: the money is already spent and no layer above may spend it
again while the work that follows is still being retried. Hence the two
differences that matter — the key is the provider call's whole input,
never an approximation of it, and the window is a retry ladder's
lifetime (``CHECKPOINT_TTL_SECONDS``, 15 minutes) rather than a product
cache's. Transcription is the documented exception: it is the most
expensive call in the package and its input is a fixed recording, so it
keeps its own week-long window (``STT_RESULT_TTL_SECONDS``) — a
deliberate re-run of a pipeline costs nothing, which is what makes a
re-run a safe thing to offer.

Scope is part of every key (AGENT-02): two tenants sending identical
input are not entitled to each other's answers, even when the answer is
"free" to serve.

A checkpoint hit is METERED, not hidden: the surface still writes its
PromptLog row, with ``cost_usd=0`` and ``cost_basis="cached"``. Summing
``cost_usd`` gives what was actually paid; counting rows still gives what
was actually asked for; and ``cost_basis="cached"`` is the query that
answers "how much did this table save us".
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)

#: Surfaces whose window is ``STT_RESULT_TTL_SECONDS`` rather than the
#: short ``CHECKPOINT_TTL_SECONDS`` one — the audio pair, where the input
#: is a stored file and the call is the most expensive one we make.
AUDIO_SURFACES = ("transcribe", "diarize")


@dataclass(frozen=True)
class CheckpointHit:
    """A stored provider result, with enough context to meter the hit."""

    key: str
    surface: str
    provider: str
    value: dict
    created_at: datetime
    age_seconds: int


def enabled(surface: str = "") -> bool:
    """Whether checkpoints are consulted and written (for *surface*).

    Two gates, because they answer different questions.
    ``CHECKPOINT_ENABLED`` is the deployment's: off means every retry
    above pays again, and the host owns that. ``CHECKPOINT_SURFACES``
    is per-call-kind, and exists for one honest trade: an embedding
    batch's stored value is megabytes of floats against the cheapest
    priced call in the package, and a host with a small database may
    reasonably keep the audio pair and drop that one. A surface is never
    chosen by the SIZE of a particular answer — that would be a
    threshold nobody can predict; it is chosen by name, in settings,
    where a reviewer sees it.
    """
    from .conf import agent_settings, checkpoint_enabled

    if not checkpoint_enabled():
        return False
    if not surface:
        return True
    return surface in set(agent_settings.CHECKPOINT_SURFACES or ())


def ttl_seconds(surface: str) -> int:
    """The window for *surface* — see the module docstring for the split."""
    from .conf import agent_settings

    if surface in AUDIO_SURFACES:
        return int(agent_settings.STT_RESULT_TTL_SECONDS)
    return int(agent_settings.CHECKPOINT_TTL_SECONDS)


def _canonical(value):
    """A stable string for any JSON-able key part.

    ``sort_keys`` because a dict's iteration order must not change a key;
    ``default=str`` because a key part may be an enum or a Decimal and a
    key that raises is worse than a key that is slightly coarse.
    """
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False
    )


def call_key(
    surface: str,
    *,
    parts: dict,
    user_id: str | None = None,
    workspace_id: str | None = None,
) -> str:
    """sha256 over (surface, scope, *parts*) — the identity of one call.

    *parts* must name EVERY input that changes what a correct answer is.
    A part left out is an answer served for a question nobody asked: for
    transcription that is the audio's content hash, the provider, the
    model, the language, the diarization flag and the biasing terms.
    """
    payload = _canonical(
        {
            "surface": surface,
            "user_id": str(user_id) if user_id is not None else None,
            "workspace_id": str(workspace_id) if workspace_id is not None else None,
            "parts": parts,
        }
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load(key: str, *, surface: str) -> CheckpointHit | None:
    """The stored result for *key*, or None (miss, expired, or disabled).

    Never raises: a checkpoint store that is unreadable must degrade to
    "call the provider", which costs money but returns an answer.
    """
    if not enabled(surface) or not key:
        return None

    from datetime import timedelta

    from django.utils import timezone

    from .models import ProviderCheckpoint

    ttl = ttl_seconds(surface)
    try:
        row = ProviderCheckpoint.objects.filter(
            pk=key, created_at__gte=timezone.now() - timedelta(seconds=ttl)
        ).first()
    except Exception:  # pragma: no cover - storage outage
        logger.warning(
            "stapel-agent: checkpoint lookup failed for %s — calling the "
            "provider (this retry pays again)", surface, exc_info=True,
        )
        return None
    if row is None:
        return None
    age = int((timezone.now() - row.created_at).total_seconds())
    return CheckpointHit(
        key=key,
        surface=row.surface,
        provider=row.provider,
        value=row.value or {},
        created_at=row.created_at,
        age_seconds=age,
    )


def store(
    key: str,
    *,
    surface: str,
    provider: str,
    value: dict,
    user_id: str | None = None,
    workspace_id: str | None = None,
) -> bool:
    """Persist *value* under *key*. Returns whether it was stored.

    Called the instant the provider answers and before anything
    downstream — that ordering is the whole mechanism, not an
    optimisation. Never raises for the same reason :func:`load` does not:
    a failure to store must not throw away an answer that has been paid
    for.
    """
    if not enabled(surface) or not key:
        return False

    from .models import ProviderCheckpoint

    try:
        ProviderCheckpoint.objects.update_or_create(
            pk=key,
            defaults={
                "surface": surface,
                "provider": provider or "",
                "value": value,
                "user_id": str(user_id) if user_id is not None else None,
                "workspace_id": (
                    str(workspace_id) if workspace_id is not None else None
                ),
            },
        )
        return True
    except Exception:  # pragma: no cover - storage outage
        logger.warning(
            "stapel-agent: could not store the %s checkpoint — a retry of "
            "this call will pay the provider again", surface, exc_info=True,
        )
        return False


def purge_expired(*, dry_run: bool = False) -> int:
    """Delete checkpoints past their surface's window. Returns the count.

    A checkpoint holds customer content (a transcript, a completion), so
    it is DELETED rather than scrubbed: unlike a ledger row it carries
    nothing worth keeping once it can no longer be resumed from.
    """
    from datetime import timedelta

    from django.db.models import Q
    from django.utils import timezone

    from .models import ProviderCheckpoint

    now = timezone.now()
    audio_cutoff = now - timedelta(seconds=ttl_seconds("transcribe"))
    other_cutoff = now - timedelta(seconds=ttl_seconds("complete"))
    qs = ProviderCheckpoint.objects.filter(
        Q(surface__in=AUDIO_SURFACES, created_at__lt=audio_cutoff)
        | Q(created_at__lt=other_cutoff) & ~Q(surface__in=AUDIO_SURFACES)
    )
    if dry_run:
        return qs.count()
    return qs.delete()[0]


__all__ = [
    "AUDIO_SURFACES",
    "CheckpointHit",
    "call_key",
    "enabled",
    "load",
    "purge_expired",
    "store",
    "ttl_seconds",
]
