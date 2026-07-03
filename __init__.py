"""stapel-agent — LLM facade: completion, translation, prompt cache/ledger.

Public API (lazily resolved, PEP 562 — importing this package pulls in
no Django code until an attribute is actually accessed):

    agent_settings        — the ``STAPEL_AGENT`` settings namespace
    complete              — raw LLM completion (cache + PromptLog ledger)
    translate             — key-value translation flow
    LlmProvider           — base class for custom LLM backends
    ProviderResult        — completion text + token accounting dataclass
    CachePolicy           — base class for custom prompt-cache policies
    register_provider     — runtime provider registration (apps.ready())
    registered_providers  — the effective provider registry mapping
"""

__all__ = [
    "CachePolicy",
    "LlmProvider",
    "ProviderResult",
    "agent_settings",
    "complete",
    "register_provider",
    "registered_providers",
    "translate",
]

# name -> (relative module, attribute)
_EXPORTS = {
    "agent_settings": (".conf", "agent_settings"),
    "complete": (".services", "complete"),
    "translate": (".services", "translate"),
    "LlmProvider": (".providers.base", "LlmProvider"),
    "ProviderResult": (".providers.base", "ProviderResult"),
    "CachePolicy": (".cache", "CachePolicy"),
    "register_provider": (".providers", "register_provider"),
    "registered_providers": (".providers", "registered_providers"),
}


def __getattr__(name):
    try:
        module_path, attr = _EXPORTS[name]
    except KeyError:
        raise AttributeError(
            f"module {__name__!r} has no attribute {name!r}"
        ) from None
    from importlib import import_module

    value = getattr(import_module(module_path, __name__), attr)
    globals()[name] = value  # cache: subsequent lookups skip __getattr__
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))
