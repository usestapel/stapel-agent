"""STT provider registry — same open merge semantics as the LLM registry.

Three layers, merged in increasing precedence:

1. ``BUILTIN_STT_PROVIDERS`` — the adapters shipped with this package;
2. ``STAPEL_AGENT["STT_PROVIDERS"]`` — host settings, merged OVER the
   built-ins (add one name, never restate the rest; ``None``/``""``
   removes a name);
3. runtime ``register_stt_provider()`` — for app-layer
   ``AppConfig.ready()`` registration (e.g. a self-hosted GigaAM
   adapter — see MODULE.md for the worked example).

Django-free at import time; settings are read when
``registered_stt_providers()`` is called.
"""
from __future__ import annotations

import inspect

from .base import SttProvider

BUILTIN_STT_PROVIDERS = {
    "whisper-http": "stapel_agent.stt.providers.whisper_http.WhisperHttpProvider",
    "elevenlabs": "stapel_agent.stt.providers.elevenlabs.ElevenLabsProvider",
    "assemblyai": "stapel_agent.stt.providers.assemblyai.AssemblyAIProvider",
    "deepgram": "stapel_agent.stt.providers.deepgram.DeepgramProvider",
    "gladia": "stapel_agent.stt.providers.gladia.GladiaProvider",
    "soniox": "stapel_agent.stt.providers.soniox.SonioxProvider",
    "speechmatics": "stapel_agent.stt.providers.speechmatics.SpeechmaticsProvider",
    "xai-stt": "stapel_agent.stt.providers.xai_stt.XaiSttProvider",
}

# name → SttProvider subclass | dotted path | None (None masks the name).
_runtime_stt_providers: dict[str, object] = {}


def register_stt_provider(name: str, provider) -> None:
    """Register *provider* (an ``SttProvider`` subclass or a dotted path)
    under *name* at runtime — highest precedence. ``None``/``""`` masks
    the name; re-registering overrides."""
    if provider is None or provider == "":
        _runtime_stt_providers[name] = None
        return
    if isinstance(provider, str):
        _runtime_stt_providers[name] = provider
        return
    if inspect.isclass(provider) and issubclass(provider, SttProvider):
        _runtime_stt_providers[name] = provider
        return
    raise TypeError(
        f"register_stt_provider({name!r}) expects an SttProvider subclass "
        f"or a dotted path string, got {provider!r}"
    )


def registered_stt_providers() -> dict:
    """Effective ``name → class-or-dotted-path`` mapping (built-ins ←
    ``STAPEL_AGENT["STT_PROVIDERS"]`` ← runtime; falsy entries dropped)."""
    from ..conf import agent_settings

    merged = {
        **BUILTIN_STT_PROVIDERS,
        **(agent_settings.STT_PROVIDERS or {}),
        **_runtime_stt_providers,
    }
    return {name: target for name, target in merged.items() if target}


def _reset_runtime_stt_providers() -> None:
    """Tests only."""
    _runtime_stt_providers.clear()


__all__ = [
    "BUILTIN_STT_PROVIDERS",
    "register_stt_provider",
    "registered_stt_providers",
]
