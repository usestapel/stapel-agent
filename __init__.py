"""stapel-agent — LLM facade: completion, translation, transcription,
summarization, prompt cache/ledger.

Public API (lazily resolved, PEP 562 — importing this package pulls in
no Django code until an attribute is actually accessed):

    agent_settings            — the ``STAPEL_AGENT`` settings namespace
    complete                  — raw LLM completion (cache + PromptLog ledger)
    translate                 — key-value translation flow
    transcribe                — speech-to-text through the STT router
    summarize                 — text/transcript summarization (map-reduce)
    LlmProvider               — base class for custom LLM backends
    ProviderResult            — completion text + token accounting dataclass
    SttProvider               — base class for custom STT backends
    AudioRef                  — url|path|bytes audio reference
    NormalizedTranscript      — canonical STT output schema
    CachePolicy               — base class for custom prompt-cache policies
    register_provider         — runtime LLM provider registration
    registered_providers      — effective LLM provider mapping
    register_stt_provider     — runtime STT provider registration
    registered_stt_providers  — effective STT provider mapping
"""

__all__ = [
    "AudioRef",
    "CachePolicy",
    "LlmProvider",
    "NormalizedTranscript",
    "ProviderResult",
    "SttProvider",
    "agent_settings",
    "complete",
    "register_provider",
    "register_stt_provider",
    "registered_providers",
    "registered_stt_providers",
    "summarize",
    "transcribe",
    "translate",
]

# name -> (relative module, attribute)
_EXPORTS = {
    "agent_settings": (".conf", "agent_settings"),
    "complete": (".services", "complete"),
    "translate": (".services", "translate"),
    "transcribe": (".services", "transcribe"),
    "summarize": (".services", "summarize"),
    "LlmProvider": (".providers.base", "LlmProvider"),
    "ProviderResult": (".providers.base", "ProviderResult"),
    "SttProvider": (".stt.base", "SttProvider"),
    "AudioRef": (".stt.base", "AudioRef"),
    "NormalizedTranscript": (".stt.base", "NormalizedTranscript"),
    "CachePolicy": (".cache", "CachePolicy"),
    "register_provider": (".providers", "register_provider"),
    "registered_providers": (".providers", "registered_providers"),
    "register_stt_provider": (".stt", "register_stt_provider"),
    "registered_stt_providers": (".stt", "registered_stt_providers"),
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
