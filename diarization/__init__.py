"""Diarization provider registry — same open merge semantics as the LLM /
STT / image registries.

Three layers, merged in increasing precedence:

1. ``BUILTIN_DIARIZATION_PROVIDERS`` — the adapters shipped with this
   package;
2. ``STAPEL_AGENT["DIARIZATION_PROVIDERS"]`` — host settings, merged OVER
   the built-ins (add one name, never restate the rest; ``None``/``""``
   removes a name);
3. runtime ``register_diarization_provider()`` — for app-layer
   ``AppConfig.ready()`` registration.

Django-free at import time; settings are read when
``registered_diarization_providers()`` is called.
"""
from __future__ import annotations

import inspect

from .base import DiarizationProvider

BUILTIN_DIARIZATION_PROVIDERS = {
    "pyannote-http": (
        "stapel_agent.diarization.providers.pyannote_http.PyannoteHttpProvider"
    ),
}

# name → DiarizationProvider subclass | dotted path | None (None masks the name).
_runtime_diarization_providers: dict[str, object] = {}


def register_diarization_provider(name: str, provider) -> None:
    """Register *provider* (a ``DiarizationProvider`` subclass or a dotted
    path) under *name* at runtime — highest precedence. ``None``/``""``
    masks the name; re-registering overrides."""
    if provider is None or provider == "":
        _runtime_diarization_providers[name] = None
        return
    if isinstance(provider, str):
        _runtime_diarization_providers[name] = provider
        return
    if inspect.isclass(provider) and issubclass(provider, DiarizationProvider):
        _runtime_diarization_providers[name] = provider
        return
    raise TypeError(
        f"register_diarization_provider({name!r}) expects a "
        f"DiarizationProvider subclass or a dotted path string, got {provider!r}"
    )


def registered_diarization_providers() -> dict:
    """Effective ``name → class-or-dotted-path`` mapping (built-ins ←
    ``STAPEL_AGENT["DIARIZATION_PROVIDERS"]`` ← runtime; falsy entries
    dropped)."""
    from ..conf import agent_settings

    merged = {
        **BUILTIN_DIARIZATION_PROVIDERS,
        **(agent_settings.DIARIZATION_PROVIDERS or {}),
        **_runtime_diarization_providers,
    }
    return {name: target for name, target in merged.items() if target}


def _reset_runtime_diarization_providers() -> None:
    """Tests only."""
    _runtime_diarization_providers.clear()


__all__ = [
    "BUILTIN_DIARIZATION_PROVIDERS",
    "register_diarization_provider",
    "registered_diarization_providers",
]
