"""Image-generation provider registry — third instance of the house
open-registry pattern (LLM ``providers``, ``stt``, now ``images``).

Three layers, merged in increasing precedence:

1. ``BUILTIN_IMAGE_PROVIDERS`` — the adapters shipped with this package;
2. ``STAPEL_AGENT["IMAGE_PROVIDERS"]`` — host settings, merged OVER the
   built-ins (add one name, never restate the rest; ``None``/``""``
   removes a name);
3. runtime ``register_image_provider()`` — for app-layer
   ``AppConfig.ready()`` registration (e.g. a Stability adapter — see
   MODULE.md for the recipe).

Django-free at import time; settings are read when
``registered_image_providers()`` is called.
"""
from __future__ import annotations

import inspect

from .base import ImageGenProvider

BUILTIN_IMAGE_PROVIDERS = {
    "openai-images": "stapel_agent.images.providers.openai_images.OpenAIImagesProvider",
}

# name → ImageGenProvider subclass | dotted path | None (None masks the name).
_runtime_image_providers: dict[str, object] = {}


def register_image_provider(name: str, provider) -> None:
    """Register *provider* (an ``ImageGenProvider`` subclass or a dotted
    path) under *name* at runtime — highest precedence. ``None``/``""``
    masks the name; re-registering overrides."""
    if provider is None or provider == "":
        _runtime_image_providers[name] = None
        return
    if isinstance(provider, str):
        _runtime_image_providers[name] = provider
        return
    if inspect.isclass(provider) and issubclass(provider, ImageGenProvider):
        _runtime_image_providers[name] = provider
        return
    raise TypeError(
        f"register_image_provider({name!r}) expects an ImageGenProvider "
        f"subclass or a dotted path string, got {provider!r}"
    )


def registered_image_providers() -> dict:
    """Effective ``name → class-or-dotted-path`` mapping (built-ins ←
    ``STAPEL_AGENT["IMAGE_PROVIDERS"]`` ← runtime; falsy entries dropped)."""
    from ..conf import agent_settings

    merged = {
        **BUILTIN_IMAGE_PROVIDERS,
        **(agent_settings.IMAGE_PROVIDERS or {}),
        **_runtime_image_providers,
    }
    return {name: target for name, target in merged.items() if target}


def _reset_runtime_image_providers() -> None:
    """Tests only."""
    _runtime_image_providers.clear()


__all__ = [
    "BUILTIN_IMAGE_PROVIDERS",
    "register_image_provider",
    "registered_image_providers",
]
