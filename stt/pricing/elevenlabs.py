"""ElevenLabs Scribe STT pricing (estimate — the API returns no cost field).

Source: ARCHITECTURE.md v1.1 §11.2, "ElevenLabs verified 2026-07-01: $0.22/hr"
(elevenlabs.io/pricing/api). ElevenLabs bills speech-to-text per hour of audio;
the exact per-hour rate is plan-dependent, so this is an ESTIMATE recorded for
cost tracking, not a billed amount (the STT response carries no cost field).
Update the constant if the Scribe v2 rate changes.

Version: 1.1 · Date: 09 Jul 2026 (P65: optional ``model`` kwarg — the three
provider pricing modules now share one call shape,
``estimate_cost(duration_ms, *, model=...) -> float | None``, so the runner can
price a run by its catalog ``model_id`` without provider branching)
"""

from __future__ import annotations

SCRIBE_V2_PRICE_PER_HOUR = 0.22  # USD per hour of audio (estimate; see docstring)

_MODEL_ID = "scribe_v2"


def estimate_cost(duration_ms: int, *, model: str = _MODEL_ID) -> float | None:
    """Estimate the Scribe transcription cost for ``duration_ms`` of audio.

    Returns None for an unpriced model (never a fabricated 0), matching the
    AssemblyAI/Deepgram pricing modules.
    """
    if model != _MODEL_ID:
        return None
    hours = duration_ms / 3_600_000
    return round(hours * SCRIBE_V2_PRICE_PER_HOUR, 6)
