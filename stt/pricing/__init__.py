"""Provider rate cards — published pricing tables + ``estimate_cost()``
helpers run BEFORE/AFTER an STT call to record an honest cost estimate.

Ported verbatim from the iron-benchmark research harness
(``pipeline/adapters/*_pricing.py``). Each provider's rate card is its own
module because the differences between them are the measured differences
between providers' published pricing pages — see each module's docstring for
the provider-specific rates, dated verification notes and "not modelled
here" caveats.

Which module prices which provider is itself a registry: ``pricing_module()``
maps an STT provider name (the SAME key as ``BUILTIN_STT_PROVIDERS``, so a
config and its adapter can never name different providers) to the module that
knows its rate card. A caller that wants "what will this cost" no longer has
to hardcode the seven import paths — and a host that registered its own STT
adapter can register its rate card next to it.

A provider with no rate card resolves to ``None``, and that is the whole point
of returning None rather than a zero-priced stub: self-hosted whisper costs
something, we just do not know what, and "unpriced" must stay distinguishable
from "free".

Public API (lazily resolved, PEP 562 — same pattern as the package root and
the other registry packages: importing this package does not import any of
the seven pricing submodules until an attribute is actually accessed, so a
module-level ``from .assemblyai import ...`` here cannot re-open the 3.14
import-lock deadlock described in ``stapel_agent/__init__.py``).
"""

from __future__ import annotations

__all__ = [
    "BUILTIN_STT_PRICING_MODULES",
    "pricing_module",
    "register_stt_pricing_module",
    "registered_stt_pricing_modules",
    "RATES_PER_HOUR_USD",
    "DIARIZATION_ADDON_PER_HOUR_USD",
    "estimate_assemblyai_cost",
    "NOVA3_PRICING",
    "NOVA3_BATCH_PRICE_PER_HOUR",
    "NOVA3_MULTI_PRICE_PER_HOUR",
    "NOVA3_DIARIZATION_ADDON_STREAMING_PER_HOUR",
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
    "MIMO_V25_ASR_PRICE_PER_HOUR",
    "MIMO_V25_ASR_PRICE_PER_HOUR_CNY",
    "estimate_xiaomi_mimo_cost",
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
    "NOVA3_DIARIZATION_ADDON_STREAMING_PER_HOUR": (
        ".deepgram", "NOVA3_DIARIZATION_ADDON_STREAMING_PER_HOUR"),
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
    "MIMO_V25_ASR_PRICE_PER_HOUR": (
        ".xiaomi_mimo", "MIMO_V25_ASR_PRICE_PER_HOUR"),
    "MIMO_V25_ASR_PRICE_PER_HOUR_CNY": (
        ".xiaomi_mimo", "MIMO_V25_ASR_PRICE_PER_HOUR_CNY"),
    "estimate_xiaomi_mimo_cost": (".xiaomi_mimo", "estimate_cost"),
}


# ── Which module prices which provider ────────────────────────────────
#
# Keys are STT provider names from ``stapel_agent.stt.BUILTIN_STT_PROVIDERS``
# — not the vendors' own ids — so that a model config, its adapter and its
# rate card are addressed by one name. ``whisper-http`` is deliberately
# ABSENT: a self-hosted endpoint has no published card, and inventing a $0
# one would report someone's GPU bill as free.
#
# ``xiaomi_mimo`` is absent for the other reason: the card ships (see
# ``.xiaomi_mimo``) but the adapter is host-side, and this map may only name
# providers this package registers. A host registers the pair together.
BUILTIN_STT_PRICING_MODULES: dict[str, str] = {
    "assemblyai": "stapel_agent.stt.pricing.assemblyai",
    "deepgram": "stapel_agent.stt.pricing.deepgram",
    "elevenlabs": "stapel_agent.stt.pricing.elevenlabs",
    "gladia": "stapel_agent.stt.pricing.gladia",
    "soniox": "stapel_agent.stt.pricing.soniox",
    "speechmatics": "stapel_agent.stt.pricing.speechmatics",
    "xai-stt": "stapel_agent.stt.pricing.xai_stt",
}

# provider name → module object | dotted path | None (None masks the name).
_runtime_stt_pricing_modules: dict[str, object] = {}


def register_stt_pricing_module(name: str, module) -> None:
    """Register the rate card for STT provider *name* at runtime.

    *module* is a module object or a dotted path to one; it must expose
    ``estimate_cost(duration_ms, *, model=..., **kwargs) -> float | None``.
    ``None``/``""`` masks the name (declaring "this provider is unpriced"),
    and re-registering overrides.

    The duck-typed contract is deliberate: a rate card is a module of
    constants plus one function, and requiring a subclass would mean a host
    could not simply point at its own ``pricing.py``.
    """
    if module is None or module == "":
        _runtime_stt_pricing_modules[name] = None
        return
    if isinstance(module, str) or hasattr(module, "estimate_cost"):
        _runtime_stt_pricing_modules[name] = module
        return
    raise TypeError(
        f"register_stt_pricing_module({name!r}) expects a module exposing "
        f"estimate_cost() or a dotted path string, got {module!r}"
    )


def registered_stt_pricing_modules() -> dict:
    """Effective ``name → module-or-dotted-path`` mapping (built-ins ←
    ``STAPEL_AGENT["STT_PRICING_MODULES"]`` ← runtime; falsy entries
    dropped)."""
    from ...conf import agent_settings

    merged = {
        **BUILTIN_STT_PRICING_MODULES,
        **(agent_settings.STT_PRICING_MODULES or {}),
        **_runtime_stt_pricing_modules,
    }
    return {name: target for name, target in merged.items() if target}


def pricing_module(provider: str):
    """The rate-card module for STT provider *provider*, or ``None``.

    ``None`` means "no published card for this provider" — never "free".
    """
    target = registered_stt_pricing_modules().get(provider)
    if target is None:
        return None
    if isinstance(target, str):
        from importlib import import_module

        return import_module(target)
    return target


def _reset_runtime_stt_pricing_modules() -> None:
    """Tests only."""
    _runtime_stt_pricing_modules.clear()


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
