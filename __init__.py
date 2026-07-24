"""stapel-agent — LLM facade: completion (text + vision), translation,
transcription, diarization, summarization, embeddings, image generation,
prompt cache/ledger.

Public API (lazily resolved, PEP 562 — importing this package pulls in
no Django code until an attribute is actually accessed):

    agent_settings              — the ``STAPEL_AGENT`` settings namespace
    complete                    — raw LLM completion (cache + PromptLog ledger)
    translate                   — key-value translation flow
    transcribe                  — speech-to-text through the STT router
    diarize                     — speaker diarization through the configured backend
    summarize                   — text/transcript summarization (map-reduce)
    embed                       — text embeddings through the configured backend
    generate_image              — image generation through the configured backend
    LlmProvider                 — base class for custom LLM backends
    ProviderResult              — completion text + token accounting dataclass
    SttProvider                 — base class for custom STT backends
    AudioRef                    — url|path|bytes audio reference
    ImageRef                    — url|bytes vision-input reference
    NormalizedTranscript        — canonical STT output schema
    DiarizationProvider         — base class for custom diarization backends
    NormalizedDiarization       — canonical diarization output schema
    EmbeddingProvider           — base class for custom embedding backends
    NormalizedEmbeddings        — canonical embeddings output schema
    ImageGenProvider            — base class for custom image-gen backends
    GeneratedImage              — one generated image (url and/or data_b64)
    CachePolicy                 — base class for custom prompt-cache policies
    register_provider           — runtime LLM provider registration
    registered_providers        — effective LLM provider mapping
    register_stt_provider       — runtime STT provider registration
    registered_stt_providers    — effective STT provider mapping
    register_diarization_provider   — runtime diarization provider registration
    registered_diarization_providers — effective diarization provider mapping
    register_embedding_provider — runtime embedding provider registration
    registered_embedding_providers — effective embedding provider mapping
    register_image_provider     — runtime image provider registration
    registered_image_providers  — effective image provider mapping
"""

__all__ = [
    "AudioRef",
    "CachePolicy",
    "DiarizationProvider",
    "EmbeddingProvider",
    "GeneratedImage",
    "ImageGenProvider",
    "ImageRef",
    "LlmProvider",
    "NormalizedDiarization",
    "NormalizedEmbeddings",
    "NormalizedTranscript",
    "ProviderResult",
    "SttProvider",
    "agent_settings",
    "complete",
    "diarize",
    "embed",
    "generate_image",
    "register_diarization_provider",
    "register_embedding_provider",
    "register_image_provider",
    "register_provider",
    "register_stt_provider",
    "registered_diarization_providers",
    "registered_embedding_providers",
    "registered_image_providers",
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
    "diarize": (".services", "diarize"),
    "summarize": (".services", "summarize"),
    "embed": (".services", "embed"),
    "generate_image": (".services", "generate_image"),
    "LlmProvider": (".providers.base", "LlmProvider"),
    "ProviderResult": (".providers.base", "ProviderResult"),
    "SttProvider": (".stt.base", "SttProvider"),
    "AudioRef": (".stt.base", "AudioRef"),
    "NormalizedTranscript": (".stt.base", "NormalizedTranscript"),
    "DiarizationProvider": (".diarization.base", "DiarizationProvider"),
    "NormalizedDiarization": (".diarization.base", "NormalizedDiarization"),
    "EmbeddingProvider": (".embeddings.base", "EmbeddingProvider"),
    "NormalizedEmbeddings": (".embeddings.base", "NormalizedEmbeddings"),
    "ImageRef": (".images.base", "ImageRef"),
    "ImageGenProvider": (".images.base", "ImageGenProvider"),
    "GeneratedImage": (".images.base", "GeneratedImage"),
    "CachePolicy": (".cache", "CachePolicy"),
    "register_provider": (".providers", "register_provider"),
    "registered_providers": (".providers", "registered_providers"),
    "register_stt_provider": (".stt", "register_stt_provider"),
    "registered_stt_providers": (".stt", "registered_stt_providers"),
    "register_diarization_provider": (
        ".diarization",
        "register_diarization_provider",
    ),
    "registered_diarization_providers": (
        ".diarization",
        "registered_diarization_providers",
    ),
    "register_embedding_provider": (".embeddings", "register_embedding_provider"),
    "registered_embedding_providers": (
        ".embeddings",
        "registered_embedding_providers",
    ),
    "register_image_provider": (".images", "register_image_provider"),
    "registered_image_providers": (".images", "registered_image_providers"),
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
