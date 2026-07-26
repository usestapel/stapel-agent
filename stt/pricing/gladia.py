"""Gladia pre-recorded (async) pricing — rate card, dated and sourced.

VERIFIED 2026-07-09 on the live pricing page (https://www.gladia.io/pricing):
  - Starter (pay-as-you-go): "Async at $0.61/hr" (real-time is $0.75/hr — a
    DIFFERENT product, do not confuse); the first 10 h per month are free.
  - Speaker diarization is INCLUDED in the async price (a "core capability" on
    every plan) — unlike AssemblyAI, where diarization is a +$0.02/hr add-on.
  - The rate is per MODEL-agnostic async hour: the page prices "async"
    transcription once, with no solaria-1 vs solaria-3 differentiation.
  - Growth ("as low as $0.20/hr") and Enterprise are negotiated commit plans —
    NOT the PAYG rate card, so they are not modelled here.
  - NOTE: docs.gladia.io has NO pricing chapter (the P66 prompt's
    chapters/pre-recorded-stt/pricing URL does not exist); the price lives on
    the marketing site only. Re-verify there on any billing-relevant day.

Billing unit (verified from the GET /v2/pre-recorded OpenAPI schema):
``result.metadata.billing_time = audio_duration * number_of_distinct_channels``
(seconds). Our benchmark corpus is mono, so ``channels`` defaults to 1; pass
the real channel count for stereo files or the estimate is 2x low.

Costs are pro-rated to the second, mirroring the other pricing modules. The
shared P65 contract: ``estimate_cost(duration_ms, *, model=...) -> float | None``
— an unknown model returns ``None`` (never a fabricated 0).

Version: 1.0 · Date: 09 Jul 2026 (P66)
"""

from __future__ import annotations

#: PAYG async rate, USD per audio-hour (Starter plan; diarization included).
GLADIA_ASYNC_PRICE_PER_HOUR = 0.61

#: Async models priced by the one PAYG rate (the page does not split them).
_ASYNC_MODELS = frozenset({"solaria-1", "solaria-3"})


def estimate_cost(duration_ms: int, *, model: str = "solaria-1",
                  channels: int = 1) -> float | None:
    """Estimated USD cost of one pre-recorded transcription.

    Args:
        duration_ms: Audio duration in milliseconds.
        model: Gladia model id; only ``solaria-1`` / ``solaria-3`` are priced
            (one shared async rate). Unknown models return ``None`` so a
            mispriced run can never be silently reported as $0.
        channels: Distinct channel count (billing_time multiplies by it).
            Defaults to 1 — the benchmark corpus is mono.

    Returns:
        Rounded cost in USD (6 dp), or ``None`` for an unknown model.
    """
    if model not in _ASYNC_MODELS:
        return None
    hours = duration_ms / 3_600_000
    return round(hours * GLADIA_ASYNC_PRICE_PER_HOUR * max(1, int(channels)), 6)
