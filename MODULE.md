# stapel-agent — MODULE.md

Agent-facing map of this module: what it provides, its fork-free extension points, and
anti-patterns. Use it to classify a desired change as **app-layer override via an
extension point** vs **upstream contribution** (see `docs/stdlib-contribution-pipeline.md`
and system-design.md §8.6 in the Stapel monorepo). Stapel modules never import each
other; all cross-module communication goes through `stapel-core` (comm bus, signals,
registries). Everything below is verifiable against the code in this repo.

- Package: `stapel-agent` (PyPI), Python package `stapel_agent`, Django app label `agent`.
- Depends on `stapel-core` only (plus DRF, drf-spectacular; the `anthropic` SDK is optional at runtime).
- Provenance: Python port of a prior NestJS service (design fixed in
  `docs/agent-service-and-core-ts.md` §2). HTTP paths/contracts are kept 1:1 —
  `stapel-translate`'s `AgentProvider` already POSTs to them. The STT/summarize
  surfaces port the legacy recordings service's `recordings/stt/` contract
  (normalized transcript schema, provider router, error taxonomy) behind the
  same facade.

## What this module provides

| Area | Contents |
|---|---|
| Models (`models.py`) | `PromptLog` (immutable per-call ledger: `source` llm_facade/translate/transcribe/summarize/generate_image/other, `model`, `model_size`, `prompt`, `system_prompt`, `response`, `status` success/failure/timeout/error, `error_message`, `input_tokens`/`output_tokens`/`thinking_tokens`/`cache_read_tokens`/`cache_write_tokens`, `duration_ms`, `user_id`, JSON `metadata`, `created_at`; doubles as the cache-by-prompt store) |
| Services (`services.py`) | `complete()` (cache lookup → provider → PromptLog row → `{status, result, usage}`; optional `images` for vision), `complete_json()` (JSON-API system prompt + JSON extraction — the `llm.complete` surface), `translate()`, `transcribe()` (STT router walk — see "STT providers"), `summarize()` (single-shot / map-reduce over `complete()`), `generate_image()` (see "Image generation"), `get_provider()` / `get_stt_provider()` / `get_image_provider()` (lazy resolution against the merged registries), `JSON_API_SYSTEM_PROMPT` |
| Parsing (`parsing.py`) | `parse_json_response()` (direct JSON → fenced block → object anywhere → array anywhere; surrounding prose becomes `comment`), `parse_translation_response()`, Django-free |
| STT seam (`stt/`) | `SttProvider` ABC (incl. the `keyterms`/`provider_options` biasing seam), `AudioRef` (exactly one of url/path/data), `NormalizedTranscript` (incl. the counts-only `biasing` block)/`NormalizedUtterance`/`NormalizedWord` + `transcript_from_dict()`/`utterances_from_words()`, `TranscriptionError` (fatal) / `RetryableTranscriptionError` (transient) error taxonomy (`stt/base.py`, Django-free); open registry (`stt/__init__.py`); language router (`stt/router.py`); adapters `whisper-http` / `elevenlabs` / `assemblyai` / `deepgram` / `gladia` / `soniox` / `speechmatics` / `xai-stt` (`stt/providers/`) |
| Summarization prep (`summary.py`) | `render_markdown()` (timestamped `[MM:SS] speaker: text` lines), `build_summary_input()` (token-budget chunking with `seg_NNNN` → start-ms anchors), `split_text_chunks()`, the three system prompts (single-shot / chunk / merge) — Django-free |
| Image seam (`images/`) | `ImageRef` (vision input: exactly one of url/data; wire form url \| base64 `data_b64`), `ImageGenProvider` ABC + `GeneratedImage`, `ImageGenError` (fatal) / `RetryableImageGenError` (transient) taxonomy (`images/base.py`, Django-free); open registry (`images/__init__.py`); built-in `openai-images` adapter (`images/providers/openai_images.py`) |
| HTTP API (`urls.py`, `views.py`) | `api/llm/complete` (accepts optional `images`), `api/llm/translate`, `api/llm/transcribe`, `api/llm/summarize`, `api/llm/generate-image` (all `IsServiceRequest \| IsStaffUser`; hosts mount the app under `agent/`). LLM/STT/image failures are HTTP 200 with `status: "failure"` |
| Providers (`providers/`) | `LlmProvider` ABC + `ProviderResult`/`ProviderError`/`ProviderTimeout` (`providers/base.py`), open registry (`providers/__init__.py`: `BUILTIN_PROVIDERS`, `register_provider()`, `registered_providers()`), `AnthropicProvider` (SDK, default), `OpenAICompatProvider` (any `/chat/completions` dialect), `ClaudeCodeCLIProvider` (opt-in `claude -p` spawn, never the default) |
| Cache (`cache.py`) | `CachePolicy` ABC (`should_cache` / `lookup` / optional `store`) + `PromptLogCachePolicy` default (PromptLog rows + `CACHE_LOOKUP`/`CACHE_TTL`) |
| System checks (`checks.py`) | `stapel_agent.E001` (DEFAULT_PROVIDER not in the effective registry), `W001` (unimportable provider path), `W002` (entry is not an `LlmProvider` subclass), `W003`/`W004` (the STT equivalents), `W005`/`W006` (the image-registry equivalents) — registered from `AgentConfig.ready()` |
| Public API (`__init__.py`, PEP 562 lazy) | `__all__ = ["AudioRef", "CachePolicy", "GeneratedImage", "ImageGenProvider", "ImageRef", "LlmProvider", "NormalizedTranscript", "ProviderResult", "SttProvider", "agent_settings", "complete", "generate_image", "register_image_provider", "register_provider", "register_stt_provider", "registered_image_providers", "registered_providers", "registered_stt_providers", "summarize", "transcribe", "translate"]` — Django-free at import |

