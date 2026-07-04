# Changelog

All notable changes to stapel-agent are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- **Transcription surface** — `POST api/llm/transcribe` + the
  `llm.transcribe` comm Function (URLs only over the wire, never raw
  audio bytes), backed by a second open registry with the same merge
  semantics as the LLM one: `STAPEL_AGENT["STT_PROVIDERS"]` overlay over
  `stt.BUILTIN_STT_PROVIDERS`, `None`/`""` removes a name,
  `register_stt_provider()` at runtime. Built-in adapters ported from
  the legacy recordings service `recordings/stt/`: `whisper-http` (OpenAI Whisper API
  or self-hosted faster-whisper — accepts url/path/bytes refs, the
  generalization the source lacked), `elevenlabs` (Scribe, diarization),
  `assemblyai` (async submit+poll, diarization). App-layer engines
  (GigaAM, ...) subclass `SttProvider` — see the MODULE.md worked
  example.
- **STT routing** (`stt/router.py`): explicit `provider` in the request
  (pinned — no fallback) > `STT_LANGUAGE_ROUTES[lang]` matrix >
  `DEFAULT_STT_PROVIDER` + `STT_FALLBACK_CHAIN`. The chain advances on
  `RetryableTranscriptionError` only (429/5xx/timeouts); fatal
  `TranscriptionError` (bad audio, auth) never falls back — ported
  intent from the the legacy recordings service error taxonomy.
- **Normalized transcript schema** (`stt/base.py`, Django-free):
  `NormalizedTranscript`/`NormalizedUtterance`/`NormalizedWord` with
  word-level timings, speakers and the untouched provider payload in
  `raw`; `AudioRef` (exactly one of url/path/data, PII-safe
  `describe()`); `transcript_from_dict()` for wire payloads.
- **Summarization surface** — `POST api/llm/summarize` + the
  `llm.summarize` comm Function: exactly one of `text`/`transcript`
  (schema-enforced), single-shot when the input fits one ~15k-token
  chunk, map-reduce (chunk summaries + merge pass) otherwise, optional
  target `language`. `summary.py` renders transcripts as timestamped
  Markdown and chunks with `seg_NNNN` → start-ms anchors
  (click-to-timestamp), ported from the legacy recordings service
  `transcript_schema.py`.
- **Ledger coverage for the new sources**: every transcription writes a
  `PromptLog` row (`source=transcribe`, `model` = STT provider name,
  token columns NULL, fallback walk in `metadata.attempts`); every
  summarize pass logs as `source=summarize` with full token accounting.
  Migration `0002` extends the `source` choices. Cache-by-prompt stays
  off for `summarize` by default (`CACHE_LOOKUP`).
- **System checks** `stapel_agent.W003` (unimportable / non-`SttProvider`
  `STT_PROVIDERS` entry) and `W004` (`DEFAULT_STT_PROVIDER` /
  `STT_FALLBACK_CHAIN` / `STT_LANGUAGE_ROUTES` naming an unknown
  provider) — warnings only: STT is an optional surface and degrades to
  `status: "failure"` per request.
- Public API additions (PEP 562, still Django-free at import):
  `transcribe`, `summarize`, `SttProvider`, `AudioRef`,
  `NormalizedTranscript`, `register_stt_provider`,
  `registered_stt_providers`.

## [0.1.0] - 2026-07-04

Initial release — Python port of the `the legacy agent service` NestJS service (the
legacy LLM facade), per the design fixed in the Stapel monorepo's
`docs/agent-service-and-core-ts.md` §2.

### Added
- **Open provider registry with merge semantics** (`providers/__init__.py`).
  `STAPEL_AGENT["PROVIDERS"]` is an overlay merged OVER
  `BUILTIN_PROVIDERS` (same additive style as stapel-notifications
  routing `TYPES`, deliberately not billing's replace-style
  `PAYMENT_PROVIDER`): adding one custom provider never requires
  restating the built-ins, `None`/`""` removes a name. Runtime API for
  app-layer `AppConfig.ready()`: `register_provider(name, cls_or_path)`
  (highest precedence) and `registered_providers()` (the effective
  mapping). `get_provider()` resolves runtime → settings merge →
  built-ins, lazily per request.
