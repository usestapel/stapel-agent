"""Provider rate cards — published pricing tables + ``estimate_cost()``
helpers run BEFORE/AFTER an STT call to record an honest cost estimate.

Ported verbatim from the iron-benchmark research harness
(``pipeline/adapters/*_pricing.py``). Each provider's rate card is its own
module because the differences between them are the measured differences
between providers' published pricing pages — see each module's docstring for
the provider-specific rates, dated verification notes and "not modelled
here" caveats.

Public API (lazily resolved, PEP 562 — same pattern as the package root and
the other registry packages: importing this package does not import any of
the seven pricing submodules until an attribute is actually accessed, so a
module-level ``from .assemblyai import ...`` here cannot re-open the 3.14
import-lock deadlock described in ``stapel_agent/__init__.py``).
"""

from __future__ import annotations

__all__ = [
    "RATES_PER_HOUR_USD",
    "DIARIZATION_ADDON_PER_HOUR_USD",
    "estimate_assemblyai_cost",
    "NOVA3_PRICING",
    "NOVA3_BATCH_PRICE_PER_HOUR",
    "NOVA3_MULTI_PRICE_PER_HOUR",
    "NOVA3_DIARIZATION_ADDON_PER_HOUR",
    "NOVA3_KEYTERM_ADDON_PER_HOUR",
    "NOVA3_KEYTERM_ADDON_PER_MIN",
    "NOVA3_RATE_CARD_VERSION",
    "RATE_CARD_VERSION",
    "estimate_deepgram_cost",
    "SCRIBE_V2_PRICE_PER_HOUR",
    "estimate_elevenlabs_cost",
    "GLADIA_ASYNC_PRICE_PER_HOUR",
    "estimate_gladia_cost",
    "STT_ASYNC_V5_PRICE_PER_HOUR",
    "estimate_soniox_cost",
    "MELIA1_BATCH_PRICE_PER_HOUR",
    "STANDARD_BATCH_PRICE_PER_HOUR",
    "ENHANCED_BATCH_PRICE_PER_HOUR",
    "estimate_speechmatics_cost",
    "STT_REST_PRICE_PER_HOUR",
    "STT_STREAMING_PRICE_PER_HOUR",
    "estimate_xai_stt_cost",
]

# name -> (relative module, attribute). Each provider module's function is
# named ``estimate_cost`` (verbatim, per its own docstring) — aliased here to
# a provider-qualified name since a flat package namespace cannot hold seven
# identically-named functions at once.
_EXPORTS = {
    "RATES_PER_HOUR_USD": (".assemblyai", "RATES_PER_HOUR_USD"),
    "DIARIZATION_ADDON_PER_HOUR_USD": (".assemblyai", "DIARIZATION_ADDON_PER_HOUR_USD"),
    "estimate_assemblyai_cost": (".assemblyai", "estimate_cost"),
    "NOVA3_PRICING": (".deepgram", "NOVA3_PRICING"),
    "NOVA3_BATCH_PRICE_PER_HOUR": (".deepgram", "NOVA3_BATCH_PRICE_PER_HOUR"),
    "NOVA3_MULTI_PRICE_PER_HOUR": (".deepgram", "NOVA3_MULTI_PRICE_PER_HOUR"),
    "NOVA3_DIARIZATION_ADDON_PER_HOUR": (
        ".deepgram", "NOVA3_DIARIZATION_ADDON_PER_HOUR"),
    "NOVA3_KEYTERM_ADDON_PER_HOUR": (".deepgram", "NOVA3_KEYTERM_ADDON_PER_HOUR"),
    "NOVA3_KEYTERM_ADDON_PER_MIN": (".deepgram", "NOVA3_KEYTERM_ADDON_PER_MIN"),
    "NOVA3_RATE_CARD_VERSION": (".deepgram", "NOVA3_RATE_CARD_VERSION"),
    "RATE_CARD_VERSION": (".deepgram", "RATE_CARD_VERSION"),
    "estimate_deepgram_cost": (".deepgram", "estimate_cost"),
    "SCRIBE_V2_PRICE_PER_HOUR": (".elevenlabs", "SCRIBE_V2_PRICE_PER_HOUR"),
    "estimate_elevenlabs_cost": (".elevenlabs", "estimate_cost"),
    "GLADIA_ASYNC_PRICE_PER_HOUR": (".gladia", "GLADIA_ASYNC_PRICE_PER_HOUR"),
    "estimate_gladia_cost": (".gladia", "estimate_cost"),
    "STT_ASYNC_V5_PRICE_PER_HOUR": (".soniox", "STT_ASYNC_V5_PRICE_PER_HOUR"),
    "estimate_soniox_cost": (".soniox", "estimate_cost"),
    "MELIA1_BATCH_PRICE_PER_HOUR": (".speechmatics", "MELIA1_BATCH_PRICE_PER_HOUR"),
    "STANDARD_BATCH_PRICE_PER_HOUR": (
        ".speechmatics", "STANDARD_BATCH_PRICE_PER_HOUR"),
    "ENHANCED_BATCH_PRICE_PER_HOUR": (
        ".speechmatics", "ENHANCED_BATCH_PRICE_PER_HOUR"),
    "estimate_speechmatics_cost": (".speechmatics", "estimate_cost"),
    "STT_REST_PRICE_PER_HOUR": (".xai_stt", "STT_REST_PRICE_PER_HOUR"),
    "STT_STREAMING_PRICE_PER_HOUR": (".xai_stt", "STT_STREAMING_PRICE_PER_HOUR"),
    "estimate_xai_stt_cost": (".xai_stt", "estimate_cost"),
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