## Extension points (fork-free)

### Settings — `STAPEL_AGENT` namespace (`conf.py`)

`agent_settings = AppSettings("STAPEL_AGENT", ...)` from `stapel_core.conf`.
Resolution order per key: `settings.STAPEL_AGENT[key]` → flat Django setting of the
same name → environment variable → default. All keys are read **lazily at call time**
(never frozen at import); caches invalidate on `setting_changed`.

| Key | Default | What it customizes |
|---|---|---|
| `MODELS` | `{"small": "claude-haiku-4-5-20251001", "medium": "claude-sonnet-5", "large": "claude-opus-4-8"}` | The size → model map every request goes through. |
| `PROVIDERS` | `{}` | Overlay **merged over** `providers.BUILTIN_PROVIDERS` — see "LLM providers" below. Resolved lazily per request via `import_string` in `services.get_provider` (not `import_strings` — an unknown/broken entry degrades to `status: "failure"`, never an import-time crash). |
| `DEFAULT_PROVIDER` | `"anthropic"` | Provider used when the request names none. |
| `ANTHROPIC_API_KEY` | `""` | Anthropic SDK key (read lazily per call). |
| `OPENAI_COMPAT_BASE_URL` / `OPENAI_COMPAT_API_KEY` | `""` | OpenAI-compatible endpoint + bearer token (OpenAI, DeepSeek, MiMo, GLM, Kimi). |
| `OPENAI_COMPAT_MODELS` | `{}` | Per-size model names for openai-compat; missing sizes fall back to `MODELS[size]`. |
| `CLI_BINARY` / `CLI_TIMEOUT` | `"claude"` / `120` | Claude Code CLI binary and the provider timeout (seconds). |
| `MAX_TOKENS` | `4096` | Completion token cap passed to providers. |
| `STT_PROVIDERS` | `{}` | Overlay **merged over** `stt.BUILTIN_STT_PROVIDERS` (whisper-http / elevenlabs / assemblyai / deepgram / gladia / soniox / speechmatics / xai-stt) — same merge semantics as `PROVIDERS`; see "STT providers" below. |
| `DEFAULT_STT_PROVIDER` | `"whisper-http"` | STT provider used when the request pins none and no language route matches. |
| `STT_FALLBACK_CHAIN` | `[]` | Provider names tried in order after the default — on **retryable** failure only. |
| `STT_LANGUAGE_ROUTES` | `{}` | `{iso-639-1: [provider names]}` language matrix; beats the default chain, loses to an explicit `provider` in the request. |
| `STT_TIMEOUT` | `1800` | Hard cap (seconds) on one STT provider's submit+poll cycle. |
| `WHISPER_BASE_URL` / `WHISPER_API_KEY` / `WHISPER_MODEL` | `""` / `""` / `"whisper-1"` | OpenAI-compatible Whisper endpoint (OpenAI API or self-hosted faster-whisper — the key is optional for self-hosted). |
| `ELEVENLABS_API_KEY` / `ELEVENLABS_STT_URL` / `ELEVENLABS_STT_MODEL` | `""` / Scribe URL / `"scribe_v2"` | ElevenLabs Scribe credentials/endpoint/model. |
| `ASSEMBLYAI_API_KEY` / `ASSEMBLYAI_BASE_URL` / `ASSEMBLYAI_MODEL` | `""` / `"https://api.assemblyai.com"` / `"universal"` | AssemblyAI credentials/endpoint/`speech_model`. |
| `DEEPGRAM_API_KEY` / `DEEPGRAM_BASE_URL` / `DEEPGRAM_MODEL` | `""` / `"https://api.deepgram.com"` / `"nova-3"` | Deepgram credentials/endpoint/model (synchronous `/v1/listen`). |
| `GLADIA_API_KEY` / `GLADIA_BASE_URL` / `GLADIA_MODEL` | `""` / `"https://api.gladia.io"` / `"solaria-1"` | Gladia credentials/endpoint/model (async upload+create+poll). |
| `SONIOX_API_KEY` / `SONIOX_BASE_URL` / `SONIOX_MODEL` | `""` / `"https://api.soniox.com"` / `"stt-async-v5"` | Soniox credentials/endpoint/model (async; uploads are cleaned up after every run). |
| `SPEECHMATICS_API_KEY` / `SPEECHMATICS_BASE_URL` / `SPEECHMATICS_MODEL` | `""` / `"https://eu1.asr.api.speechmatics.com"` / `"melia-1"` | Speechmatics credentials/region endpoint/model (melia-1 exists in EU1/US1 only). |
| `XAI_API_KEY` / `XAI_STT_URL` | `""` / `"https://api.x.ai/v1/stt"` | xAI STT credentials/endpoint (single synchronous POST; the endpoint has no model parameter). |
| `IMAGE_PROVIDERS` | `{}` | Overlay **merged over** `images.BUILTIN_IMAGE_PROVIDERS` (openai-images) — same merge semantics as `PROVIDERS`; see "Image generation" below. |
| `DEFAULT_IMAGE_PROVIDER` | `"openai-images"` | Image provider used when the request pins none. |
| `IMAGES_BASE_URL` / `IMAGES_API_KEY` | `""` / `""` | OpenAI-compatible `/images/generations` endpoint + key; **both fall back to the `OPENAI_COMPAT_*` pair**, so an OpenAI-flavoured host configures nothing extra. |
| `IMAGES_MODEL` | `""` | Optional model name; empty = omitted from the request (single-model servers). |
| `CACHE_LOOKUP` | `{"llm_facade": False, "translate": True, "summarize": False}` | Per-source cache-by-prompt toggle, honoured by the **default** cache policy (latest `success` row with identical prompt+system_prompt+source **and matching provider + resolved model + model size**; multimodal rows are excluded — the text key can't see pixels). |
| `CACHE_TTL` | `604800` | Cache window in seconds; expired rows are ignored (default policy). |
| `CACHE_POLICY` | `"stapel_agent.cache.PromptLogCachePolicy"` | Dotted path to a `CachePolicy` subclass — in `import_strings`, instantiated per call. See "Cache policy" below. |

### LLM providers — open registry with MERGE semantics (flagship seam)

Unlike billing's `PAYMENT_PROVIDER` (a single replace-style dotted path), the
provider registry is **additive**. Three layers, later wins per name:

1. `providers.BUILTIN_PROVIDERS` (anthropic / openai-compat / claude-code);
2. `STAPEL_AGENT["PROVIDERS"]` — merged **over** the built-ins: adding one custom
   provider never requires restating the built-ins; setting a name to `None`/`""`
   removes it from the effective registry;
3. runtime registrations via `stapel_agent.register_provider(name, cls_or_path)` —
   for app-layer packages registering from their own `AppConfig.ready()`.

`registered_providers()` returns the effective mapping; `services.get_provider(name)`
resolves against it lazily per request.

```python
# myproject/llm.py
from stapel_agent import LlmProvider, ProviderResult

class AcmeProvider(LlmProvider):
    name = "acme"
    def complete(self, *, prompt, model, system_prompt=None):
        ...
        return ProviderResult(text=..., input_tokens=..., output_tokens=...)

# settings.py — one entry, built-ins untouched; None removes a name
STAPEL_AGENT = {
    "PROVIDERS": {"acme": "myproject.llm.AcmeProvider", "claude-code": None},
    "DEFAULT_PROVIDER": "acme",
}

# — or at runtime, from an AppConfig.ready():
from stapel_agent import register_provider
register_provider("acme", AcmeProvider)   # class or dotted path
```

Misconfiguration is caught at startup by the system checks (`stapel_agent.E001`
for a `DEFAULT_PROVIDER` missing from the effective registry; `W001`/`W002` for
unimportable or non-`LlmProvider` entries — warnings, because unused broken
entries must not block deploys while lazy resolution degrades them to
`status: "failure"` per request).

ABC contract:

| Member | Signature | Contract |
|---|---|---|
| `complete` | `(*, prompt: str, model: str, system_prompt: str \| None = None, images: list[ImageRef] \| None = None) -> ProviderResult` | Return the completion; raise `ProviderError` on failure, `ProviderTimeout` on timeout (logged as status `timeout`). Fill every token field you can — the ledger is the point. |
| `resolve_model` | `(model_size: str, default: str) -> str` | Optional override: map a size to this backend's model name; *default* is the already-resolved `MODELS[model_size]` (see `OpenAICompatProvider`). |
| `supports_images` | class attribute, default `False` | Set `True` **and** accept the `images` kwarg to opt into vision. The service passes `images` only when non-empty and only to providers with the flag — pre-vision subclasses with the old three-argument signature keep working, and unsupporting providers degrade to a clear `status: "failure"`. |

Providers must read credentials lazily (at call time, via `agent_settings`), never at
import — and never crash the process for a missing optional dependency (raise
`ProviderError` with a clear message instead; see `AnthropicProvider`).

### Vision input — `images` on `llm.complete`

`ImageRef` (`images/base.py`, Django-free) carries exactly one of `url` /
`data` (bytes; `mime` defaults to `image/png`). Over comm/HTTP only `url` or
base64 `data_b64` travel — the JSON schema and the request serializer reject
raw-bytes keys and invalid base64; `ImageRef(data=...)` exists for in-process
callers. Provider mapping:

- `AnthropicProvider` → content blocks `{"type": "image", "source":
  {"type": "url"|"base64", ...}}` followed by the text block;
- `OpenAICompatProvider` → `content: [{"type": "image_url", "image_url":
  {"url": <url-or-data-URI>}}, ..., {"type": "text", ...}]`;
- `ClaudeCodeCLIProvider` → no vision (`supports_images = False`): an image
  request through it fails fast with `status: "failure"`.

Cache interaction: the prompt cache is **text-keyed**, so image requests
bypass lookup and store, and the default policy's `lookup()` excludes
multimodal ledger rows — identical text over different pixels never collides
in either direction. The ledger row records `metadata.images = {count,
kinds}` — never bytes.

**Image size limits are not enforced in this module (by design).** A
too-large image is not rejected locally — it is sent to the vendor, which
returns its own 4xx, mapped to a clean `status: "failure"` (no crash, no
bytes in the ledger). The trade-off: the error is vendor-worded, not
localized, and the upstream bandwidth is spent. For HTTP callers the
effective ceiling is Django's `DATA_UPLOAD_MAX_MEMORY_SIZE` (2.5 MB
default); comm and in-process `ImageRef(data=...)` callers have no cap. A
host that wants a hard local limit should gate before calling — e.g. a
provider subclass that checks each ref's byte size against the vendor's
documented maximum and raises `ProviderError`.

