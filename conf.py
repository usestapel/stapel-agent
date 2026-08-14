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
        # Audio-download guards (AudioRef.read_bytes → stapel_core.net).
        # The URL in an AudioRef comes from the request payload, so this is
        # an SSRF sink and a memory sink: a caller must not be able to make
        # a worker hold an arbitrary body for an arbitrary time. Both are
        # ceilings — a per-call timeout argument may lower them, never raise
        # them.
        "STT_DOWNLOAD_MAX_BYTES": 128 * 1024 * 1024,
        # Per-socket connect/read timeout for one hop.
        "STT_DOWNLOAD_TIMEOUT": 30.0,
        # Whole download, redirects included. A per-socket timeout alone
        # bounds nothing against a server that trickles one byte per window.
        "STT_DOWNLOAD_TOTAL_DEADLINE": 300.0,
        # Optional exact-host allowlist for audio URLs ([] = any public
        # host). Deployments that only ever pass presigned URLs from their
        # own object store should list that store here: it turns the
        # SSRF-shaped surface into a fetch of one known origin.
        "STT_DOWNLOAD_ALLOWED_HOSTS": [],
        # Overlay merged OVER stt.pricing.BUILTIN_STT_PRICING_MODULES —
        # {provider name: dotted path to a module exposing estimate_cost()}.
        # A host that registered its own STT adapter registers its rate card
        # here, under the SAME name; None/"" declares a provider unpriced.
        "STT_PRICING_MODULES": {},
        # Overlay merged OVER stt.model_configs.BUILTIN_STT_MODEL_CONFIGS —
        # {config id: ModelConfig}. Same merge semantics as STT_PROVIDERS:
        # add one profile without restating the shipped ones, None/"" removes.
        "STT_MODEL_CONFIGS": {},
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
        # ── Diarization (speaker turns) ─────────────────────────
        # Overlay merged OVER diarization.BUILTIN_DIARIZATION_PROVIDERS
        # (pyannote-http) — same merge semantics as PROVIDERS/STT_PROVIDERS.
        "DIARIZATION_PROVIDERS": {},
        "DEFAULT_DIARIZATION_PROVIDER": "pyannote-http",
        # Hard cap (seconds) on one diarization request.
        "DIARIZATION_TIMEOUT": 1800,
        # Self-hosted pyannote wrapper service (plain HTTP multipart
        # POST {base}/diarize). Key optional — self-hosted often has none.
        "PYANNOTE_BASE_URL": "",
        "PYANNOTE_API_KEY": "",
        # pyannoteAI CLOUD (api.pyannote.ai — billed job API). A SEPARATE
        # credential from the self-host bearer above on purpose: same
        # vendor name, different service, and one shared key setting
        # silently sends a self-host token to the cloud (or back).
        # MODEL defaults to the flagship precision-2; EXCLUSIVE asks for
        # the non-overlapping speaker layer (per-call override lives in
        # provider_options).
        "PYANNOTEAI_API_KEY": "",
        "PYANNOTEAI_BASE_URL": "https://api.pyannote.ai/v1",
        "PYANNOTEAI_MODEL": "precision-2",
        "PYANNOTEAI_EXCLUSIVE": True,
        # ── Embeddings ──────────────────────────────────────────
        # Overlay merged OVER embeddings.BUILTIN_EMBEDDING_PROVIDERS
        # (openai-embeddings / embeddings-http) — same merge semantics.
        "EMBEDDING_PROVIDERS": {},
        "DEFAULT_EMBEDDING_PROVIDER": "openai-embeddings",
        # Hard cap (seconds) on one embeddings request.
        "EMBEDDINGS_TIMEOUT": 120,
        # OpenAI-compatible /embeddings endpoint (base URL includes /v1,
        # like OPENAI_COMPAT_BASE_URL). Both fall back to the
        # OPENAI_COMPAT_* pair, so a host already on an OpenAI-flavoured
        # stack configures nothing extra.
        "EMBEDDINGS_BASE_URL": "",
        "EMBEDDINGS_API_KEY": "",
        "EMBEDDINGS_MODEL": "text-embedding-3-small",
        # Generic self-hosted embeddings server (POST {base}/embed,
        # {"texts": [...]} → {"vectors": [[...]]}; model fixed
        # server-side — bge-m3 / multilingual-e5 class). Key optional.
        "EMBEDDINGS_HTTP_BASE_URL": "",
        "EMBEDDINGS_HTTP_API_KEY": "",
        # ── Rerank ──────────────────────────────────────────────
        # Overlay merged OVER rerank.BUILTIN_RERANK_PROVIDERS
        # (deepinfra-rerank / rerank-http) — same merge semantics.
        "RERANK_PROVIDERS": {},
        "DEFAULT_RERANK_PROVIDER": "deepinfra-rerank",
        # Hard cap (seconds) on one rerank request.
        "RERANK_TIMEOUT": 120,
        # DeepInfra inference API (POST {base}/inference/{model}); the
        # base URL includes /v1. RERANK_API_KEY is the DeepInfra key —
        # app layers alias their DEEPINFRA_API_KEY onto it.
        "RERANK_BASE_URL": "https://api.deepinfra.com/v1",
        "RERANK_API_KEY": "",
        "RERANK_MODEL": "Qwen/Qwen3-Reranker-8B",
        # Generic self-hosted reranker speaking the TEI /rerank dialect
        # (POST {base}/rerank, {"query", "texts"} → [{"index", "score"}];
        # model fixed server-side). Keyless — the self-host fallback.
        "RERANK_HTTP_BASE_URL": "",
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
        # Sources whose content is declared NON-personal and may therefore
        # use the shared, tenant-less cache when a call supplies no
        # user_id. Empty = fail closed: an unscoped call skips the cache
        # rather than risk serving one tenant's answer to another
        # (AGENT-02). Add "translate" here only if the strings translated
        # in that deployment are UI copy, never user content.
        "CACHE_ALLOW_UNSCOPED": [],
        # Days after which a PromptLog row's TEXT (prompt, system prompt,
        # response, error) is scrubbed by ``purge_prompt_logs`` — the row
        # and its token counters stay for accounting. None = no retention
        # limit, and the host owes the regulator an explanation.
        "PROMPT_LOG_RETENTION_DAYS": 90,
        # Dotted path to a stapel_agent.cache.CachePolicy subclass — the
        # cache seam. The default implements the PromptLog+TTL behaviour;
        # swap for Redis/no-op without forking.
        "CACHE_POLICY": "stapel_agent.cache.PromptLogCachePolicy",
    },
    import_strings=("CACHE_POLICY",),
)

__all__ = ["agent_settings"]
