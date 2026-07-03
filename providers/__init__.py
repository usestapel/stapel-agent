"""LLM provider registry — the module's flagship fork-free seam.

Three layers, merged in increasing precedence (same merge semantics as
stapel-notifications' routing ``TYPES``, deliberately NOT the
replace-the-whole-dict style of billing's ``PAYMENT_PROVIDER``):

1. ``BUILTIN_PROVIDERS`` — the providers shipped with this package;
2. ``STAPEL_AGENT["PROVIDERS"]`` — host settings, merged OVER the
   built-ins: adding one custom provider never requires restating the
   three built-ins, and setting a name to ``None``/``""`` removes it::

       STAPEL_AGENT = {
           "PROVIDERS": {
               "acme": "myproject.llm.AcmeProvider",   # add
               "claude-code": None,                     # remove a built-in
           },
       }

3. runtime registrations — for app-layer packages that want to register
   from their own ``AppConfig.ready()``::

       from stapel_agent.providers import register_provider

       register_provider("acme", AcmeProvider)                  # class …
       register_provider("acme", "myproject.llm.AcmeProvider")  # … or path

``services.get_provider(name)`` resolves against the effective mapping
(``registered_providers()``) and instantiates lazily per request.

This module is Django-free at import time; settings are only read when
``registered_providers()`` is called.
"""
from __future__ import annotations

import inspect

from .base import LlmProvider

BUILTIN_PROVIDERS = {
    "anthropic": "stapel_agent.providers.anthropic.AnthropicProvider",
    "openai-compat": "stapel_agent.providers.openai_compat.OpenAICompatProvider",
    "claude-code": "stapel_agent.providers.claude_cli.ClaudeCodeCLIProvider",
}

# name → LlmProvider subclass | dotted path | None (None masks the name).
_runtime_providers: dict[str, object] = {}


def register_provider(name: str, provider) -> None:
    """Register *provider* (an ``LlmProvider`` subclass or a dotted path)
    under *name* at runtime — highest precedence, meant for app-layer
    ``AppConfig.ready()``. Re-registering a name overrides it;
    ``None``/``""`` masks it (removes it from the effective mapping).
    """
    if provider is None or provider == "":
        _runtime_providers[name] = None
        return
    if isinstance(provider, str):
        _runtime_providers[name] = provider
        return
    if inspect.isclass(provider) and issubclass(provider, LlmProvider):
        _runtime_providers[name] = provider
        return
    raise TypeError(
        f"register_provider({name!r}) expects an LlmProvider subclass or a "
        f"dotted path string, got {provider!r}"
    )


def registered_providers() -> dict:
    """The effective ``name → class-or-dotted-path`` mapping.

    Built-ins ← ``STAPEL_AGENT["PROVIDERS"]`` ← runtime registrations,
    with ``None``/``""`` entries dropped at every layer.
    """
    from ..conf import agent_settings

    merged = {
        **BUILTIN_PROVIDERS,
        **(agent_settings.PROVIDERS or {}),
        **_runtime_providers,
    }
    return {name: target for name, target in merged.items() if target}


def _reset_runtime_providers() -> None:
    """Tests only."""
    _runtime_providers.clear()


__all__ = [
    "BUILTIN_PROVIDERS",
    "register_provider",
    "registered_providers",
]
