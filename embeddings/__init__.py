"""Embedding provider registry — same open merge semantics as the LLM /
STT / image / diarization registries.

Three layers, merged in increasing precedence:

1. ``BUILTIN_EMBEDDING_PROVIDERS`` — the adapters shipped with this
   package;
2. ``STAPEL_AGENT["EMBEDDING_PROVIDERS"]`` — host settings, merged OVER
   the built-ins (add one name, never restate the rest; ``None``/``""``
   removes a name);
3. runtime ``register_embedding_provider()`` — for app-layer
   ``AppConfig.ready()`` registration.

Django-free at import time; settings are read when
``registered_embedding_providers()`` is called.
"""
from __future__ import annotations

import inspect

from .base import EmbeddingProvider

BUILTIN_EMBEDDING_PROVIDERS = {
    "openai-embeddings": (
        "stapel_agent.embeddings.providers.openai_compat.OpenAIEmbeddingsProvider"
    ),
    "embeddings-http": (
        "stapel_agent.embeddings.providers.http_server.HttpServerEmbeddingsProvider"
    ),
}

# name → EmbeddingProvider subclass | dotted path | None (None masks the name).
_runtime_embedding_providers: dict[str, object] = {}


def register_embedding_provider(name: str, provider) -> None:
    """Register *provider* (an ``EmbeddingProvider`` subclass or a dotted
    path) under *name* at runtime — highest precedence. ``None``/``""``
    masks the name; re-registering overrides."""
    if provider is None or provider == "":
        _runtime_embedding_providers[name] = None
        return
    if isinstance(provider, str):
        _runtime_embedding_providers[name] = provider
        return
    if inspect.isclass(provider) and issubclass(provider, EmbeddingProvider):
        _runtime_embedding_providers[name] = provider
        return
    raise TypeError(
        f"register_embedding_provider({name!r}) expects an EmbeddingProvider "
        f"subclass or a dotted path string, got {provider!r}"
    )


def registered_embedding_providers() -> dict:
    """Effective ``name → class-or-dotted-path`` mapping (built-ins ←
    ``STAPEL_AGENT["EMBEDDING_PROVIDERS"]`` ← runtime; falsy entries
    dropped)."""
    from ..conf import agent_settings

    merged = {
        **BUILTIN_EMBEDDING_PROVIDERS,
        **(agent_settings.EMBEDDING_PROVIDERS or {}),
        **_runtime_embedding_providers,
    }
    return {name: target for name, target in merged.items() if target}


def _reset_runtime_embedding_providers() -> None:
    """Tests only."""
    _runtime_embedding_providers.clear()


__all__ = [
    "BUILTIN_EMBEDDING_PROVIDERS",
    "register_embedding_provider",
    "registered_embedding_providers",
]
