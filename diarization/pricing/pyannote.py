"""pyannoteAI diarization pricing (rate card ESTIMATE, not an invoice).

Rates are EUR — the only provider in the stack that bills in euros. The
primary truth is docs/provider_catalog.yaml (P68 canon: prices are READ from
the catalog file, never re-searched on the web):

    precision-2  EUR 0.112 / audio hour
    community-1  EUR 0.035 / audio hour
    (verified 2026-07-09 against pyannote.ai/pricing; plans are monthly EUR
    credits — Developer EUR 19 ~= 170 h of precision-2)

Billing mechanics (docs.pyannote.ai/administration/billing, 2026-07-11):
only ``succeeded`` jobs are billed, per SECOND of submitted audio, with a
**20-second minimum charge** per job.

The pipeline's cost fields are USD (``cost_estimate_usd``, ``total_cost``),
so this module also provides a USD equivalent through a PINNED conversion
rate. The rate is a dated snapshot, NOT a live feed — EUR cost is the
primary number, the USD figure is for cross-provider comparability only
(both are written to the diarization stage's cost_latency.json).

Version: 1.0 · Date: 11 Jul 2026 (P84)
"""

from __future__ import annotations

#: EUR per audio hour by model (docs/provider_catalog.yaml, verified 2026-07-09).
RATES_EUR_PER_HOUR: dict[str, float] = {
    "precision-2": 0.112,
    "community-1": 0.035,
}

#: Minimum billable duration per successful job (billing.md, 2026-07-11).
MIN_BILLABLE_SECONDS = 20.0

#: EUR->USD conversion for the comparability figure. Snapshot of 2026-07-11
#: (~1.1416 per tradingeconomics/moneyswapp), rounded conservatively; exchange
#: rates drift daily — re-pin when the drift starts to matter.
EUR_USD_RATE = 1.14
EUR_USD_RATE_AS_OF = "2026-07-11"


def billable_seconds(duration_ms: int) -> float:
    """Actual seconds billed: max(audio seconds, the 20 s minimum)."""
    return max(duration_ms / 1000.0, MIN_BILLABLE_SECONDS)


def estimate_cost_eur(duration_ms: int, *, model: str = "precision-2",
                      **_ignored) -> float | None:
    """EUR estimate for one diarization job; None for an unknown model.

    Unknown model -> None (never a fabricated 0) — same contract as the STT
    pricing modules.
    """
    rate = RATES_EUR_PER_HOUR.get(model)
    if rate is None:
        return None
    return round(billable_seconds(duration_ms) * rate / 3600.0, 6)


def estimate_cost(duration_ms: int, *, model: str = "precision-2",
                  **_ignored) -> float | None:
    """USD-equivalent estimate (EUR figure x the pinned EUR_USD_RATE).

    Named ``estimate_cost`` for symmetry with the STT pricing modules (the
    runner sums USD); the EUR figure from ``estimate_cost_eur`` is the
    primary rate-card number.
    """
    eur = estimate_cost_eur(duration_ms, model=model)
    if eur is None:
        return None
    return round(eur * EUR_USD_RATE, 6)
