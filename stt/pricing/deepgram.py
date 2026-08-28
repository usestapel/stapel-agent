"""Deepgram Nova-3 pricing (estimate — the API returns no cost field).

Rates VERIFIED 2026-08-28 from the OFFICIAL page (https://deepgram.com/pricing,
"Speech to Text" + "Speech-to-Text Add-ons" tables; fetched twice on the day,
the second time asking for the Speaker Diarization row verbatim).

  - Nova-3 Monolingual  Pre-Recorded  Pay As You Go  $0.0043 / min  ($0.258 / hr)
  - Nova-3 Monolingual  Pre-Recorded  Growth         $0.0036 / min  ($0.216 / hr)
  - Nova-3 Multilingual Pre-Recorded  Pay As You Go  $0.0052 / min  ($0.312 / hr)
  - Nova-3 Multilingual Pre-Recorded  Growth         $0.0043 / min  ($0.258 / hr)
  - Speaker Diarization  Pre-Recorded  INCLUDED, both tiers (no add-on)
  - Speaker Diarization  Streaming     $0.0020 / min PAYG; Growth not published
  - Keyterm Prompting    Both          $0.0013 / min PAYG, $0.0012 / min Growth

THIS PAGE IS VOLATILE — TREAT THIS CARD AS DATED, NOT AUTHORITATIVE
-------------------------------------------------------------------
The same two numbers have now moved TWICE under this module, in opposite
directions, inside seven weeks:

  2026-07-04  mono batch $0.0043 / min, diarization included
  2026-07-09  mono batch $0.0048 / min, diarization a PAID add-on $0.0020/min
  2026-08-28  mono batch $0.0043 / min, diarization included (pre-recorded)

The 2026-07-09 reading is what shipped, and between then and this sync a
diarized monolingual hour was estimated at $0.408 against a true $0.258 — a
58% overstatement. The lesson is not "the July reading was careless": each
reading matched the page on its day. The lesson is that this vendor restates
its public rate card between quarters and the diarization line moves between
"included" and "add-on", so anyone planning spend re-reads
https://deepgram.com/pricing on the day and does not trust the constants
below to still be the page.

DIARIZATION IS NOT AN ADD-ON FOR THE WORKLOAD THIS PACKAGE PRICES
------------------------------------------------------------------
On the 2026-08-28 card the $0.0020/min Speaker Diarization add-on is charged
on STREAMING only; the Pre-Recorded column reads "Included" for both Pay As
You Go and Growth. This package prices batch, so ``estimate_cost`` adds
nothing for diarization. The ``diarization`` keyword is KEPT (default True —
our adapter always sends ``diarize_model``) so that callers and
``ModelConfig.pricing_kwargs`` do not have to change with the vendor's
packaging, and because this line has already moved twice; it is currently a
no-op on the batch rates. ``NOVA3_DIARIZATION_ADDON_STREAMING_PER_MIN``
carries the streaming rate under a name that says which product it bills —
the unqualified ``NOVA3_DIARIZATION_ADDON_PER_*`` constants are gone, because
a constant named for an add-on this workload does not pay is how the
overstatement above stayed invisible.

The price VARIANT reaches this module via ``multilingual=True`` — the runner
forwards it from ``ModelConfig.pricing_kwargs`` (``deepgram_nova3_multi``
carries ``{"multilingual": True}``), so a ``language=multi`` run is estimated
at its true $0.312/hr while sharing wire model "nova-3" with the mono default
($0.258/hr).

Deepgram bills per MINUTE of audio; this estimate pro-rates by the exact clip
duration (minutes = duration_ms / 60000). The precise sub-minute rounding rule
is not published, so this is an ESTIMATE from the rate card, not a billed
amount.

Keyterm Prompting is a paid add-on on both products; it is billed only when
the request actually carries ``keyterm`` params, so ``estimate_cost`` takes an
opt-in ``keyterm=False`` flag. Its Growth rate ($0.0012/min) IS published on
the 2026-08-28 card and is now used for the Growth tier — the previous card
had no Growth column for it and deliberately charged PAYG rather than invent a
discount.

Version: 3.0 · Date: 28 Aug 2026 (rate-card resync: mono/multi batch and
Growth rates corrected, pre-recorded diarization is included again, keyterm
Growth rate added; ``NOVA3_DIARIZATION_ADDON_PER_MIN``/``_PER_HOUR`` renamed
to ``..._STREAMING_...``)
Source: https://deepgram.com/pricing
Verified: 2026-08-28
"""

from __future__ import annotations

