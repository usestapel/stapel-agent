# CONFIG.MD — stapel-agent

Config registry for **stapel-agent** (`static-scaffold-and-config.md` §2). One
row per configuration key the module reads, its **source** (`env` = the process
environment / the `STAPEL_AGENT` settings dict; `vault` = a secret that belongs
in the `stapel_core.secrets` provider seam), what it is for, whether it is
required, and its default.

Every key is read through `agent_settings` (`stapel_agent.conf.AppSettings`,
namespace `STAPEL_AGENT`). Resolution order per key: `settings.STAPEL_AGENT`
dict → a flat Django setting of the same name → environment variable → the
default below. At scaffold time `assemble_scaffold` aggregates this file (under
the `## stapel-agent` owner heading) into the project's root CONFIG.MD; the keys
are read *inside* the lib, so they are library-owned and exempt from CFG003 in a
consuming project.

## stapel-agent

### LLM completion (source: env / vault for keys)

| Key | Source | Purpose | Required | Default |
|-----|--------|---------|----------|---------|
| MODELS | env | size→model-name map for the default (Anthropic) providers (`small`/`medium`/`large`). | no | claude-haiku-4-5 / claude-sonnet-5 / claude-opus-4-8 |
| PROVIDERS | env | overlay merged OVER the built-in provider registry (dotted paths); None/"" removes a name. | no | {} |
| DEFAULT_PROVIDER | env | provider used when a request names none. | no | anthropic |
| ANTHROPIC_API_KEY | vault | Anthropic SDK key (read lazily at call time). | no | (empty) |
| OPENAI_COMPAT_BASE_URL | env | any OpenAI-compatible /chat/completions endpoint. | no | (empty) |
| OPENAI_COMPAT_API_KEY | vault | key for the OpenAI-compatible endpoint. | no | (empty) |
| OPENAI_COMPAT_MODELS | env | size→model-name map for the openai-compat provider. | no | {} |
| CLI_BINARY | env | Claude Code CLI binary (opt-in provider). | no | claude |
| CLI_TIMEOUT | env | CLI provider timeout, seconds. | no | 120 |
| MAX_TOKENS | env | max output tokens per completion. | no | 4096 |

### Speech-to-text (source: env / vault for keys)

| Key | Source | Purpose | Required | Default |
|-----|--------|---------|----------|---------|
| STT_PROVIDERS | env | overlay merged OVER built-in STT providers. | no | {} |
| DEFAULT_STT_PROVIDER | env | STT provider when a request names none. | no | whisper-http |
| STT_FALLBACK_CHAIN | env | providers tried in order after the default on retryable failure. | no | [] |
| STT_LANGUAGE_ROUTES | env | {iso-639-1: [provider names]} language routing matrix. | no | {} |
| STT_TIMEOUT | env | hard cap (seconds) on one provider's submit+poll cycle. | no | 1800 |
| WHISPER_BASE_URL | env | OpenAI-compatible Whisper endpoint. | no | (empty) |
| WHISPER_API_KEY | vault | Whisper endpoint key (optional for self-hosted). | no | (empty) |
| WHISPER_MODEL | env | Whisper model name. | no | whisper-1 |
| ELEVENLABS_API_KEY | vault | ElevenLabs Scribe key. | no | (empty) |
| ELEVENLABS_STT_URL | env | ElevenLabs speech-to-text URL. | no | https://api.elevenlabs.io/v1/speech-to-text |
| ELEVENLABS_STT_MODEL | env | ElevenLabs STT model. | no | scribe_v2 |
| ASSEMBLYAI_API_KEY | vault | AssemblyAI key. | no | (empty) |
| ASSEMBLYAI_BASE_URL | env | AssemblyAI base URL. | no | https://api.assemblyai.com |
| ASSEMBLYAI_MODEL | env | AssemblyAI model. | no | universal |

### Image generation (source: env / vault for keys)

| Key | Source | Purpose | Required | Default |
|-----|--------|---------|----------|---------|
| IMAGE_PROVIDERS | env | overlay merged OVER built-in image providers. | no | {} |
| DEFAULT_IMAGE_PROVIDER | env | image provider when a request names none. | no | openai-images |
| IMAGES_BASE_URL | env | OpenAI-compatible /images/generations endpoint (falls back to OPENAI_COMPAT_*). | no | (empty) |
| IMAGES_API_KEY | vault | image endpoint key (falls back to OPENAI_COMPAT_API_KEY). | no | (empty) |
| IMAGES_MODEL | env | image model name; empty = omitted from request. | no | (empty) |

### Prompt cache (source: env)

| Key | Source | Purpose | Required | Default |
|-----|--------|---------|----------|---------|
| CACHE_LOOKUP | env | per-source cache-by-prompt toggle {source: bool}. | no | {llm_facade: false, translate: true, summarize: false} |
| CACHE_TTL | env | seconds; cached rows older than this are ignored. | no | 604800 |
| CACHE_POLICY | env | dotted path to a CachePolicy subclass (the cache seam). | no | stapel_agent.cache.PromptLogCachePolicy |
