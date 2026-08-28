"""Soniox async STT pricing — rate card, dated and sourced.

RE-VERIFIED 2026-08-28 (soniox.com/pricing) — every number below unchanged
since the 2026-07-09 reading, and kept as the planning rate:
  - Async STT effective $0.10 per audio-hour. Token-based underneath:
    $1.50 / 1M audio-input tokens (~30k tokens per audio-hour) +
    $3.50 / 1M text-output tokens (~15k tokens per speech-hour) — the page's
    own headline resolves the sum to $0.10/hr, which is what we plan with.
  - Speaker diarization (<= 15 speakers), language identification and smart
    formatting are BUNDLED into the rate (pricing FAQ; no add-ons).
  - Real-time is a different card ($0.12/hr) — not wired in this adapter.

The billable job also reports ``audio_duration_ms`` on GET
/v1/transcriptions/{id} (OpenAPI, verified 2026-07-10) — the estimate below
uses OUR ffprobe duration before the call and can be re-checked against the
provider's number after it.

The shared P65 contract: ``estimate_cost(duration_ms, *, model=...) ->
float | None`` — an unknown model returns ``None`` (never a fabricated 0).
Prices are volatile — re-check the pricing page on the day of use.

Version: 1.1 · Date: 28 Aug 2026 (re-verification sweep; rates unchanged)
Source: https://soniox.com/pricing
Verified: 2026-08-28
"""

from __future__ import annotations

#: Async PAYG effective rate, USD per audio-hour (diarization + LID included).
STT_ASYNC_V5_PRICE_PER_HOUR = 0.10

#: Underlying token prices (informational; the hourly effective rate above is
#: the planning number — do not hand-multiply tokens).
ASYNC_AUDIO_INPUT_PER_MTOK_USD = 1.50
ASYNC_TEXT_OUTPUT_PER_MTOK_USD = 3.50

#: Wire model id -> rate. stt-async-v4 is an ALIAS routed to v5 since
#: 2026-06-30 (models.mdx changelog) — same rate, kept for completeness.
_RATES = {
    "stt-async-v5": STT_ASYNC_V5_PRICE_PER_HOUR,
    "stt-async-v4": STT_ASYNC_V5_PRICE_PER_HOUR,
}


def estimate_cost(duration_ms: int, *, model: str = "stt-async-v5") -> float | None:
    """Estimated USD cost of one async transcription.

    Args:
        duration_ms: Audio duration in milliseconds.
        model: Soniox wire model id (``stt-async-v5``; the ``stt-async-v4``
            alias routes to v5 server-side). Unknown models return ``None``
            so a mispriced run can never be silently reported as $0.

    Returns:
        Rounded cost in USD (6 dp), or ``None`` for an unknown model.
        Diarization and language identification are included in the rate.
    """
    rate = _RATES.get(model)
    if rate is None:
        return None
    hours = duration_ms / 3_600_000
    return round(hours * rate, 6)