### STT providers — a second open registry, same merge semantics

`stt/__init__.py` mirrors the LLM registry exactly. Three layers, later wins
per name:

1. `stt.BUILTIN_STT_PROVIDERS` (`whisper-http` / `elevenlabs` / `assemblyai` /
   `deepgram` / `gladia` / `soniox` / `speechmatics` / `xai-stt`);
2. `STAPEL_AGENT["STT_PROVIDERS"]` — merged **over** the built-ins (add one
   name, never restate the rest; `None`/`""` removes a name);
3. runtime `stapel_agent.register_stt_provider(name, cls_or_path)` — highest
   precedence, for app-layer `AppConfig.ready()`.

`registered_stt_providers()` returns the effective mapping;
`services.get_stt_provider(name)` resolves lazily per request.

**Routing** (`stt/router.py`, `select_chain()`): explicit `provider` in the
request → single-name chain, **no fallback** (a pinned provider's failures must
stay visible) → `STT_LANGUAGE_ROUTES[lang]` (language normalized `en-US` → `en`)
→ `[DEFAULT_STT_PROVIDER] + STT_FALLBACK_CHAIN`. The service walks the chain on
`RetryableTranscriptionError` only (429/5xx/timeouts/transport); a fatal
`TranscriptionError` (bad audio, auth, other 4xx) stops immediately — the next
provider would fail on the same input. Every `transcribe()` call writes one
PromptLog row: `source=transcribe`, `model` = provider name, token columns
NULL, `metadata.attempts` = the per-provider walk.