- **Django system checks** (`checks.py`, registered from
  `AgentConfig.ready()`): `stapel_agent.E001` when `DEFAULT_PROVIDER` is
  not in the effective registry; `stapel_agent.W001`/`W002` for
  unimportable dotted paths / non-`LlmProvider` entries (warnings — a
  broken unused entry degrades per request, it must not block deploys).
- **Cache-policy seam** (`cache.py`): `STAPEL_AGENT["CACHE_POLICY"]`
  (default `stapel_agent.cache.PromptLogCachePolicy`) points at a
  `CachePolicy` ABC — `should_cache(source)`, `lookup(prompt,
  system_prompt, source) -> str | None`, optional `store()` hook for
  external-storage policies. The default implements the PromptLog+TTL
  behaviour (`CACHE_LOOKUP`/`CACHE_TTL`); hosts swap in Redis/no-op
  without forking. The PromptLog ledger row is written regardless.
- **Serializer seams on both views** (`SerializerSeamMixin`, billing
  pattern): request serializers on both endpoints; typed
  `TranslateResponse` dataclass + serializer on translate.
  `api/llm/complete` deliberately keeps a plain contract dict — its
  `result` is arbitrary JSON (see MODULE.md).
- **HTTP surface, 1:1 with the legacy agent service**: `POST api/llm/complete` and
  `POST api/llm/translate` (hosts mount under `agent/`), same request/
  response contracts — `stapel-translate`'s `AgentProvider` keeps working
  unchanged. LLM failures stay HTTP 200 with `status: "failure"`; the
  JSON-API system prompt and the JSON/translation response extractors
  (`parsing.py`) are ported verbatim from `llm.controller.ts` /
  `llm.service.ts`. Auth is `IsServiceRequest | IsStaffUser`
  (`SERVICE_API_KEY` via stapel-core), same as stapel-billing's internal
  debit view.
- **comm surface**: `llm.complete` and `llm.translate` Functions
  (`stapel_core.comm`), with JSON Schemas in `schemas/functions/` — in a
  monolith the calls run in-process without HTTP. The comm payload uses
  `from_lang` (a Python-keyword-safe key); the HTTP wire keeps `from`.
- **Provider registry** (`STAPEL_AGENT["PROVIDERS"]`, dotted paths,
  resolved lazily per request): `AnthropicProvider` (SDK, default;
  optional `anthropic` extra), `OpenAICompatProvider` (any
  `/chat/completions` dialect — OpenAI, DeepSeek, MiMo, GLM, Kimi; maps
  `reasoning_tokens` → `thinking_tokens`), `ClaudeCodeCLIProvider`
  (spawns `claude -p`, **opt-in only, never the default**). Custom
  backends subclass `stapel_agent.LlmProvider` — no fork.
- **PromptLog ledger** with the full token accounting from
  system-design 7.16: input/output/**thinking**/cache-read/cache-write
  tokens, `duration_ms`, `model`, `model_size`, `source`, `user_id`,
  `metadata`; read-only admin.
- **Cache-by-prompt**: per-source toggle `CACHE_LOOKUP` (on for
  `translate`, off for `llm_facade` by default) + `CACHE_TTL` (7 days) —
  a repeated identical prompt+system_prompt within the window is served
  from the latest successful row without calling the provider.
- **Settings namespace** `STAPEL_AGENT` (`conf.py`, stapel-core
  `AppSettings`): `MODELS` size map, providers, credentials,
  `MAX_TOKENS`, CLI binary/timeout, cache policy — all read lazily.
- PEP 562 lazy public API (`agent_settings`, `complete`, `translate`,
  `LlmProvider`, `ProviderResult`) — importing the package pulls in no
  Django; `py.typed` marker.

### Deliberately dropped from the legacy agent service
- The `claude` module (execute/stream proto-harness) and the `terminal`
  module (node-pty shell) — out of scope for a Django library.
- The ApiKey CRUD/entity — service auth is stapel-core's
  `SERVICE_API_KEY` / `IsServiceRequest`; no module-owned key table.
- OAuth credential reading from `~/.claude/.credentials.json` and the
  background token-refresh hack — the CLI provider owns its auth; the
  facade itself is PAYG-API-key only.
- Any Node/CLI dependency in a default path — `claude-code` is a host
  opt-in.
