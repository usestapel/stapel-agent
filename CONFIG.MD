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
| MODELS | env | size→model-name map for the default (Anthropic) providers (`small`/`medium`/`large`/`xlarge`). | no | claude-haiku-4-5 / claude-sonnet-5 / claude-opus-4-8 / claude-fable-5 |
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
| STT_DOWNLOAD_MAX_BYTES | env | byte cap on an audio-URL download; the transfer aborts mid-stream when crossed. | no | 134217728 |
| STT_DOWNLOAD_TIMEOUT | env | per-socket connect/read timeout for one hop of that download. | no | 30.0 |
| STT_DOWNLOAD_TOTAL_DEADLINE | env | ceiling (seconds) on the whole download incl. redirects; a per-call timeout may only lower it. | no | 300.0 |
| STT_DOWNLOAD_ALLOWED_HOSTS | env | exact-host allowlist for audio URLs, applied to every redirect hop ([] = any public host). | no | [] |
| STT_PRICING_MODULES | env | overlay merged OVER built-in STT rate cards ({provider name: dotted path to a module exposing `estimate_cost()`}); None/"" declares a provider unpriced. | no | {} |
| STT_MODEL_CONFIGS | env | overlay merged OVER the built-in STT model-config catalog ({config id: `ModelConfig`}); None/"" removes a shipped config. | no | {} |
| WHISPER_BASE_URL | env | OpenAI-compatible Whisper endpoint. | no | (empty) |
| WHISPER_API_KEY | vault | Whisper endpoint key (optional for self-hosted). | no | (empty) |
| WHISPER_MODEL | env | Whisper model name. | no | whisper-1 |
| ELEVENLABS_API_KEY | vault | ElevenLabs Scribe key. | no | (empty) |
| ELEVENLABS_STT_URL | env | ElevenLabs speech-to-text URL. | no | https://api.elevenlabs.io/v1/speech-to-text |
| ELEVENLABS_STT_MODEL | env | ElevenLabs STT model. | no | scribe_v2 |
| ASSEMBLYAI_API_KEY | vault | AssemblyAI key. | no | (empty) |
| ASSEMBLYAI_BASE_URL | env | AssemblyAI base URL. | no | https://api.assemblyai.com |
| ASSEMBLYAI_MODEL | env | AssemblyAI model. | no | universal |

### Diarization — speaker turns (source: env / vault for keys)

| Key | Source | Purpose | Required | Default |
|-----|--------|---------|----------|---------|
| DIARIZATION_PROVIDERS | env | overlay merged OVER built-in diarization providers. | no | {} |
| DEFAULT_DIARIZATION_PROVIDER | env | diarization provider when a request names none. | no | pyannote-http |
| DIARIZATION_TIMEOUT | env | hard cap (seconds) on one diarization request. | no | 1800 |
| PYANNOTE_BASE_URL | env | self-hosted pyannote wrapper (multipart POST {base}/diarize). | no | (empty) |
| PYANNOTE_API_KEY | vault | bearer for that SELF-HOSTED service (often none). | no | (empty) |
| PYANNOTEAI_API_KEY | vault | pyannoteAI **cloud** key — a different service from the self-host bearer above; never share one setting between them. | no | (empty) |
| PYANNOTEAI_BASE_URL | env | pyannoteAI cloud job API base. | no | https://api.pyannote.ai/v1 |
| PYANNOTEAI_MODEL | env | cloud model pin (`precision-2` flagship; the open-weights ladder is a different quality point). | no | precision-2 |
| PYANNOTEAI_EXCLUSIVE | env | ask the cloud for the non-overlapping speaker layer. | no | true |

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
| CACHE_ALLOW_UNSCOPED | env | sources declared non-personal, so a call without user_id may use the shared cache; empty = an unscoped call skips the cache. | no | [] |
| PROMPT_LOG_RETENTION_DAYS | env | age after which `purge_prompt_logs` scrubs a ledger row's text (counters kept); None = no limit. | no | 90 |
| MODEL_SIZE_CEILING_ENTITLEMENT | env | entitlement key (STAPEL_BILLING plan catalog) that caps how far up the `MODELS` size ladder a caller's plan reaches; unset = seam closed, no billing lookup at all. | no | (none) |
| CACHE_POLICY | env | dotted path to a CachePolicy subclass (the cache seam). | no | stapel_agent.cache.PromptLogCachePolicy |