ABC contract (`stt/base.py`, Django-free):

| Member | Signature | Contract |
|---|---|---|
| `transcribe` | `(*, audio: AudioRef, language: str \| None = None, diarization: bool = False, timeout_seconds: int \| None = None, keyterms: list[str] \| None = None, provider_options: dict \| None = None) -> NormalizedTranscript` | Synchronous (polling-based) batch transcription. Raise `RetryableTranscriptionError` on transient failure, `TranscriptionError` on permanent failure. `keyterms` = the generic vocabulary-biasing seam (see below); `provider_options` = free-form per-provider passthrough applied AFTER the adapter's own request params — never silently dropped. |
| `name` / `supports_diarization` / `supports_keyterms` / `supported_languages` / `cost_per_hour` | class attributes | Stable id (stored on the PromptLog row), capability flags, optional USD/hour for billing hosts. |
| `speech_model` | class attribute (`str \| None`, default None) | **Per-registration model pin** — the STT mirror of fixing a model on an LLM registration. Set it on a subclass to force one engine/model for that registered name, overriding the provider's configured default (`WHISPER_MODEL` / `ELEVENLABS_STT_MODEL` / `ASSEMBLYAI_MODEL`); None falls back to that default. `effective_model()` returns the pin-or-default and `default_speech_model()` the configured default (providers override it to read their setting). Two registrations of one adapter class can thus carry different models without a settings change or a fork. |

