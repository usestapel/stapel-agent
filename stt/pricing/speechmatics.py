"""Speechmatics batch (pre-recorded) pricing — rate card, dated and sourced.

VERIFIED on the live pricing page (https://www.speechmatics.com/pricing).
Each rate below carries the date it was last actually READ — the compare-plans
table body has never rendered to a fetcher, so the three rates do NOT share
one verification date:
  - Pro plan hero rate "from $0.129/hr" = the Melia 1 batch rate
    (re-confirmed 2026-08-28: the hero still reads $0.129; also stated as
    "from $0.129/hour" in the vendor's own comparison pages).
  - Standard $0.24/hr and Enhanced $0.40/hr batch rates come from the
    compare-plans pricing table, verified 2026-07-09 (P68). The table body
    still renders only interactively (re-attempted 2026-08-28: the fetched
    page carries the plan hero, the free-credit line and the volume-discount
    note, and no per-model rates at all; docs.speechmatics.com has no pricing
    chapter — two candidate URLs 404). On 2026-08-28 the vendor's own
    comparison pages restate Standard "from $0.24/hr"; ENHANCED $0.40/hr WAS
    NOT RE-CONFIRMED and carries the 2026-07-09 date. Realtime is a DIFFERENT
    rate card (0.24/0.43) — do not confuse.
  - Speaker diarization is INCLUDED on all plans (feature table, read
    2026-07-09; that table is part of the body that no longer renders to a
    fetcher, so this is NOT re-confirmed as of 2026-08-28).
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

Version: 1.1 · Date: 28 Aug 2026 (re-verification sweep: Melia and Standard
re-confirmed, Enhanced and the diarization-included claim explicitly NOT)
Source: https://www.speechmatics.com/pricing
Verified: melia-1 and standard 2026-08-28; enhanced 2026-07-09
"""

from __future__ import annotations

#: Batch PAYG rates, USD per audio-hour (Pro plan; diarization included).
MELIA1_BATCH_PRICE_PER_HOUR = 0.129    # re-confirmed 2026-08-28
STANDARD_BATCH_PRICE_PER_HOUR = 0.24   # re-confirmed 2026-08-28
ENHANCED_BATCH_PRICE_PER_HOUR = 0.40   # last read 2026-07-09; see docstring

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
