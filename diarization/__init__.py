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

# The base class is imported inside register_diarization_provider() — NOT
# here. A module-level ``from .base import DiarizationProvider`` makes
# this package body hold its own module lock while it takes the
# submodule's; a thread importing ``stapel_agent.diarization.base`` first
# takes the same two locks in the opposite order. Python 3.14 reports that
# as `_DeadlockError` — it is what killed iron-agent's runserver thread at
# startup (system checks walk the registries on django-main-thread while
# the autoreloader's main thread imports the URLconf → 502 until the next
# restart).

BUILTIN_DIARIZATION_PROVIDERS = {
    "pyannote-http": (
        "stapel_agent.diarization.providers.pyannote_http.PyannoteHttpProvider"
    ),
    "pyannote-cloud": (
        "stapel_agent.diarization.providers.pyannote_cloud.PyannoteCloudProvider"
    ),
}

# name → DiarizationProvider subclass | dotted path | None (None masks the name).
_runtime_diarization_providers: dict[str, object] = {}


def register_diarization_provider(name: str, provider) -> None:
    """Register *provider* (a ``DiarizationProvider`` subclass or a dotted
    path) under *name* at runtime — highest precedence. ``None``/``""``
    masks the name; re-registering overrides."""
    from .base import DiarizationProvider

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