**Vocabulary biasing** (`keyterms`): a normalized list of plain terms (no
provider weight syntax). Adapters with `supports_keyterms = True` map it onto
their provider's own parameter — Deepgram Nova-3 repeated `keyterm` query
params (~500-token budget), ElevenLabs Scribe `keyterms` (<50 chars / ≤5
words / ≤1000 terms; +20% billing surcharge), AssemblyAI `keyterms_prompt`
(≤6 words per phrase, ≤1000 words total), xAI repeated `keyterm` multipart
fields (≤100 × 50 chars). Per-provider limits TRUNCATE (never error);
adapters without support report the request instead of failing. Application
metadata comes back generically as `NormalizedTranscript.biasing =
{"applied": bool, "terms_sent": int, "terms_truncated": int} | None` —
**counts only, never the terms**: term lists are customer data and stay out
of transcripts, ledgers and logs. Dictionary storage/selection, routing
matrices and biasing telemetry are app-layer concerns, not core's.

`AudioRef` carries exactly one of `url` / `path` / `data` (+ optional `mime`).
Cloud adapters that need a fetchable URL call `audio.require_url(provider=...)`
(fatal error otherwise); upload-style adapters call `audio.read_bytes(...)`,
which accepts any ref kind. `audio.describe()` is the PII-safe form logged to
the ledger (URL host only — no signed query strings, no raw bytes).

Worked example — a self-hosted **GigaAM** endpoint (a ru-quality STT engine)
stays app-layer; no fork:

```python
# myproject/stt.py
import requests
from stapel_agent import AudioRef, SttProvider
from stapel_agent.stt.base import (
    NormalizedTranscript, RetryableTranscriptionError, TranscriptionError,
    utterances_from_words,
)

class GigaAmProvider(SttProvider):
    name = "gigaam"
    supported_languages = frozenset({"ru"})

    def transcribe(self, *, audio, language=None, diarization=False,
                   timeout_seconds=None):
        payload = audio.read_bytes(provider=self.name)   # any ref kind
        # `timeout_seconds is None` → default; never `or <default>` — an
        # explicit 0 must stay 0 (the request boundary rejects non-positive
        # values with `minimum: 1`, so 0/negatives never reach here).
        timeout = 1800 if timeout_seconds is None else timeout_seconds
        resp = requests.post("http://gigaam:8080/transcribe",
                             files={"file": payload}, timeout=timeout)
        if resp.status_code == 429 or resp.status_code >= 500:
            raise RetryableTranscriptionError(f"gigaam {resp.status_code}",
                                              provider=self.name)
        if resp.status_code >= 400:
            raise TranscriptionError(f"gigaam {resp.status_code}", provider=self.name)
        body = resp.json()
        words = [...]  # map body["words"] → NormalizedWord(text, start, end)
        return NormalizedTranscript(
            provider=self.name, language="ru",
            duration_seconds=body.get("duration"),
            words=words, utterances=utterances_from_words(words), raw=body,
        )

# settings.py — route Russian audio to it, everything else stays default
STAPEL_AGENT = {
    "STT_PROVIDERS": {"gigaam": "myproject.stt.GigaAmProvider"},
    "STT_LANGUAGE_ROUTES": {"ru": ["gigaam", "whisper-http"]},
}

# — or at runtime, from an AppConfig.ready():
from stapel_agent import register_stt_provider
register_stt_provider("gigaam", GigaAmProvider)
```

Misconfiguration is caught at startup by `stapel_agent.W003` (unimportable /
non-`SttProvider` `STT_PROVIDERS` entry) and `W004` (`DEFAULT_STT_PROVIDER`,
`STT_FALLBACK_CHAIN` or `STT_LANGUAGE_ROUTES` referencing a name missing from
the effective registry). All STT checks are warnings — STT is an optional
surface and a broken entry degrades to `status: "failure"` per request.

### Image generation — a third open registry, same merge semantics

`images/__init__.py` is the third instance of the house registry pattern:

1. `images.BUILTIN_IMAGE_PROVIDERS` (`openai-images` — the OpenAI images
   dialect `POST {base}/images/generations`: OpenAI, Together, self-hosted
   compatibles);
2. `STAPEL_AGENT["IMAGE_PROVIDERS"]` — merged **over** the built-ins
   (`None`/`""` removes a name);
3. runtime `stapel_agent.register_image_provider(name, cls_or_path)` —
   highest precedence, for app-layer `AppConfig.ready()`.

ABC contract (`images/base.py`, Django-free):

| Member | Signature | Contract |
|---|---|---|
| `generate` | `(*, prompt: str, size: str = "1024x1024", n: int = 1, timeout_seconds: int \| None = None) -> list[GeneratedImage]` | Raise `RetryableImageGenError` on transient failure (network, 429, 5xx, timeout), `ImageGenError` on permanent failure (bad prompt/size, auth). |
| `name` / `supported_sizes` | class attributes | Stable id (stored on the PromptLog row); set of `"WxH"` strings or None for "any" — the service rejects unsupported sizes before calling out. |

Vendors with their own protocols (Stability, Ideogram, ...) stay app-layer:

