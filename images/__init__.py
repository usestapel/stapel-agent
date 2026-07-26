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

# The base class is imported inside register_image_provider() — NOT here.
# A module-level ``from .base import ImageGenProvider`` makes this package
# body hold its own module lock while it takes the submodule's; a thread
# importing ``stapel_agent.images.base`` first takes the same two locks in
# the opposite order. Python 3.14 reports that as `_DeadlockError` — it is
# what killed iron-agent's runserver thread at startup (system checks walk
# the registries on django-main-thread while the autoreloader's main
# thread imports the URLconf → 502 until the next restart).

BUILTIN_IMAGE_PROVIDERS = {
    "openai-images": "stapel_agent.images.providers.openai_images.OpenAIImagesProvider",
}

# name → ImageGenProvider subclass | dotted path | None (None masks the name).
_runtime_image_providers: dict[str, object] = {}


def register_image_provider(name: str, provider) -> None:
    """Register *provider* (an ``ImageGenProvider`` subclass or a dotted
    path) under *name* at runtime — highest precedence. ``None``/``""``
    masks the name; re-registering overrides."""
    from .base import ImageGenProvider

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
