"""Settings namespace for stapel-agent.

All configuration is read through ``agent_settings`` (lazily, at call
time) instead of module-level ``os.getenv`` — so tests and host projects
can override any key via ``settings.STAPEL_AGENT``, a flat Django setting
of the same name, or an environment variable::

    STAPEL_AGENT = {
        "DEFAULT_PROVIDER": "openai-compat",
        "OPENAI_COMPAT_BASE_URL": "https://api.deepseek.com/v1",
        "OPENAI_COMPAT_API_KEY": "sk-...",
        "OPENAI_COMPAT_MODELS": {"small": "deepseek-chat"},
    }

``PROVIDERS`` entries are **merged over** the built-in registry
(``stapel_agent.providers.BUILTIN_PROVIDERS``) — adding one custom
provider does not require restating the built-ins, and setting a name to
``None``/``""`` removes it. Values are dotted paths to ``LlmProvider``
subclasses, resolved lazily per request in ``services.get_provider``
(not via ``import_strings`` — an unknown or broken provider must degrade
to a ``status: failure`` response, never an import-time crash).
"""
from stapel_core.conf import AppSettings

agent_settings = AppSettings(
    "STAPEL_AGENT",
    defaults={
        # Size → model-name map used by the default (Anthropic-flavoured)
        # providers. OpenAI-compatible hosts override per-size names via
        # OPENAI_COMPAT_MODELS instead.
        "MODELS": {
            "small": "claude-haiku-4-5-20251001",
            "medium": "claude-sonnet-5",
            "large": "claude-opus-4-8",
        },
        # Overlay merged OVER providers.BUILTIN_PROVIDERS (anthropic /
        # openai-compat / claude-code): add or override entries here,
        # None/"" removes a name. Resolved lazily per request via
        # import_string in services.get_provider(name).
        "PROVIDERS": {},
        "DEFAULT_PROVIDER": "anthropic",
        # Anthropic SDK (read lazily at call time, never frozen at import).
        "ANTHROPIC_API_KEY": "",
        # Any OpenAI-compatible /chat/completions endpoint
        # (OpenAI, DeepSeek, MiMo, GLM, Kimi, ...).
        "OPENAI_COMPAT_BASE_URL": "",
        "OPENAI_COMPAT_API_KEY": "",
        # Optional size → model-name map for the openai-compat provider,
        # e.g. {"small": "gpt-4o-mini", "medium": "gpt-4o"}. Missing sizes
        # fall back to MODELS[size].
        "OPENAI_COMPAT_MODELS": {},
        # Claude Code CLI provider (opt-in only, never the default).
        "CLI_BINARY": "claude",
        "CLI_TIMEOUT": 120,
        "MAX_TOKENS": 4096,
        # ── STT (speech-to-text) ────────────────────────────────
        # Overlay merged OVER stt.BUILTIN_STT_PROVIDERS (whisper-http /
        # elevenlabs / assemblyai) — same merge semantics as PROVIDERS.
        "STT_PROVIDERS": {},
        "DEFAULT_STT_PROVIDER": "whisper-http",
        # Providers tried in order after the default on RETRYABLE failure
        # (fatal errors never fall back).
        "STT_FALLBACK_CHAIN": [],
        # Language matrix: {iso-639-1: [provider names]}. An explicit
        # `provider` in the request wins over this; this wins over
        # DEFAULT_STT_PROVIDER + STT_FALLBACK_CHAIN.
        "STT_LANGUAGE_ROUTES": {},
        # Hard cap (seconds) on one provider's submit+poll cycle.
        "STT_TIMEOUT": 1800,
        # OpenAI-compatible Whisper endpoint (OpenAI API or self-hosted
        # faster-whisper). Key optional — self-hosted often has none.
        "WHISPER_BASE_URL": "",
        "WHISPER_API_KEY": "",
        "WHISPER_MODEL": "whisper-1",
        # ElevenLabs Scribe.
        "ELEVENLABS_API_KEY": "",
        "ELEVENLABS_STT_URL": "https://api.elevenlabs.io/v1/speech-to-text",
        "ELEVENLABS_STT_MODEL": "scribe_v2",
        # AssemblyAI (async submit+poll).
        "ASSEMBLYAI_API_KEY": "",
        "ASSEMBLYAI_BASE_URL": "https://api.assemblyai.com",
        "ASSEMBLYAI_MODEL": "universal",
        # Deepgram (synchronous /v1/listen, raw-bytes body).
        "DEEPGRAM_API_KEY": "",
        "DEEPGRAM_BASE_URL": "https://api.deepgram.com",
        "DEEPGRAM_MODEL": "nova-3",
        # Gladia (async upload+create+poll).
        "GLADIA_API_KEY": "",
        "GLADIA_BASE_URL": "https://api.gladia.io",
        "GLADIA_MODEL": "solaria-1",
        # Soniox (async upload+create+poll+fetch, mandatory cleanup).
        "SONIOX_API_KEY": "",
        "SONIOX_BASE_URL": "https://api.soniox.com",
        "SONIOX_MODEL": "stt-async-v5",
        # Speechmatics (async submit+poll+fetch). Melia 1 exists in
        # EU1/US1 only — the base URL selects the region.
        "SPEECHMATICS_API_KEY": "",
        "SPEECHMATICS_BASE_URL": "https://eu1.asr.api.speechmatics.com",
        "SPEECHMATICS_MODEL": "melia-1",
        # xAI STT (single synchronous multipart POST; the endpoint has NO
        # model parameter — nothing to pin).
        "XAI_API_KEY": "",
        "XAI_STT_URL": "https://api.x.ai/v1/stt",
        # ── Image generation ────────────────────────────────────
        # Overlay merged OVER images.BUILTIN_IMAGE_PROVIDERS
        # (openai-images) — same merge semantics as PROVIDERS/STT_PROVIDERS.
        "IMAGE_PROVIDERS": {},
        "DEFAULT_IMAGE_PROVIDER": "openai-images",
        # OpenAI-compatible /images/generations endpoint. Both fall back
        # to the OPENAI_COMPAT_* pair, so a host already on an
        # OpenAI-flavoured stack configures nothing extra.
        "IMAGES_BASE_URL": "",
        "IMAGES_API_KEY": "",
        # Optional model name ("gpt-image-1", "flux-schnell", ...);
        # empty = omitted from the request (single-model servers).
        "IMAGES_MODEL": "",
        # Per-source cache-by-prompt toggle: a repeated identical
        # prompt+system_prompt within CACHE_TTL returns the stored response
        # without calling the provider. Sources missing from the dict
        # default to off.
        "CACHE_LOOKUP": {"llm_facade": False, "translate": True, "summarize": False},
        # Seconds; cached rows older than this are ignored (7 days).
        "CACHE_TTL": 604800,
        # Dotted path to a stapel_agent.cache.CachePolicy subclass — the
        # cache seam. The default implements the PromptLog+TTL behaviour;
        # swap for Redis/no-op without forking.
        "CACHE_POLICY": "stapel_agent.cache.PromptLogCachePolicy",
    },
    import_strings=("CACHE_POLICY",),
)

__all__ = ["agent_settings"]
