# stapel-agent

[![CI](https://github.com/usestapel/stapel-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/usestapel/stapel-agent/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/usestapel/stapel-agent/graph/badge.svg)](https://codecov.io/gh/usestapel/stapel-agent)
[![PyPI](https://img.shields.io/pypi/v/stapel-agent.svg)](https://pypi.org/project/stapel-agent/)

> LLM facade — JSON completion, translation, transcription and summarization in front of swappable model/STT providers, with a prompt cache and a token ledger

Part of the [Stapel framework](https://github.com/usestapel) — composable Django apps for building production-grade platforms.

Python port of a prior NestJS service. Same HTTP paths and contracts
(`stapel-translate`'s `AgentProvider` keeps working unchanged), plus a comm
surface so monolith deployments call it in-process without HTTP.

## Installation

```bash
pip install stapel-agent            # core
pip install stapel-agent[anthropic] # + the Anthropic SDK for the default provider
```

## Quick start

```python
# settings.py
INSTALLED_APPS = [
    ...
    'stapel_agent',
]

STAPEL_AGENT = {
    "ANTHROPIC_API_KEY": "sk-ant-...",
}

# urls.py — mount the app under agent/
urlpatterns = [
    ...
    path("agent/", include("stapel_agent.urls")),
]
```

Two surfaces, same contracts:

| Surface | HTTP | comm Function | Does |
|---|---|---|---|
| Complete | `POST /agent/api/llm/complete` | `llm.complete` | JSON completion: `{"prompt", "model": "small\|medium\|large", "system_prompt"?, "provider"?, "images"?}` → parsed JSON in `result`, prose in `comment`. `images` (vision) entries are `{url}` or `{data_b64, mime?}` — OCR, screenshots, photo moderation |
| Translate | `POST /agent/api/llm/translate` | `llm.translate` | `{"from"/"from_lang", "to", "entries": {key: text}}` → `{key: translated}` (cached by prompt) |
| Transcribe | `POST /agent/api/llm/transcribe` | `llm.transcribe` | `{"audio_url", "language"?, "diarization"?, "provider"?, "timeout_seconds"?}` → a normalized transcript (words, utterances, speakers, timings) via the STT router |
| Summarize | `POST /agent/api/llm/summarize` | `llm.summarize` | exactly one of `text` / `transcript` (+ `language`?, `model`?, `provider`?) → Markdown `summary` + aggregated `usage`; long inputs are map-reduced |
| Generate image | `POST /agent/api/llm/generate-image` | `llm.generate_image` | `{"prompt", "size"?, "n"? (1-10), "provider"?}` → raw provider results `[{url? \| data_b64?, mime}]` — storing them in a CDN/asset library is the caller's job |

```bash
# HTTP (service-to-service: X-API-KEY, or a staff session)
POST /agent/api/llm/complete   {"prompt": "...", "model": "small|medium|large",
                                "provider"?: "...", "system_prompt"?: "...",
                                "images"?: [{"url": "https://..."} | {"data_b64": "...", "mime"?: "image/webp"}]}
POST /agent/api/llm/translate  {"from": "auto", "to": "de", "entries": {"key": "text"}}
POST /agent/api/llm/transcribe {"audio_url": "https://...presigned...", "language"?: "en",
                                "diarization"?: true, "provider"?: "elevenlabs"}
POST /agent/api/llm/summarize  {"text": "..."} | {"transcript": {...llm.transcribe output...}}
POST /agent/api/llm/generate-image {"prompt": "a cat", "size"?: "1024x1024", "n"?: 2}
```

```python
# comm (in-process in a monolith, transport chosen by STAPEL_COMM)
from stapel_core.comm import call

call("llm.complete", {"prompt": "...", "model": "small"})
call("llm.complete", {"prompt": "what is on this?", "model": "small",
                      "images": [{"url": "https://..."}]})  # url or data_b64 — never raw bytes
call("llm.translate", {"from_lang": "auto", "to": "de", "entries": {...}})
call("llm.transcribe", {"audio_url": "https://..."})  # URLs only — never raw bytes
call("llm.summarize", {"transcript": result["transcript"]})
call("llm.generate_image", {"prompt": "a cat", "n": 2})
```

LLM failures are **HTTP 200** with
`{"status": "failure", "reason": ...}` — 4xx/5xx are reserved for request
validation and auth. Successful completions return the parsed JSON in
`result`, prose around it in `comment`, and snake_case `usage`
(`input_tokens` / `output_tokens`).

Every provider call writes a `PromptLog` row: model, size, source, status,
duration and the full token ledger (input / output / thinking / cache-read /
cache-write) — per-user and per-source cost accounting needs no other table.
Transcriptions land there too (`source=transcribe`, `model` = STT provider
name, token columns NULL, the fallback walk in `metadata.attempts`); each
summarize pass is a normal LLM row (`source=summarize`); image generations
log as `source=generate_image` with `{count, mimes, bytes_total}` in
metadata — image bytes never touch the ledger, and multimodal completions
never collide with the text-keyed prompt cache.

Transcription routes through an STT provider chain — explicit `provider` in
the request beats `STT_LANGUAGE_ROUTES[lang]`, which beats
`DEFAULT_STT_PROVIDER` + `STT_FALLBACK_CHAIN` — and falls back only on
transient failures (429/5xx/timeouts); bad audio or auth errors never
retry on another provider.

## Settings — `STAPEL_AGENT`

| Key | Default | Meaning |
|---|---|---|
| `MODELS` | `{"small": "claude-haiku-4-5-20251001", "medium": "claude-sonnet-5", "large": "claude-opus-4-8"}` | Size → model-name map |
| `PROVIDERS` | `{}` | Overlay **merged over** the built-in registry (anthropic / openai-compat / claude-code) — add/override entries, `None` removes one; resolved lazily per request |
| `DEFAULT_PROVIDER` | `"anthropic"` | Provider used when a request names none |
| `ANTHROPIC_API_KEY` | `""` | Key for the Anthropic SDK provider (read lazily) |
| `OPENAI_COMPAT_BASE_URL` | `""` | Base URL of any OpenAI-compatible endpoint |
| `OPENAI_COMPAT_API_KEY` | `""` | Bearer token for that endpoint |
| `OPENAI_COMPAT_MODELS` | `{}` | Optional size → model map for openai-compat (missing sizes fall back to `MODELS`) |
| `CLI_BINARY` | `"claude"` | Claude Code CLI binary (opt-in provider only) |
| `CLI_TIMEOUT` | `120` | Provider timeout, seconds |
| `MAX_TOKENS` | `4096` | Completion token cap |
| `STT_PROVIDERS` | `{}` | Overlay **merged over** the built-in STT registry (whisper-http / elevenlabs / assemblyai) — same semantics as `PROVIDERS` |
| `DEFAULT_STT_PROVIDER` | `"whisper-http"` | STT provider used when a request pins none and no language route matches |
| `STT_FALLBACK_CHAIN` | `[]` | STT providers tried in order after the default — on transient failure only |
| `STT_LANGUAGE_ROUTES` | `{}` | `{"ru": ["gigaam", "whisper-http"], ...}` language matrix (beats the default chain) |
| `STT_TIMEOUT` | `1800` | Hard cap (seconds) on one STT provider's submit+poll cycle |
| `WHISPER_BASE_URL` / `WHISPER_API_KEY` / `WHISPER_MODEL` | `""` / `""` / `"whisper-1"` | OpenAI-compatible Whisper endpoint — the OpenAI API or self-hosted faster-whisper (key optional) |
| `ELEVENLABS_API_KEY` / `ELEVENLABS_STT_URL` / `ELEVENLABS_STT_MODEL` | `""` / Scribe URL / `"scribe_v2"` | ElevenLabs Scribe |
| `ASSEMBLYAI_API_KEY` / `ASSEMBLYAI_BASE_URL` / `ASSEMBLYAI_MODEL` | `""` / `"https://api.assemblyai.com"` / `"universal"` | AssemblyAI (async submit+poll) |
| `IMAGE_PROVIDERS` | `{}` | Overlay **merged over** the built-in image registry (openai-images) — same semantics as `PROVIDERS` |
| `DEFAULT_IMAGE_PROVIDER` | `"openai-images"` | Image provider used when a request pins none |
| `IMAGES_BASE_URL` / `IMAGES_API_KEY` | `""` / `""` | OpenAI-compatible `/images/generations` endpoint + key; both fall back to the `OPENAI_COMPAT_*` pair |
| `IMAGES_MODEL` | `""` | Optional model name (`gpt-image-1`, ...); empty = omitted from the request |
| `CACHE_LOOKUP` | `{"llm_facade": False, "translate": True, "summarize": False}` | Per-source cache-by-prompt toggle (used by the default cache policy) |
| `CACHE_TTL` | `604800` | Cache window in seconds (7 days); older rows are ignored (default policy) |
| `CACHE_POLICY` | `"stapel_agent.cache.PromptLogCachePolicy"` | Dotted path to a `CachePolicy` subclass — swap the prompt cache (Redis, no-op, ...) without forking |

## Provider matrix

| Name | Class | Backend | Needs |
|---|---|---|---|
| `anthropic` (default) | `providers.anthropic.AnthropicProvider` | Anthropic SDK | `anthropic` extra + `ANTHROPIC_API_KEY` |
| `openai-compat` | `providers.openai_compat.OpenAICompatProvider` | Any `/chat/completions` dialect: OpenAI, DeepSeek, MiMo, GLM, Kimi | `OPENAI_COMPAT_BASE_URL` (+ key) |
| `claude-code` | `providers.claude_cli.ClaudeCodeCLIProvider` | Spawns `claude -p ... --output-format json` | The CLI in the host image |

**No CLI in any default path.** `claude-code` is strictly opt-in: it exists for
hosts that ship the Claude Code CLI in their image and want the CLI to handle
its own authentication (`provider: "claude-code"` per request, or
`DEFAULT_PROVIDER` override). There is no OAuth credential
reading and no background token-refresh — that plumbing was deliberately
dropped.

### Adding, overriding and removing providers (merge semantics)

`STAPEL_AGENT["PROVIDERS"]` is an **overlay merged over the built-ins**, not a
replacement dict — adding your provider never requires restating the three
shipped ones, and setting a name to `None` removes it:

```python
# settings.py — one line per change, built-ins stay available
STAPEL_AGENT = {
    "PROVIDERS": {
        "acme": "myproject.llm.AcmeProvider",  # add a custom backend
        "claude-code": None,                    # remove a built-in
    },
    "DEFAULT_PROVIDER": "acme",
}
```

Or register at runtime from your app's `AppConfig.ready()` (highest
precedence):

```python
from stapel_agent import register_provider

register_provider("acme", AcmeProvider)  # class or dotted path
```

A custom backend is just a `stapel_agent.LlmProvider` subclass returning a
`ProviderResult`. Django system checks (`stapel_agent.E001/W001/W002`) flag a
`DEFAULT_PROVIDER` that is not in the effective registry, unimportable dotted
paths and non-`LlmProvider` entries at startup. See [MODULE.md](MODULE.md)
for the full extension-point map.

## STT provider matrix

| Name | Class | Backend | Needs |
|---|---|---|---|
| `whisper-http` (default) | `stt.providers.whisper_http.WhisperHttpProvider` | OpenAI Whisper API **or** any self-hosted server speaking the same dialect (faster-whisper, whisper.cpp); accepts url/path/bytes refs | `WHISPER_BASE_URL` (+ key for OpenAI) |
| `elevenlabs` | `stt.providers.elevenlabs.ElevenLabsProvider` | ElevenLabs Scribe (synchronous multipart), diarization | `ELEVENLABS_API_KEY`; URL refs only |
| `assemblyai` | `stt.providers.assemblyai.AssemblyAIProvider` | AssemblyAI async submit+poll, diarization, 99-language `universal` model | `ASSEMBLYAI_API_KEY`; URL refs only |

The STT registry has the same merge semantics as the LLM one
(`STAPEL_AGENT["STT_PROVIDERS"]` overlay, `None` removes, or
`register_stt_provider()` at runtime), and its own startup checks
(`stapel_agent.W003/W004`). A custom engine is a `stapel_agent.SttProvider`
subclass returning a `NormalizedTranscript` — MODULE.md walks through a
self-hosted GigaAM adapter as the worked example.

## Vision & image generation

The two vision-capable LLM providers map `images` into their dialects
automatically — `anthropic` to image content blocks (URL or base64 source),
`openai-compat` to `image_url` parts (URL or data URI). `claude-code` has no
vision: an image request through it fails fast with `status: "failure"`.

Image generation ships one built-in backend, `openai-images` — anything
speaking the OpenAI `POST {base}/images/generations` dialect (OpenAI,
Together, self-hosted). It is the third instance of the merge-registry
pattern (`STAPEL_AGENT["IMAGE_PROVIDERS"]` overlay, `None` removes,
`register_image_provider()` at runtime; startup checks
`stapel_agent.W005/W006`). Vendors with their own protocols (Stability,
Ideogram, ...) are an app-layer `stapel_agent.ImageGenProvider` subclass —
recipe in [MODULE.md](MODULE.md). The agent returns raw results and writes
the ledger; storage/placement belongs to the calling tier.

## License

MIT — see [LICENSE](LICENSE)