```python
# myproject/imagegen.py
from stapel_agent import GeneratedImage, ImageGenProvider

class StabilityProvider(ImageGenProvider):
    name = "stability"
    supported_sizes = frozenset({"1024x1024", "1152x896"})

    def generate(self, *, prompt, size="1024x1024", n=1, timeout_seconds=None):
        ...  # call the Stability REST API; raise Retryable/ImageGenError
        return [GeneratedImage(mime="image/png", data_b64=...)]

# settings.py — one entry, openai-images stays available
STAPEL_AGENT = {"IMAGE_PROVIDERS": {"stability": "myproject.imagegen.StabilityProvider"}}
# — or: register_image_provider("stability", StabilityProvider) in ready()
```

Misconfiguration is caught by `stapel_agent.W005` (unimportable /
non-`ImageGenProvider` entry) and `W006` (unknown `DEFAULT_IMAGE_PROVIDER`)
— warnings, same rationale as the STT checks.

**Module boundary — storage is the CALLER's job.** `generate_image()` returns
the provider's raw results (`url` and/or `data_b64` per image) and writes the
ledger row, nothing else. Placing images into stapel-cdn / asset libraries,
metering, and placement belong to the system-design §8.8 gateway verb in the
calling tier — this module never talks to storage. The ledger row is
`source=generate_image`, `model` = provider name, prompt logged, response
**not** logged raw (`metadata.images = {count, mimes, bytes_total}` instead),
token columns NULL.

### Summarization contract

`services.summarize(text_or_transcript, *, language=None, model_size="medium",
provider=None, user_id=None, chunk_tokens=None)` accepts a `str`, a
`NormalizedTranscript`, or its `to_dict()` form (the shape `llm.transcribe`
returns) — exactly one input, enforced at every surface. Transcripts are
rendered to timestamped `[MM:SS] speaker: text` Markdown; `summary.py` chunks
by token budget (`DEFAULT_CHUNK_TOKENS` = 15 000, ≈4 chars/token) with
`seg_NNNN` → start-ms anchors for click-to-timestamp UIs. One chunk →
single-shot; more → map-reduce (per-chunk summaries, then a merge pass), all
through `services.complete()` — so every pass lands in the ledger as
`source=summarize` with full token accounting, and `usage` in the response is
the aggregate across all passes. Cache-by-prompt is off for `summarize` by
default (`CACHE_LOOKUP`).

### Cache policy (dotted-path swap)

`STAPEL_AGENT["CACHE_POLICY"]` points at a `stapel_agent.cache.CachePolicy`
subclass (instantiated per call). The default `PromptLogCachePolicy` implements the
stock behaviour: `should_cache(source)` reads `CACHE_LOOKUP`, `lookup()` returns the
latest successful `PromptLog` response with identical
prompt+system_prompt+source **and the same provider + resolved model + model
size** within `CACHE_TTL`. Swap it for Redis or a no-op without forking:

| Method | Signature | Contract |
|---|---|---|
| `should_cache` | `(source: str) -> bool` | Whether this source consults the cache at all. |
| `lookup` | `(prompt, system_prompt, source, *, provider, model, model_size) -> str \| None` | Cached raw response text, or None on a miss. `provider`/`model` (resolved `MODELS[model_size]`)/`model_size` are part of the key — a "small" answer must never satisfy a "large" request, an explicit `provider=` must not collide with the default, and a model-version bump in `MODELS` must invalidate old rows. |
| `store` | `(prompt, system_prompt, source, response, *, provider, model, model_size) -> None` | Optional (no-op default): persist a success for policies with external storage — the default policy needs nothing here because the PromptLog ledger row IS its storage. |

> **Breaking in 0.2.0:** `lookup`/`store` gained the keyword-only
> `provider`/`model`/`model_size` params. A custom policy must add them to
> its overrides (they are required, not defaulted — a mismatch is a loud
> `TypeError`, never a silent cross-model cache hit). See CHANGELOG.

The `PromptLog` ledger row is written for every provider call **regardless of the
policy** — caching is a read seam; token accounting is not optional.

### Swappable models

None. `PromptLog` has a fixed `db_table` (`agent_prompt_log`) and no user FK — it
stores `user_id` as an opaque string, so it works with any (or no) user model. Extend
per-call data via `metadata` (JSON) or an app-layer model keyed by `PromptLog.id` —
do not fork to add columns.

### Serializer seams (`views.py`)

All views mix in `SerializerSeamMixin` (`request_serializer_class` /
`response_serializer_class` + overridable getters); subclass the view, swap the
serializer, remount the URL — HTTP method bodies stay untouched.

