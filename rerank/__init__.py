"""Rerank provider registry — same open merge semantics as the LLM /
STT / image / diarization / embedding registries.

Three layers, merged in increasing precedence:

1. ``BUILTIN_RERANK_PROVIDERS`` — the adapters shipped with this
   package;
2. ``STAPEL_AGENT["RERANK_PROVIDERS"]`` — host settings, merged OVER
   the built-ins (add one name, never restate the rest; ``None``/``""``
   removes a name);
3. runtime ``register_rerank_provider()`` — for app-layer
   ``AppConfig.ready()`` registration.

Django-free at import time; settings are read when
``registered_rerank_providers()`` is called.
"""
from __future__ import annotations

import inspect

from .base import RerankProvider

BUILTIN_RERANK_PROVIDERS = {
    "deepinfra-rerank": (
        "stapel_agent.rerank.providers.deepinfra.DeepInfraRerankProvider"
    ),
    "rerank-http": (
        "stapel_agent.rerank.providers.http_server.HttpServerRerankProvider"
    ),
}

# name → RerankProvider subclass | dotted path | None (None masks the name).
_runtime_rerank_providers: dict[str, object] = {}


def register_rerank_provider(name: str, provider) -> None:
    """Register *provider* (a ``RerankProvider`` subclass or a dotted
    path) under *name* at runtime — highest precedence. ``None``/``""``
    masks the name; re-registering overrides."""
    if provider is None or provider == "":
        _runtime_rerank_providers[name] = None
        return
    if isinstance(provider, str):
        _runtime_rerank_providers[name] = provider
        return
    if inspect.isclass(provider) and issubclass(provider, RerankProvider):
        _runtime_rerank_providers[name] = provider
        return
    raise TypeError(
        f"register_rerank_provider({name!r}) expects a RerankProvider "
        f"subclass or a dotted path string, got {provider!r}"
    )


def registered_rerank_providers() -> dict:
    """Effective ``name → class-or-dotted-path`` mapping (built-ins ←
    ``STAPEL_AGENT["RERANK_PROVIDERS"]`` ← runtime; falsy entries
    dropped)."""
    from ..conf import agent_settings

    merged = {
        **BUILTIN_RERANK_PROVIDERS,
        **(agent_settings.RERANK_PROVIDERS or {}),
        **_runtime_rerank_providers,
    }
    return {name: target for name, target in merged.items() if target}


def _reset_runtime_rerank_providers() -> None:
    """Tests only."""
    _runtime_rerank_providers.clear()


__all__ = [
    "BUILTIN_RERANK_PROVIDERS",
    "register_rerank_provider",
    "registered_rerank_providers",
]
