"""AssemblyAI pre-recorded pricing (estimate — pro-rated to the second).

Rates verified 2026-06-30 (assemblyai.com/pricing, recorded in the harness
``benchmark/pricing.py`` + ARCHITECTURE.md v1.1 §11.1):
  - universal-2         $0.15 / hr
  - universal-3-pro     $0.21 / hr
  - speaker diarization +$0.02 / hr (billable add-on for the Universal models)

``universal-3-5-pro`` re-verified 2026-07-09 (assemblyai.com/pricing.md):
$0.21 / hr async; the standard +$0.02/hr diarization add-on applies to it too.
(The same page also lists an "Experimental" diarization variant at +$0.065/hr
for high speaker counts — NOT modelled here; we send the standard one.)
An unknown model still returns ``None`` (never a fabricated $0.00).

Cost = seconds x (base + add-ons) / 3600 (docs: pre-recorded is "pro-rated to
the exact second"). This is an ESTIMATE from the published rate card, not an
invoice.

Version: 1.1 · Date: 09 Jul 2026 (P65: universal-3-5-pro rate verified + added)
"""

from __future__ import annotations

RATES_PER_HOUR_USD: dict[str, float] = {
    "universal-2": 0.15,
    "universal-3-pro": 0.21,
    "universal-3-5-pro": 0.21,   # verified 2026-07-09, assemblyai.com/pricing.md
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
