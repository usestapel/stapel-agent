"""Deepgram Nova-3 pricing (estimate — the API returns no cost field).

Rates VERIFIED 2026-07-09 from the OFFICIAL page (https://deepgram.com/pricing,
"Speech to Text" + "Speech-to-Text Add-ons" tables; re-fetched the same day in
P69 before this sync). Canonical dated copy: ``docs/provider_catalog.yaml``.

  - Nova-3 Monolingual  Pre-Recorded  Pay As You Go  $0.0048 / min  ($0.288 / hr)
  - Nova-3 Monolingual  Pre-Recorded  Growth         $0.0042 / min
  - Nova-3 Multilingual Pre-Recorded  Pay As You Go  $0.0058 / min  ($0.348 / hr)
  - Nova-3 Multilingual Pre-Recorded  Growth         $0.0050 / min
  - Speaker Diarization  Add-on       Pay As You Go  $0.0020 / min  ($0.12 / hr)
  - Speaker Diarization  Add-on       Growth         $0.0017 / min

THE PAGE MOVED between 2026-07-04 and 2026-07-09 (P68 finding): mono batch went
$0.0043 -> $0.0048/min AND Speaker Diarization moved from "included in the
Nova-3 rate" to the paid "Speech-to-Text Add-ons" table (next to Redaction
$0.0020 / Keyterm Prompting $0.0013). Until P69 this module carried the old
card and UNDER-estimated a diarized run by ~37%.

Our adapter ALWAYS sends ``diarize_model`` (this harness is a diarization
benchmark), so ``estimate_cost`` defaults to ``diarization=True`` — the same
convention as ``assemblyai_pricing``. Effective default rate: mono + diar
= $0.0068/min = $0.408/hr.

The price VARIANT reaches this module via ``multilingual=True`` — since P70
the runner forwards it from ``ModelConfig.pricing_kwargs``
(``deepgram_nova3_multi`` carries ``{"multilingual": True}``), so a
``language=multi`` run is estimated at its true $0.468/hr while sharing wire
model "nova-3" with the mono default ($0.408/hr). (P69 shipped with this as a
known limitation for one commit; P70 closed it.)

Deepgram bills per MINUTE of audio; this estimate pro-rates by the exact clip
duration (minutes = duration_ms / 60000). The precise sub-minute rounding rule
is not published, so this is an ESTIMATE from the rate card, not a billed
amount.

Keyterm Prompting (P116) is another paid Add-on from the same 2026-07-09 card
(``docs/provider_catalog.yaml``: ``addon_keyterm_per_min_usd: 0.0013``); it is
billed only when the request actually carries ``keyterm`` params, so
``estimate_cost`` takes an opt-in ``keyterm=False`` flag (unlike diarization,
which this harness always sends).

Version: 2.4 · Date: 19 Jul 2026 (P118: generic RATE_CARD_VERSION alias for
provider-agnostic runners; P117: NOVA3_RATE_CARD_VERSION telemetry
provenance; P116: keyterm add-on tier; P69: rate-card sync; P70:
multilingual variant wired from ModelConfig.pricing_kwargs)
Source: https://deepgram.com/pricing
Verified: 2026-07-09
"""

from __future__ import annotations

# Native Deepgram billing unit is per-minute. Keyed by model; monolingual and
# multilingual are PRICE VARIANTS of the same wire model "nova-3" (selected by
# the request's language param, not the model param).
NOVA3_PRICING: dict[str, dict] = {
    "nova-3": {
        "batch_per_min_usd": 0.0048,          # Pre-Recorded, Monolingual, Pay As You Go
        "growth_per_min_usd": 0.0042,         # Pre-Recorded, Monolingual, Growth tier
        "multi_batch_per_min_usd": 0.0058,    # Pre-Recorded, Multilingual, Pay As You Go
        "multi_growth_per_min_usd": 0.0050,   # Pre-Recorded, Multilingual, Growth tier
        "diarization_addon_per_min_usd": 0.0020,   # paid Add-on (PAYG) since ~2026-07
        "diarization_addon_growth_per_min_usd": 0.0017,
        # Keyterm Prompting Add-on (P116): only billed when the request carries
        # keyterm params. Growth-tier keyterm rate is not published on the card
        # we verified — PAYG only here (never fabricate a discount).
        "keyterm_addon_per_min_usd": 0.0013,
        "variant": "monolingual",
        "tier": "pay_as_you_go",
        "billing": "pre_recorded_batch",
        "source": "https://deepgram.com/pricing",
        "verified_date": "2026-07-09",
    },
}

# Convenience constants for the Pay As You Go pre-recorded Nova-3 rates.
NOVA3_BATCH_PRICE_PER_MIN = NOVA3_PRICING["nova-3"]["batch_per_min_usd"]
NOVA3_BATCH_PRICE_PER_HOUR = round(NOVA3_BATCH_PRICE_PER_MIN * 60, 6)  # $0.288/hr
NOVA3_MULTI_PRICE_PER_MIN = NOVA3_PRICING["nova-3"]["multi_batch_per_min_usd"]
NOVA3_MULTI_PRICE_PER_HOUR = round(NOVA3_MULTI_PRICE_PER_MIN * 60, 6)  # $0.348/hr
NOVA3_DIARIZATION_ADDON_PER_MIN = (
    NOVA3_PRICING["nova-3"]["diarization_addon_per_min_usd"])
NOVA3_DIARIZATION_ADDON_PER_HOUR = round(
    NOVA3_DIARIZATION_ADDON_PER_MIN * 60, 6)                           # $0.12/hr
NOVA3_KEYTERM_ADDON_PER_MIN = (
    NOVA3_PRICING["nova-3"]["keyterm_addon_per_min_usd"])
NOVA3_KEYTERM_ADDON_PER_HOUR = round(
    NOVA3_KEYTERM_ADDON_PER_MIN * 60, 6)                               # $0.078/hr

#: Rate-card provenance for telemetry (P117): stamped into
#: StageMetrics.rate_card_version so an A/B whose two arms were priced on
#: different cards is machine-detectable (the P116 cost_delta lesson).
NOVA3_RATE_CARD_VERSION = f"deepgram_{NOVA3_PRICING['nova-3']['verified_date']}"

#: Generic hook (P118): provider-agnostic runners read
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
    ``diarize_model``) adds the Speaker Diarization add-on. ``keyterm``
    (default False — opt-in, P116) adds the Keyterm Prompting add-on; a
    Growth keyterm rate is not published, so the PAYG rate is used for both
    tiers rather than fabricating a discount. Returns None for an unpriced
    model (never a fabricated 0).
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
    if diarization:
        per_min += (row["diarization_addon_per_min_usd"]
                    if tier == "pay_as_you_go"
                    else row["diarization_addon_growth_per_min_usd"])
    if keyterm:
        per_min += row["keyterm_addon_per_min_usd"]
    minutes = duration_ms / 60_000
    return round(minutes * per_min, 6)
