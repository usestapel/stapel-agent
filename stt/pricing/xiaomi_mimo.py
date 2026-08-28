"""Xiaomi MiMo ASR pricing (published rate card, per hour of input audio).

Source: https://mimo.mi.com/docs/en-US/price/pay-as-you-go, fetched
21 Aug 2026 and re-verified 28 Aug 2026 (both prices unchanged). The ASR series is billed on the duration of the INPUT AUDIO —
"duration statistics are accurate to the second, and ultimately converted to
hourly billing" — which is the same shape as every other card in this package.

    mimo-v2.5-asr    $0.074 / hour   (overseas price list)
    mimo-v2.5-asr    ¥0.5   / hour   (domestic / mainland-China price list)

WHY THIS MODULE IS NOT IN ``BUILTIN_STT_PRICING_MODULES``
---------------------------------------------------------
There is no MiMo adapter in this package — the one in production lives in a
host (``iron-agent``'s ``agent_ext/stt.py``, registered as ``xiaomi_mimo``),
and the builtin map's keys are, by invariant, names in
``BUILTIN_STT_PROVIDERS``. The card ships here anyway because the missing
piece was never the adapter: a live, paid, key-configured ``zh`` route was
running against no rate card at all, and every one of its rows priced at
"unknown". A host wires the two together next to its own registration::

    from stapel_agent.stt import register_stt_provider
    from stapel_agent.stt.pricing import register_stt_pricing_module

    register_stt_provider("xiaomi_mimo", XiaomiMimoProvider)
    register_stt_pricing_module(
        "xiaomi_mimo", "stapel_agent.stt.pricing.xiaomi_mimo"
    )

NOT MODELLED HERE
-----------------
The domestic CNY list. Which of the two lists an account bills against is a
property of the account, not of the request, and there is no field in the API
response that says. Converting ¥0.5 at some pinned rate would produce a
plausible number for the wrong customers; a host on the domestic list
registers its own module with ``MIMO_V25_ASR_PRICE_PER_HOUR_CNY`` converted at
the rate its finance team uses. The constant is exported for exactly that.

Version: 1.1 · Date: 28 Aug 2026 (re-verification sweep; rates unchanged)
Verified: 2026-08-28
"""

from __future__ import annotations

#: USD per hour of audio — the overseas price list (the default here).
MIMO_V25_ASR_PRICE_PER_HOUR = 0.074

#: CNY per hour of audio — the domestic list. Exported, never converted:
#: see "NOT MODELLED HERE".
MIMO_V25_ASR_PRICE_PER_HOUR_CNY = 0.5

_MODEL_ID = "mimo-v2.5-asr"


def estimate_cost(duration_ms: int, *, model: str = _MODEL_ID) -> float | None:
    """Estimate the MiMo ASR cost for ``duration_ms`` of audio.

    Returns None for an unpriced model (never a fabricated 0), matching the
    other provider pricing modules in this package.
    """
    if model != _MODEL_ID:
        return None
    hours = duration_ms / 3_600_000
    return round(hours * MIMO_V25_ASR_PRICE_PER_HOUR, 6)
