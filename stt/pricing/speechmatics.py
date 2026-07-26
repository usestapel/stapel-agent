"""Speechmatics batch (pre-recorded) pricing — rate card, dated and sourced.

VERIFIED on the live pricing page (https://www.speechmatics.com/pricing):
  - Pro plan hero rate "from $0.129/hr" = the Melia 1 batch rate
    (re-confirmed 2026-07-10 on the rendered page).
  - Standard $0.24/hr and Enhanced $0.40/hr batch rates come from the
    compare-plans pricing table, verified 2026-07-09 (P68); on 2026-07-10 the
    table body renders only interactively, so the two legacy rates carry the
    2026-07-09 verification date. Realtime is a DIFFERENT rate card
    (0.24/0.43) — do not confuse.
  - Speaker diarization is INCLUDED on all plans (feature table; the paid
    bolt-ons are translation/summaries/chapters/sentiment/topics only).
  - Billed to the second (pricing FAQ) — costs are pro-rated like the other
    pricing modules.
  - Free tier is self-contradictory on the provider's own pages (2026-07-10):
    pricing hero says "Free 3,000 minutes (50 hours) per month", the P68
    compare table split that into 30 h batch + 20 h realtime, the pricing FAQ
    said "8hrs", and docs/speech-to-text/batch/limits.md says the Free Tier is
    10 h/month for batch standard/enhanced. Never plan spend around the free
    tier; the rate card below is the planning source.
  - Model Training opt-in = 33% discount; >500 h/month = volume discount.
    Both are account-level modifiers, NOT the PAYG rate card — not modelled.

The shared P65 contract: ``estimate_cost(duration_ms, *, model=...) ->
float | None`` — an unknown model returns ``None`` (never a fabricated 0).

Version: 1.0 · Date: 10 Jul 2026 (P73)
"""

from __future__ import annotations

#: Batch PAYG rates, USD per audio-hour (Pro plan; diarization included).
MELIA1_BATCH_PRICE_PER_HOUR = 0.129
STANDARD_BATCH_PRICE_PER_HOUR = 0.24
ENHANCED_BATCH_PRICE_PER_HOUR = 0.40

#: Wire model id -> batch rate. Enhanced is priced here for completeness even
#: though only melia-1 / standard have catalog configs (P73).
_BATCH_RATES = {
    "melia-1": MELIA1_BATCH_PRICE_PER_HOUR,
    "standard": STANDARD_BATCH_PRICE_PER_HOUR,
    "enhanced": ENHANCED_BATCH_PRICE_PER_HOUR,
}


def estimate_cost(duration_ms: int, *, model: str = "melia-1") -> float | None:
    """Estimated USD cost of one batch transcription.

    Args:
        duration_ms: Audio duration in milliseconds.
        model: Speechmatics wire model id (``melia-1`` | ``standard`` |
            ``enhanced``). Unknown models return ``None`` so a mispriced run
            can never be silently reported as $0.

    Returns:
        Rounded cost in USD (6 dp), or ``None`` for an unknown model.
        Diarization is included in the rate (no add-on, unlike AssemblyAI
        or Deepgram).
    """
    rate = _BATCH_RATES.get(model)
    if rate is None:
        return None
    hours = duration_ms / 3_600_000
    return round(hours * rate, 6)
