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
        # Per-source cache-by-prompt toggle: a repeated identical
        # prompt+system_prompt within CACHE_TTL returns the stored response
        # without calling the provider.
        "CACHE_LOOKUP": {"llm_facade": False, "translate": True},
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