| View | Route (name) | Request serializer | Response serializer |
|---|---|---|---|
| `LlmCompleteView` | `api/llm/complete` (`llm-complete`) | `CompleteRequestSerializer` (validates `images` entries: exactly one of url/data_b64, base64 must decode) | — (plain contract dict, see below) |
| `LlmTranslateView` | `api/llm/translate` (`llm-translate`) | `TranslateRequestSerializer` (maps wire key `"from"` → `from_lang`) | `TranslateResponseSerializer` (`TranslateResponse` dataclass; None keys dropped after serialization — absent keys stay absent on the wire) |
| `LlmTranscribeView` | `api/llm/transcribe` (`llm-transcribe`) | `TranscribeRequestSerializer` | — (plain contract dict, see below) |
| `LlmSummarizeView` | `api/llm/summarize` (`llm-summarize`) | `SummarizeRequestSerializer` (400 on not-exactly-one of text/transcript, 400 on a bad model size) | `SummarizeResponseSerializer` (`SummarizeResponse` dataclass; None keys dropped) |
| `LlmGenerateImageView` | `api/llm/generate-image` (`llm-generate-image`) | `GenerateImageRequestSerializer` (400 on `n` outside 1-10 or `timeout_seconds` < 1) | — (plain contract dict, see below) |

`LlmCompleteView`, `LlmTranscribeView` and `LlmGenerateImageView` deliberately
have **no response serializer**: complete's `result` is arbitrary JSON — an
object or an array, whatever structure the prompt asked the model for —
transcribe's `transcript` embeds the raw provider payload under `raw`, and
generate-image's entries carry `url` or `data_b64` depending on the backend;
a typed dataclass serializer cannot express any of these without lying about
the schema. The plain `{status, ...}` dict is the contract there; the
translate and summarize responses ARE typed, so they get the full seam.

### Events & functions (comm surface)

Transport-agnostic via `stapel_core.comm` (in-process in a monolith, NATS/HTTP in
microservices — same code). JSON Schemas live in `schemas/functions/`.

**Emits:** none.

**Consumes:** none.

**Functions provided** (`functions.py`, registered in `AgentConfig.ready()`):

| Function | Payload | Returns |
|---|---|---|
| `llm.complete` | `{prompt, model: "small"\|"medium"\|"large", system_prompt?, provider?, images?: [{url} \| {data_b64, mime?}]}` — image entries are url or base64 only (schema-enforced oneOf; a raw `data` key is rejected) | Same dict as the HTTP response: `{status, result?, comment?, reason?, usage?}` |
| `llm.translate` | `{from_lang, to, entries: {key: text}}` (comm uses `from_lang`, not the HTTP wire key `from`) | `{status, result?: {key: translated}, reason?}` |
| `llm.transcribe` | `{audio_url, language?, diarization?, provider?, timeout_seconds? (>= 1)}` — **URLs only, never raw audio bytes** (`additionalProperties: false` rejects `data`/`path` keys); byte/path refs exist only for in-process `services.transcribe(AudioRef(...))` callers | `{status, transcript?: NormalizedTranscript, provider_used?, fallback_used?, reason?}` |
| `llm.summarize` | `{text \| transcript: NormalizedTranscript-dict (exactly one, schema-enforced oneOf), language?, model?, provider?}` | `{status, summary?, usage?, reason?}` |
| `llm.generate_image` | `{prompt, size?, n? (1-10), provider?, timeout_seconds? (>= 1)}` | `{status, images?: [{url? \| data_b64?, mime}], provider_used?, reason?}` — raw results; storage is the caller's job |
| `llm.stt_catalog` | `{}` (no arguments) | `{status, providers: [{name, available, model, pinned_model, supports_diarization, supported_languages, cost_per_hour}], default_provider, fallback_chain, language_routes}` — the addressable STT surface; each `model` is the registration's effective model (the `speech_model` pin, else the configured default). Read-only, writes no PromptLog row |

### Admin categories (`stapel_core.access`, admin-suite AS-5)

`PromptLog` is decorated `@access.ops` and its `ModelAdmin` (`admin.py`)
subclasses `stapel_core.django.admin.base.StapelModelAdmin`: it is the
doc's own `NotificationLog`-shaped delivery/audit ledger, written exclusively
by the `services.py` completion pipeline (`services.complete`/`transcribe`)
— there is no staff add/change/delete workflow through the admin for `ops`
to break. `StapelModelAdmin` now enforces that read-only contract (view
requires HIGH clearance; add/change/delete forbidden for everyone including
the superuser) instead of the three hand-rolled `has_*_permission`
overrides the admin used before this rollout.

No `@access.secret` model exists in this repo. Every LLM/STT/image-gen API
key (`ANTHROPIC_API_KEY`, `OPENAI_COMPAT_API_KEY`, `ELEVENLABS_API_KEY`,
`ASSEMBLYAI_API_KEY`, `WHISPER_API_KEY`, `IMAGES_API_KEY`, ...) is read
lazily from `agent_settings`/Django settings/env at call time (see "Settings"
above) — none is ever persisted to a model field, so there is no credential
carrier to mask.

## Anti-patterns

- **Don't put subscription/tp keys here — PAYG only.** Per the design doc
  (`docs/agent-service-and-core-ts.md`): this service authenticates to model vendors
  with pay-as-you-go API keys. Subscription OAuth tokens (Claude Pro/Max) belong to
  the CLI's own auth in hosts that opt into `claude-code` — never read
  `~/.claude/.credentials.json`, never background-refresh tokens.
