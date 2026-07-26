"""xAI Grok STT pricing — rate card, dated and sourced.

VERIFIED from two independent primary sources:
  - docs.x.ai/developers/pricing ("Last updated: July 3, 2026"; page opens
    ONLY via scrapling stealthy_fetch — WAF): Speech to Text REST $0.10/hr,
    Streaming $0.20/hr.
  - Igor's console capture (console.x.ai models page, captured 2026-07-11):
    "Speech to Text — REST $0.10 / hr · Streaming $0.20 / hr" — identical.

There is NO model parameter on POST /v1/stt (rest-api-reference/inference/
voice, verified 2026-07-10): the served model cannot be pinned, so the price
key here is the ENDPOINT variant ("stt-rest" | "stt-streaming"), not a model
name. Diarization (``diarize=true``) is NOT priced as an add-on anywhere on
the pricing page — one flat hourly rate.

The shared P65 contract: ``estimate_cost(duration_ms, *, model=...) ->
float | None`` — an unknown key returns ``None`` (never a fabricated 0).
Prices are volatile — re-check the pricing page on the day of use.

Version: 1.0 · Date: 10 Jul 2026 (P76)
"""

from __future__ import annotations

#: PAYG rates, USD per audio-hour (docs.x.ai pricing, verified 2026-07-10).
STT_REST_PRICE_PER_HOUR = 0.10
STT_STREAMING_PRICE_PER_HOUR = 0.20   # WebSocket; not wired in this adapter

#: Endpoint-variant key -> rate. "stt-rest" is the only variant this batch
#: pipeline calls; streaming is priced for completeness.
_RATES = {
    "stt-rest": STT_REST_PRICE_PER_HOUR,
    "stt-streaming": STT_STREAMING_PRICE_PER_HOUR,
}


def estimate_cost(duration_ms: int, *, model: str = "stt-rest") -> float | None:
    """Estimated USD cost of one xAI STT call.

    Args:
        duration_ms: Audio duration in milliseconds.
        model: The ENDPOINT variant key (``stt-rest`` | ``stt-streaming``) —
            xAI STT has no model parameter, so this is not a wire model id.
            Unknown keys return ``None`` so a mispriced run can never be
            silently reported as $0.

    Returns:
        Rounded cost in USD (6 dp), or ``None`` for an unknown key.
        Diarization is included in the flat rate (no add-on).
    """
    rate = _RATES.get(model)
    if rate is None:
        return None
    hours = duration_ms / 3_600_000
    return round(hours * rate, 6)
