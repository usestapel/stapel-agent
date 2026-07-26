"""Provider rate card for diarization providers — published pricing table +
``estimate_cost()`` / ``estimate_cost_eur()`` helpers for an honest cost
estimate.

Ported verbatim from the iron-benchmark research harness
(``pipeline/adapters/pyannote_diar_pricing.py``).

Public API (lazily resolved, PEP 562 — same pattern as the package root and
the other registry packages: importing this package does not import the
``pyannote`` submodule until an attribute is actually accessed, so a
module-level ``from .pyannote import ...`` here cannot re-open the 3.14
import-lock deadlock described in ``stapel_agent/__init__.py``).
"""

from __future__ import annotations

__all__ = [
    "RATES_EUR_PER_HOUR",
    "MIN_BILLABLE_SECONDS",
    "EUR_USD_RATE",
    "EUR_USD_RATE_AS_OF",
    "billable_pyannote_seconds",
    "estimate_pyannote_cost_eur",
    "estimate_pyannote_cost",
]

# name -> (relative module, attribute). ``estimate_cost`` (and its EUR
# sibling) is named verbatim inside the module, per its own docstring —
# aliased here to a provider-qualified name for the package-level namespace.
_EXPORTS = {
    "RATES_EUR_PER_HOUR": (".pyannote", "RATES_EUR_PER_HOUR"),
    "MIN_BILLABLE_SECONDS": (".pyannote", "MIN_BILLABLE_SECONDS"),
    "EUR_USD_RATE": (".pyannote", "EUR_USD_RATE"),
    "EUR_USD_RATE_AS_OF": (".pyannote", "EUR_USD_RATE_AS_OF"),
    "billable_pyannote_seconds": (".pyannote", "billable_seconds"),
    "estimate_pyannote_cost_eur": (".pyannote", "estimate_cost_eur"),
    "estimate_pyannote_cost": (".pyannote", "estimate_cost"),
}


def __getattr__(name):
    try:
        module_path, attr = _EXPORTS[name]
    except KeyError:
        raise AttributeError(
            f"module {__name__!r} has no attribute {name!r}"
        ) from None
    from importlib import import_module

    value = import_module(module_path, __name__)
    if attr is not None:
        value = getattr(value, attr)
    globals()[name] = value  # cache: subsequent lookups skip __getattr__
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))
