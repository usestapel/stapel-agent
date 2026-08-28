"""AssemblyAI pre-recorded pricing (estimate — pro-rated to the second).

RE-VERIFIED 2026-08-28 on the official page (https://www.assemblyai.com/pricing)
— unchanged from the 2026-07-09 reading:
  - universal-2         $0.15 / hr
  - universal-3-5-pro   $0.21 / hr
  - speaker diarization +$0.02 / hr (billable add-on for the Universal models)

(The same page also lists an "Experimental" diarization variant at +$0.065/hr
for high speaker counts — NOT modelled here; we send the standard one.)
An unknown model still returns ``None`` (never a fabricated $0.00).

``universal-3-pro`` is NOT on the 2026-08-28 page: it lists Universal-2 and
Universal-3.5 Pro and marks SLAM-1 deprecated. The key stays priced at its
last verified rate ($0.21 / hr, read 2026-06-30) rather than being deleted —
a run recorded against it is still priceable — but that one number carries the
OLDER date and nothing re-confirmed it today.

Cost = seconds x (base + add-ons) / 3600 (docs: pre-recorded is "pro-rated to
the exact second"). This is an ESTIMATE from the published rate card, not an
invoice.

Version: 1.2 · Date: 28 Aug 2026 (re-verification sweep; universal-3-pro
marked as off the current page)
Source: https://www.assemblyai.com/pricing
Verified: 2026-08-28
"""

from __future__ import annotations

RATES_PER_HOUR_USD: dict[str, float] = {
    "universal-2": 0.15,
    "universal-3-pro": 0.21,       # off the 2026-08-28 page; rate as read 2026-06-30
    "universal-3-5-pro": 0.21,    # re-verified 2026-08-28, assemblyai.com/pricing
}
DIARIZATION_ADDON_PER_HOUR_USD = 0.02


def estimate_cost(duration_ms: int, *, model: str = "universal-2",
                  diarization: bool = True) -> float | None:
    """Estimated USD cost for ``duration_ms`` of audio on ``model``.

    Returns None for an unpriced model (never a fabricated 0).
    """
    base = RATES_PER_HOUR_USD.get(model)
    if base is None:
        return None
    rate = base + (DIARIZATION_ADDON_PER_HOUR_USD if diarization else 0.0)
    hours = duration_ms / 3_600_000
    return round(hours * rate, 6)