- **Don't make `claude-code` the default provider in library code.** It is a host
  opt-in for images that ship the CLI; the shipped default stays `anthropic`.
- **Don't turn LLM failures into HTTP 5xx.** The contract is HTTP 200 +
  `{"status": "failure", "reason": ...}`; `stapel-translate`'s `AgentProvider` (and
  any other caller) branches on `status`.
- **Don't fork to add an LLM provider.** Implement the `LlmProvider` ABC in the app
  layer and add ONE settings entry (`STAPEL_AGENT["PROVIDERS"]["acme"] = "dotted.path"`)
  or call `register_provider()` from your `AppConfig.ready()` — the registry merges,
  so the built-ins never need restating.
- **Don't restate the built-in providers when overriding `PROVIDERS`.** The dict is a
  merge overlay, not a replacement — restating them freezes this module's internal
  paths into host settings and breaks when upstream moves a class. Add/override/remove
  only the names you mean to change.
- **Don't fork to add an STT engine either.** Same seam, same rule: implement
  the `SttProvider` ABC in the app layer and add one `STT_PROVIDERS` entry or
  call `register_stt_provider()` from `AppConfig.ready()` (see the GigaAM
  worked example above).
- **Don't send raw audio bytes over comm/HTTP.** The `llm.transcribe` payload
  carries a presigned URL only; the tier that owns bytes uploads them and
  passes a URL (or calls `services.transcribe(AudioRef(data=...))` in-process).
- **Don't fall back across STT providers on fatal errors.** The chain walk is
  for `RetryableTranscriptionError` only — bad audio/auth fails the same way on
  every provider, and retrying would just burn money and mask the real error.
- **Don't store generated images in this module.** The agent returns raw
  results (`url`/`data_b64`) and writes the ledger; folding them into
  stapel-cdn or an asset library is the calling tier's job (system-design §8.8
  gateway verb) — this module never imports storage.
- **Don't log image bytes.** Neither vision inputs nor generation outputs
  belong in `PromptLog` — the ledger records `{count, kinds}` /
  `{count, mimes, bytes_total}` in `metadata`, and `response` stays NULL for
  `generate_image` rows.
- **Don't write `PromptLog` rows by hand or edit them.** The ledger is written by
  `services.complete()` only, and successful rows double as the prompt cache —
  hand-written rows poison cache lookups. The admin is read-only for the same reason.
- **Don't bypass `services.complete()` to call a provider directly.** You would skip
  the cache, the token ledger and the timeout→status mapping.
- **Don't read provider credentials at import time.** Read through `agent_settings`
  at call time (tests and multi-tenant hosts change settings after import).
- **Don't import other `stapel-*` modules.** Callers reach this module via HTTP or
  the `llm.*` comm functions; this module imports only `stapel_core`.

## App-layer override vs upstream contribution — rule of thumb

**App-layer override** (client-owned, no fork) when the change fits an extension point
above: a new/replacement/removed LLM backend (`PROVIDERS` merge entry or
`register_provider()` + `LlmProvider` subclass), a new STT engine (GigaAM, Xiaomi,
Deepgram, ... — `STT_PROVIDERS` entry or `register_stt_provider()` + `SttProvider`
subclass), STT routing (`DEFAULT_STT_PROVIDER` / `STT_FALLBACK_CHAIN` /
`STT_LANGUAGE_ROUTES`), a new image-generation backend (Stability, Ideogram,
... — `IMAGE_PROVIDERS` entry or `register_image_provider()` +
`ImageGenProvider` subclass), different model names (`MODELS`,
`OPENAI_COMPAT_MODELS`, `IMAGES_MODEL`), credentials, cache behaviour
(`CACHE_LOOKUP` / `CACHE_TTL` for the default policy, or a whole
`CACHE_POLICY` swap), request/response payload shape (serializer seams + URL
remount), enabling the CLI provider in a host image.

**Upstream contribution** (Stapel-owned, via the contribution pipeline) when the change
alters module-owned contracts or invariants: new columns/indexes on `PromptLog`
(migrations live here), new `PromptSource`/`PromptStatus` values, changes to the
`LlmProvider`, `SttProvider`, `ImageGenProvider` or `CachePolicy` ABC surfaces, the
`NormalizedTranscript`/`ImageRef`/`GeneratedImage` schemas or the fatal/retryable
error taxonomy, the registry merge semantics, the JSON-extraction rules in
`parsing.py`, the chunking/anchor format in `summary.py`, the provider
content-block mapping for vision, the HTTP/comm response contracts or schemas,
new system checks, new endpoints or comm functions, bug fixes anywhere in this repo.

If a needed seam does not exist (e.g. a streaming surface or per-provider
rate-limiting hooks), the seam itself is an upstream contribution; the code that
plugs into it stays app-layer.