# Native Deepgram billing unit is per-minute. Keyed by model; monolingual and
# multilingual are PRICE VARIANTS of the same wire model "nova-3" (selected by
# the request's language param, not the model param).
NOVA3_PRICING: dict[str, dict] = {
    "nova-3": {
        "batch_per_min_usd": 0.0043,          # Pre-Recorded, Monolingual, Pay As You Go
        "growth_per_min_usd": 0.0036,         # Pre-Recorded, Monolingual, Growth tier
        "multi_batch_per_min_usd": 0.0052,    # Pre-Recorded, Multilingual, Pay As You Go
        "multi_growth_per_min_usd": 0.0043,   # Pre-Recorded, Multilingual, Growth tier
        # Pre-Recorded diarization is "Included" on both tiers (2026-08-28).
        # The flag below is the fact the estimate reads, so a future card that
        # moves it back changes one boolean and one rate, not the arithmetic.
        "diarization_batch_included": True,
        # STREAMING only. Kept for provenance and for a host that prices a
        # live pipeline; never added to a batch estimate. Growth column reads
        # "—" on the card, so there is no Growth streaming rate here.
        "diarization_addon_streaming_per_min_usd": 0.0020,
        # Keyterm Prompting Add-on: only billed when the request carries
        # keyterm params. Both tiers published on the 2026-08-28 card.
        "keyterm_addon_per_min_usd": 0.0013,
        "keyterm_addon_growth_per_min_usd": 0.0012,
        "variant": "monolingual",
        "tier": "pay_as_you_go",
        "billing": "pre_recorded_batch",
        "source": "https://deepgram.com/pricing",
        "verified_date": "2026-08-28",
    },
}

# Convenience constants for the Pay As You Go pre-recorded Nova-3 rates.
NOVA3_BATCH_PRICE_PER_MIN = NOVA3_PRICING["nova-3"]["batch_per_min_usd"]
NOVA3_BATCH_PRICE_PER_HOUR = round(NOVA3_BATCH_PRICE_PER_MIN * 60, 6)  # $0.258/hr
NOVA3_MULTI_PRICE_PER_MIN = NOVA3_PRICING["nova-3"]["multi_batch_per_min_usd"]
NOVA3_MULTI_PRICE_PER_HOUR = round(NOVA3_MULTI_PRICE_PER_MIN * 60, 6)  # $0.312/hr

#: Streaming-only Speaker Diarization add-on. A batch estimate never adds it —
#: pre-recorded diarization is included in the rates above.
NOVA3_DIARIZATION_ADDON_STREAMING_PER_MIN = (
    NOVA3_PRICING["nova-3"]["diarization_addon_streaming_per_min_usd"])
NOVA3_DIARIZATION_ADDON_STREAMING_PER_HOUR = round(
    NOVA3_DIARIZATION_ADDON_STREAMING_PER_MIN * 60, 6)                 # $0.12/hr

NOVA3_KEYTERM_ADDON_PER_MIN = (
    NOVA3_PRICING["nova-3"]["keyterm_addon_per_min_usd"])
NOVA3_KEYTERM_ADDON_PER_HOUR = round(
    NOVA3_KEYTERM_ADDON_PER_MIN * 60, 6)                               # $0.078/hr

#: Rate-card provenance for telemetry: stamped into
#: StageMetrics.rate_card_version so an A/B whose two arms were priced on
#: different cards is machine-detectable.
NOVA3_RATE_CARD_VERSION = f"deepgram_{NOVA3_PRICING['nova-3']['verified_date']}"

#: Generic hook: provider-agnostic runners read
#: ``getattr(pricing_module, "RATE_CARD_VERSION", None)``. Pricing modules
#: that stamp provenance expose this alias; modules without it yield None
#: (an honest "unknown card", never a fabricated version).
RATE_CARD_VERSION = NOVA3_RATE_CARD_VERSION


def estimate_cost(
    duration_ms: int, *, model: str = "nova-3", tier: str = "pay_as_you_go",
    multilingual: bool = False, diarization: bool = True,
    keyterm: bool = False,
) -> float | None:
    """Estimated USD cost for ``duration_ms`` of audio on ``model``.

    ``tier`` selects the Pay As You Go (default) or Growth per-minute rate;
    ``multilingual`` selects the Nova-3 Multilingual price variant (the wire
    model stays "nova-3" — the request differs only by ``language=multi``).

    ``diarization`` (default True — our adapter always sends
    ``diarize_model``) adds nothing on the 2026-08-28 card: Speaker
    Diarization is Included on pre-recorded, and the paid add-on is charged on
    streaming. The parameter is kept because this line has moved twice and the
    caller should not have to change with the vendor's packaging.

    ``keyterm`` (default False — opt-in) adds the Keyterm Prompting add-on at
    the requested tier's published rate. Returns None for an unpriced model
    (never a fabricated 0).
    """
    row = NOVA3_PRICING.get(model)
    if row is None:
        return None
    variant_key = "multi_batch_per_min_usd" if multilingual else "batch_per_min_usd"
    growth_key = ("multi_growth_per_min_usd" if multilingual
                  else "growth_per_min_usd")
    per_min = (row[variant_key] if tier == "pay_as_you_go"
               else row.get(growth_key))
    if per_min is None:
        return None
    if diarization and not row["diarization_batch_included"]:
        # Only reachable if a future card moves diarization back out of the
        # pre-recorded rate; the streaming add-on is the rate it moved to.
        per_min += row["diarization_addon_streaming_per_min_usd"]
    if keyterm:
        per_min += (row["keyterm_addon_per_min_usd"] if tier == "pay_as_you_go"
                    else row["keyterm_addon_growth_per_min_usd"])
    minutes = duration_ms / 60_000
    return round(minutes * per_min, 6)
