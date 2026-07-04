# stapel-agent — MODULE.md

Agent-facing map of this module: what it provides, its fork-free extension points, and
anti-patterns. Use it to classify a desired change as **app-layer override via an
extension point** vs **upstream contribution** (see `docs/stdlib-contribution-pipeline.md`
and system-design.md §8.6 in the Stapel monorepo). Stapel modules never import each
other; all cross-module communication goes through `stapel-core` (comm bus, signals,
registries). Everything below is verifiable against the code in this repo.

- Package: `stapel-agent` (PyPI), Python package `stapel_agent`, Django app label `agent`.
- Depends on `stapel-core` only (plus DRF, drf-spectacular; the `anthropic` SDK is optional at runtime).
- Provenance: Python port of the `the legacy agent service` NestJS service (design fixed in
  `docs/agent-service-and-core-ts.md` §2). HTTP paths/contracts are kept 1:1 —
  `stapel-translate`'s `AgentProvider` already POSTs to them. The STT/summarize
  surfaces port `the legacy recordings service`' `recordings/stt/` contract (normalized
  transcript schema, provider router, error taxonomy) behind the same facade.

## What this module provides

| Area | Contents |
|---|---|
| Models (`models.py`) | `PromptLog` (immutable per-call ledger: `source` llm_facade/translate/transcribe/summarize/other, `model`, `model_size`, `prompt`, `system_prompt`, `response`, `status` success/failure/timeout/error, `error_message`, `input_tokens`/`output_tokens`/`thinking_tokens`/`cache_read_tokens`/`cache_write_tokens`, `duration_ms`, `user_id`, JSON `metadata`, `created_at`; doubles as the cache-by-prompt store) |
| Services (`services.py`) | `complete()` (cache lookup → provider → PromptLog row → `{status, result, usage}`), `complete_json()` (JSON-API system prompt + JSON extraction — the `llm.complete` surface), `translate()` (iron's translate flow), `transcribe()` (STT router walk — see "STT providers"), `summarize()` (single-shot / map-reduce over `complete()`), `get_provider()` / `get_stt_provider()` (lazy resolution against the merged registries), `JSON_API_SYSTEM_PROMPT` |
| Parsing (`parsing.py`) | `parse_json_response()` (direct JSON → fenced block → object anywhere → array anywhere; surrounding prose becomes `comment`), `parse_translation_response()` — ports of the legacy agent service's extractors, Django-free |
| STT seam (`stt/`) | `SttProvider` ABC, `AudioRef` (exactly one of url/path/data), `NormalizedTranscript`/`NormalizedUtterance`/`NormalizedWord` + `transcript_from_dict()`/`utterances_from_words()`, `TranscriptionError` (fatal) / `RetryableTranscriptionError` (transient) error taxonomy (`stt/base.py`, Django-free); open registry (`stt/__init__.py`); language router (`stt/router.py`); adapters `whisper-http` / `elevenlabs` / `assemblyai` (`stt/providers/`) |
| Summarization prep (`summary.py`) | `render_markdown()` (timestamped `[MM:SS] speaker: text` lines), `build_summary_input()` (token-budget chunking with `seg_NNNN` → start-ms anchors), `split_text_chunks()`, the three system prompts (single-shot / chunk / merge) — Django-free |
| HTTP API (`urls.py`, `views.py`) | `api/llm/complete`, `api/llm/translate`, `api/llm/transcribe`, `api/llm/summarize` (all `IsServiceRequest \| IsStaffUser`; hosts mount the app under `agent/`). LLM/STT failures are HTTP 200 with `status: "failure"` — the iron contract |
| Providers (`providers/`) | `LlmProvider` ABC + `ProviderResult`/`ProviderError`/`ProviderTimeout` (`providers/base.py`), open registry (`providers/__init__.py`: `BUILTIN_PROVIDERS`, `register_provider()`, `registered_providers()`), `AnthropicProvider` (SDK, default), `OpenAICompatProvider` (any `/chat/completions` dialect), `ClaudeCodeCLIProvider` (opt-in `claude -p` spawn, never the default) |
| Cache (`cache.py`) | `CachePolicy` ABC (`should_cache` / `lookup` / optional `store`) + `PromptLogCachePolicy` default (PromptLog rows + `CACHE_LOOKUP`/`CACHE_TTL`) |
| System checks (`checks.py`) | `stapel_agent.E001` (DEFAULT_PROVIDER not in the effective registry), `W001` (unimportable provider path), `W002` (entry is not an `LlmProvider` subclass), `W003`/`W004` (the STT equivalents — see "STT providers") — registered from `AgentConfig.ready()` |
| Public API (`__init__.py`, PEP 562 lazy) | `__all__ = ["AudioRef", "CachePolicy", "LlmProvider", "NormalizedTranscript", "ProviderResult", "SttProvider", "agent_settings", "complete", "register_provider", "register_stt_provider", "registered_providers", "registered_stt_providers", "summarize", "transcribe", "translate"]` — Django-free at import |

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
| `STT_PROVIDERS` | `{}` | Overlay **merged over** `stt.BUILTIN_STT_PROVIDERS` (whisper-http / elevenlabs / assemblyai) — same merge semantics as `PROVIDERS`; see "STT providers" below. |
| `DEFAULT_STT_PROVIDER` | `"whisper-http"` | STT provider used when the request pins none and no language route matches. |
| `STT_FALLBACK_CHAIN` | `[]` | Provider names tried in order after the default — on **retryable** failure only. |
| `STT_LANGUAGE_ROUTES` | `{}` | `{iso-639-1: [provider names]}` language matrix; beats the default chain, loses to an explicit `provider` in the request. |
| `STT_TIMEOUT` | `1800` | Hard cap (seconds) on one STT provider's submit+poll cycle. |
| `WHISPER_BASE_URL` / `WHISPER_API_KEY` / `WHISPER_MODEL` | `""` / `""` / `"whisper-1"` | OpenAI-compatible Whisper endpoint (OpenAI API or self-hosted faster-whisper — the key is optional for self-hosted). |
| `ELEVENLABS_API_KEY` / `ELEVENLABS_STT_URL` / `ELEVENLABS_STT_MODEL` | `""` / Scribe URL / `"scribe_v2"` | ElevenLabs Scribe credentials/endpoint/model. |
| `ASSEMBLYAI_API_KEY` / `ASSEMBLYAI_BASE_URL` / `ASSEMBLYAI_MODEL` | `""` / `"https://api.assemblyai.com"` / `"universal"` | AssemblyAI credentials/endpoint/`speech_model`. |
| `CACHE_LOOKUP` | `{"llm_facade": False, "translate": True, "summarize": False}` | Per-source cache-by-prompt toggle, honoured by the **default** cache policy (latest `success` row with identical prompt+system_prompt+source). |
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
| `complete` | `(*, prompt: str, model: str, system_prompt: str \| None = None) -> ProviderResult` | Return the completion; raise `ProviderError` on failure, `ProviderTimeout` on timeout (logged as status `timeout`). Fill every token field you can — the ledger is the point. |
| `resolve_model` | `(model_size: str, default: str) -> str` | Optional override: map a size to this backend's model name; *default* is the already-resolved `MODELS[model_size]` (see `OpenAICompatProvider`). |

Providers must read credentials lazily (at call time, via `agent_settings`), never at
import — and never crash the process for a missing optional dependency (raise
`ProviderError` with a clear message instead; see `AnthropicProvider`).

### STT providers — a second open registry, same merge semantics

`stt/__init__.py` mirrors the LLM registry exactly. Three layers, later wins
per name:

1. `stt.BUILTIN_STT_PROVIDERS` (`whisper-http` / `elevenlabs` / `assemblyai`);
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
| `transcribe` | `(*, audio: AudioRef, language: str \| None = None, diarization: bool = False, timeout_seconds: int \| None = None) -> NormalizedTranscript` | Synchronous (polling-based) batch transcription. Raise `RetryableTranscriptionError` on transient failure, `TranscriptionError` on permanent failure. |
| `name` / `supports_diarization` / `supported_languages` / `cost_per_hour` | class attributes | Stable id (stored on the PromptLog row), capability flags, optional USD/hour for billing hosts. |

`AudioRef` carries exactly one of `url` / `path` / `data` (+ optional `mime`).
Cloud adapters that need a fetchable URL call `audio.require_url(provider=...)`
(fatal error otherwise); upload-style adapters call `audio.read_bytes(...)`,
which accepts any ref kind. `audio.describe()` is the PII-safe form logged to
the ledger (URL host only — no signed query strings, no raw bytes).

Worked example — a self-hosted **GigaAM** endpoint (the ru-quality engine from
the legacy recordings service) stays app-layer; no fork:

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
        resp = requests.post("http://gigaam:8080/transcribe",
                             files={"file": payload}, timeout=timeout_seconds or 1800)
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
latest successful `PromptLog` response with identical prompt+system_prompt+source
within `CACHE_TTL`. Swap it for Redis or a no-op without forking:

| Method | Signature | Contract |
|---|---|---|
| `should_cache` | `(source: str) -> bool` | Whether this source consults the cache at all. |
| `lookup` | `(prompt, system_prompt, source) -> str \| None` | Cached raw response text, or None on a miss. |
| `store` | `(prompt, system_prompt, source, response) -> None` | Optional (no-op default): persist a success for policies with external storage — the default policy needs nothing here because the PromptLog ledger row IS its storage. |

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
| `LlmCompleteView` | `api/llm/complete` (`llm-complete`) | `CompleteRequestSerializer` | — (plain contract dict, see below) |
| `LlmTranslateView` | `api/llm/translate` (`llm-translate`) | `TranslateRequestSerializer` (maps wire key `"from"` → `from_lang`) | `TranslateResponseSerializer` (`TranslateResponse` dataclass; None keys dropped after serialization — absent keys stay absent on the wire, per the iron contract) |
| `LlmTranscribeView` | `api/llm/transcribe` (`llm-transcribe`) | `TranscribeRequestSerializer` | — (plain contract dict, see below) |
| `LlmSummarizeView` | `api/llm/summarize` (`llm-summarize`) | `SummarizeRequestSerializer` (400 on not-exactly-one of text/transcript, 400 on a bad model size) | `SummarizeResponseSerializer` (`SummarizeResponse` dataclass; None keys dropped) |

`LlmCompleteView` and `LlmTranscribeView` deliberately have **no response
serializer**: complete's `result` is arbitrary JSON — an object or an array,
whatever structure the prompt asked the model for — and transcribe's
`transcript` embeds the raw provider payload under `raw`, which a typed
dataclass serializer cannot express without lying about the schema. The plain
`{status, ...}` dict is the contract there; the translate and summarize
responses ARE typed, so they get the full seam.

### Events & functions (comm surface)

Transport-agnostic via `stapel_core.comm` (in-process in a monolith, NATS/HTTP in
microservices — same code). JSON Schemas live in `schemas/functions/`.

**Emits:** none.

**Consumes:** none.

**Functions provided** (`functions.py`, registered in `AgentConfig.ready()`):

| Function | Payload | Returns |
|---|---|---|
| `llm.complete` | `{prompt, model: "small"\|"medium"\|"large", system_prompt?, provider?}` | Same dict as the HTTP response: `{status, result?, comment?, reason?, usage?}` |
| `llm.translate` | `{from_lang, to, entries: {key: text}}` (comm uses `from_lang`, not the HTTP wire key `from`) | `{status, result?: {key: translated}, reason?}` |
| `llm.transcribe` | `{audio_url, language?, diarization?, provider?, timeout_seconds?}` — **URLs only, never raw audio bytes** (`additionalProperties: false` rejects `data`/`path` keys); byte/path refs exist only for in-process `services.transcribe(AudioRef(...))` callers | `{status, transcript?: NormalizedTranscript, provider_used?, fallback_used?, reason?}` |
| `llm.summarize` | `{text \| transcript: NormalizedTranscript-dict (exactly one, schema-enforced oneOf), language?, model?, provider?}` | `{status, summary?, usage?, reason?}` |

## Anti-patterns

- **Don't put subscription/tp keys here — PAYG only.** Per the design doc
  (`docs/agent-service-and-core-ts.md`): this service authenticates to model vendors
  with pay-as-you-go API keys. Subscription OAuth tokens (Claude Pro/Max) belong to
  the CLI's own auth in hosts that opt into `claude-code` — never read
  `~/.claude/.credentials.json`, never background-refresh tokens (that the legacy agent service
  hack was deliberately dropped).
- **Don't make `claude-code` the default provider in library code.** It is a host
  opt-in for images that ship the CLI; the shipped default stays `anthropic`.
- **Don't turn LLM failures into HTTP 5xx.** The iron contract is HTTP 200 +
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
`STT_LANGUAGE_ROUTES`), different model names (`MODELS`,
`OPENAI_COMPAT_MODELS`), credentials, cache behaviour (`CACHE_LOOKUP` / `CACHE_TTL`
for the default policy, or a whole `CACHE_POLICY` swap), request/response payload
shape (serializer seams + URL remount), enabling the CLI provider in a host image.

**Upstream contribution** (Stapel-owned, via the contribution pipeline) when the change
alters module-owned contracts or invariants: new columns/indexes on `PromptLog`
(migrations live here), new `PromptSource`/`PromptStatus` values, changes to the
`LlmProvider`, `SttProvider` or `CachePolicy` ABC surfaces, the
`NormalizedTranscript` schema or the fatal/retryable error taxonomy, the registry
merge semantics, the JSON-extraction rules in `parsing.py`, the chunking/anchor
format in `summary.py`, the HTTP/comm response contracts or schemas, new system
checks, new endpoints or comm functions, bug fixes anywhere in this repo.

If a needed seam does not exist (e.g. a streaming surface or per-provider
rate-limiting hooks), the seam itself is an upstream contribution; the code that
plugs into it stays app-layer.
