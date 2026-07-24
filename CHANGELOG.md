# Changelog

All notable changes to stapel-agent are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [0.4.0] — 2026-07-24

Minor: two new generic seams — speaker **diarization** and text
**embeddings** — layered end-to-end the way transcribe is (ABC +
normalized dataclass + error taxonomy → provider adapters → registry +
settings → service + PromptLog → comm function + HTTP endpoint +
committed schema). Core stays generic: parameter wiring per API lives
here; fusing diarization turns with STT words, chunking policies and
ranking stay app-layer.

### Added
- **Diarization seam** (`diarization/base.py`):
  `DiarizationProvider.diarize(*, audio: AudioRef, num_speakers=None,
  timeout_seconds=None, provider_options=None) → NormalizedDiarization`
  (`provider`, `duration_seconds`, `turns: [DiarTurn(speaker, start,
  end, confidence)]` — seconds-float, wire order preserved,
  `speakers_detected`, `raw`). Errors join the house hierarchy:
  `DiarizationError(ProviderError)` fatal vs
  `RetryableDiarizationError` (429/5xx/timeouts). Ported iron-benchmark
  invariants: speaker-count knob validation
  (`validate_speaker_counts` — exact count XOR min/max bounds, all
  ≥ 1, min ≤ max, fail loudly BEFORE any call), inverted-segment
  clamping (`end < start` → clamped, never dropped), malformed-success
  = loud failure. An EMPTY diarization is data, not an error (the
  empty=error gate is hybrid-merge policy — caller's decision).
- **`pyannote-http` adapter** (`diarization/providers/pyannote_http.py`):
  one synchronous multipart POST to a self-hosted pyannote wrapper
  (gigaam-style plain HTTP, NOT the pyannoteAI cloud jobs API):
  `POST {PYANNOTE_BASE_URL}/diarize` (file + optional
  `num_speakers`/`min_speakers`/`max_speakers` form fields, bounds via
  `provider_options`) → `{"diarization": [{speaker, start, end,
  confidence?}], "duration"?}` — request knobs named after
  `pyannote.audio`'s own `apply()` signature, response segments in the
  pyannoteAI `output.diarization` shape; the full wire contract is
  documented in the module docstring. Upload-capable: any AudioRef
  kind. Settings: `DIARIZATION_PROVIDERS` / `DEFAULT_DIARIZATION_PROVIDER`
  (merge-registry canon + `register_diarization_provider()`),
  `DIARIZATION_TIMEOUT`, `PYANNOTE_BASE_URL`, `PYANNOTE_API_KEY`
  (optional — Bearer only when set).
- **Embeddings seam** (`embeddings/base.py`):
  `EmbeddingProvider.embed(*, texts: list[str], timeout_seconds=None,
  provider_options=None) → NormalizedEmbeddings` (`provider`, `model`,
  `dim`, `vectors` — **input order preserved**, `usage`, `raw` — raw
  kept small, the vectors are never stored twice). Batch gate
  `require_texts`: empty batches / non-string / empty-string entries
  are fatal BEFORE any provider call. A returned count mismatch is a
  loud fatal failure, never a misaligned batch. Per-registration
  `embedding_model` pin mirrors the STT `speech_model` canon.
  `EmbeddingError(ProviderError)` fatal vs `RetryableEmbeddingError`.
- **Embedding adapters**: `openai-embeddings`
  (`embeddings/providers/openai_compat.py` — `POST {base}/embeddings`,
  `{"model", "input": [...]}`, wire entries re-ordered by `index`;
  `EMBEDDINGS_BASE_URL`/`EMBEDDINGS_API_KEY` fall back to the
  `OPENAI_COMPAT_*` pair, `EMBEDDINGS_MODEL` default
  `text-embedding-3-small`) and `embeddings-http`
  (`embeddings/providers/http_server.py` — generic self-host contract
  for local multilingual models class bge-m3/multilingual-e5:
  `POST {EMBEDDINGS_HTTP_BASE_URL}/embed` `{"texts": [...]}` →
  `{"vectors": [[...]], "model"?, "dim"?, "usage"?}`, documented in the
  module docstring; model attribution = server echo, never pretended).
  Settings: `EMBEDDING_PROVIDERS` / `DEFAULT_EMBEDDING_PROVIDER` +
  `register_embedding_provider()`, `EMBEDDINGS_TIMEOUT`,
  `EMBEDDINGS_HTTP_BASE_URL`, `EMBEDDINGS_HTTP_API_KEY`.
- **Surfaces**: `llm.diarize` and `llm.embed` comm functions (+
  committed `schemas/functions/llm.diarize.json` / `llm.embed.json`),
  HTTP endpoints `POST api/v1/llm/diarize` / `POST api/v1/llm/embed`
  (DTO + serializer validation: `num_speakers ≥ 1`, non-empty `texts`,
  positive timeout — new error keys `error.400.invalid_num_speakers`,
  `error.400.empty_texts`). Envelopes mirror transcribe's:
  `{"status": "ok", "diarization"|"embeddings": {...},
  "provider_used": str}` or the failure envelope (HTTP 200).
- **Ledger**: new `PromptSource.DIARIZE` / `PromptSource.EMBED`
  (migration 0004). One row per call, `model` = provider name.
  Privacy canon: the diarize row carries the PII-safe
  `audio.describe()` descriptor + turn/speaker COUNTS; the embed row
  carries `texts:<n>` + `{model, batch_size, dim, usage}` — **never
  the texts, never the vectors** (tested).
- **Checks**: `stapel_agent.W007`/`W008` (diarization registry entry /
  default), `W009`/`W010` (embedding registry entry / default) — all
  W-level, same degrade-per-request rationale as STT/images.
- Public API: `diarize`, `embed`, `DiarizationProvider`,
  `NormalizedDiarization`, `EmbeddingProvider`, `NormalizedEmbeddings`,
  `register_diarization_provider` / `registered_diarization_providers`,
  `register_embedding_provider` / `registered_embedding_providers`.

## [0.3.0] — 2026-07-23

Minor: the generic STT vocabulary-biasing seam + five new provider
adapters ported from the iron-benchmark quads. Core stays generic —
per-provider PARAMETER WIRING (how each API accepts biasing) lives here;
dictionary storage/selection, routing matrices and biasing telemetry
stay app-layer.

### Added
- **Biasing seam** (`stt/base.py`): `SttProvider.transcribe(...)` gains
  `keyterms: list[str] | None` (normalized plain bias terms) and
  `provider_options: dict | None` (free-form per-provider passthrough,
  applied AFTER the adapter's own request params — a caller can pin
  provider specifics without a core release; unknown keys go to the
  provider as-is, never silently dropped). New capability class-attr
  `supports_keyterms: bool = False`. `NormalizedTranscript` gains
  `biasing: dict | None` — `{"applied": bool, "terms_sent": int,
  "terms_truncated": int}`, **counts only, never the term strings**
  (term lists are customer data; the safe thing is the default) —
  threaded through `to_dict()`/`transcript_from_dict()`. Helpers
  `biasing_metadata()` / `unsupported_biasing()`. Adapters without
  keyterm support report requested terms as not applied instead of
  failing; per-provider limits TRUNCATE with counts, never error.
- **Keyterm wiring on existing adapters**: ElevenLabs Scribe `keyterms`
  multipart list (<50 chars / ≤5 words / ≤1000 terms, prohibited chars
  filtered; +20% surcharge noted), AssemblyAI `keyterms_prompt` (≤6
  words per phrase, ≤1000 words total; the legacy `word_boost` pair is
  never sent — gone from current docs).
- **New adapters** (`stt/providers/`, registered as built-ins):
  `deepgram` (Nova-3 `/v1/listen`, raw-bytes body, `Token` auth,
  `diarize_model` — never the deprecated `diarize` boolean; keyterm =
  repeated query param with the ported ~500-token budget estimator,
  legacy `term:weight` syntax and duplicates truncated; keyterm add-on
  $0.0013/min noted), `gladia` (upload→create→poll, solaria-1 pinned
  explicitly), `soniox` (upload→create→poll→fetch with mandatory
  file cleanup every run; sub-word token merge into words), `speechmatics`
  (multipart submit + poll + transcript fetch; melia-1 wire language
  "multi" + hints, `is_eos`-split derived utterances, "UU" → no speaker),
  `xai-stt` (single multipart POST, file field last, `format`+`language`
  pair rules, repeated `keyterm` fields ≤100 × 50 chars; no model
  parameter exists — nothing to pin). Gladia/Soniox/Speechmatics ship
  `supports_keyterms = False` (their vocabulary params are not covered
  by the verified sources); their `provider_options` reach the request
  body for hosts that own that decision. New settings:
  `DEEPGRAM_*`, `GLADIA_*`, `SONIOX_*`, `SPEECHMATICS_*`, `XAI_API_KEY`
  / `XAI_STT_URL`.
- **Surfaces**: `llm.transcribe` schema (+ committed
  `schemas/functions/llm.transcribe.json`), the HTTP transcribe
  serializer/DTO and `services.transcribe()` accept `keyterms` +
  `provider_options` (top-level schema stays `additionalProperties:
  false`; the free-form zone is inside `provider_options` only); the
  result transcript carries the `biasing` block. `llm.stt_catalog` /
  `services.stt_catalog()` entries gain `supports_keyterms`.

### Changed
- `services.transcribe()` threads the seam kwargs to adapters ONLY when
  provided, so out-of-tree adapters written against the pre-seam
  signature keep working until a caller actually uses biasing.

## [0.2.10] — 2026-07-17

Fleet follow-up to stapel-core 0.12.0 (legacy shim sweep). No source
changes needed. Full suite green against core 0.12.0.

### Changed
- `stapel-core` dependency ceiling `<0.12` → `<0.13`.

## [0.2.9] — 2026-07-17

### Changed
- `stapel-core` ceiling raised `>=0.10,<0.11` → `>=0.10,<0.12` (core 0.11
  fleet re-pin: default bus, nav, config-checks, error params/language —
  additive for modules). Suite green against core 0.11.2 (incl. the
  `anthropic` extra), no code changes needed.

## [0.2.8] — 2026-07-16

### Changed
- **v1 canon sweep §60** (api-versioning.md §2, §6): URL set moved to
  `urls_v1.py` (paths now relative: `llm/...`); the new root `urls.py`
  mounts it under `api/v1/`. Host mount `agent/` unchanged: endpoints now
  serve at `/agent/api/v1/llm/...`; bare `/agent/api/llm/...` no longer
  exists (sweep lands before the §3 API00x gates are enabled). Callers
  (stapel-translate AgentProvider) move to `{AGENT_URL}/api/v1/llm/complete`
  in stapel-translate 0.4.8. No contract artifacts in this repo yet.
- Lint hygiene to a clean `stapel-verify`: explicit `# noqa` on pre-existing
  findings.

## [0.2.6] - 2026-07-09

### Added
- Per-registration STT model pin: `SttProvider.speech_model` class-attr
  (the STT mirror of fixing a model on an LLM registration). Setting it on
  a subclass forces one engine/model for that registered name, overriding
  the provider's configured default (`WHISPER_MODEL` /
  `ELEVENLABS_STT_MODEL` / `ASSEMBLYAI_MODEL`); `None` (the default) keeps
  the settings-driven behaviour, so existing registrations are unchanged.
  New `effective_model()` (pin-or-default) and `default_speech_model()`
  (the configured default; providers override it to read their setting)
  helpers on the ABC; the three built-in adapters now send
  `effective_model()`. Two registrations of one adapter class can carry
  different models without a settings change or a fork.
- New comm function `llm.stt_catalog` (committed schema in
  `schemas/functions/llm.stt_catalog.json`, handler in `functions.py`,
  `services.stt_catalog()`): takes no arguments and returns the addressable
  STT surface — `{status, providers: [{name, available, model,
  pinned_model, supports_diarization, supported_languages, cost_per_hour}],
  default_provider, fallback_chain, language_routes}`. Each `model` is the
  registration's effective model (the `speech_model` pin, else the
  configured default); an unresolvable entry is listed `available: false`
  with an `error` rather than silently dropped. Read-only — writes no
  PromptLog row.

### Notes
- **Semver:** strictly this is a MINOR release (a new comm verb + a new
  ABC surface `speech_model`/`effective_model`), but it is held to a PATCH
  (`0.2.6`) by studio's `stapel-agent < 0.3` floor. That is safe here
  because every change is purely additive and backward-compatible: the new
  `speech_model` defaults to `None` (unchanged behaviour), the built-in
  adapters emit the same model as before when unpinned, and `llm.stt_catalog`
  is a brand-new verb touching no existing surface. A dedicated `0.3.0`
  (with a coordinated bump of studio's floor) is deferred until a change
  that actually warrants breaking the floor lands.

- Design note only (no implementation): `docs/streaming-seam.md` sketches
  where a future streaming seam would sit (provider ABC `stream_complete` +
  `supports_streaming`, a `complete_stream()` service generator, an additive
  `emits`-style wire surface) and the invariants it must preserve
  (chunk order, backpressure, wire compatibility, failure parity,
  one-row-per-call ledger). Input for a future design, not a commitment.

## [0.2.5] - 2026-07-09

### Added
- Per-call output-token cap: `llm.complete` payload (comm) and
  `services.complete`/`complete_json` accept an optional `max_tokens`
  integer overriding the configured `STAPEL_AGENT["MAX_TOKENS"]` for that
  call — long structured outputs (file manifests, findings with inline
  tests) raise the ceiling per call instead of a global bump; short calls
  can bound cost. New `LlmProvider.supports_max_tokens` capability flag
  (same discipline as `supports_images`): the kwarg travels only to
  providers that declare it — `anthropic` and `openai-compat` do; a
  requested cap on a non-supporting provider (e.g. `claude-code`) is
  ignored with a logged warning and the configured default stays in
  effect. Pre-existing provider subclasses with older `complete()`
  signatures keep working untouched. The text-keyed prompt cache does not
  see the cap — hosts enabling `CACHE_LOOKUP` for a source should keep
  that source's budget stable (the default policy caches translate only).

## [0.2.4] - 2026-07-09

### Added
- `llm.complete` payload schema admits an optional `role` string — an opaque
  caller tag (e.g. the calling role in a multi-role pipeline) for provider
  routing, override providers and observability. The default completion
  pipeline ignores it. Previously `additionalProperties: false` refused any
  tagged call as soon as schema validation was on (in-process comm callers
  hit `SchemaValidationError`), while stacks that override the provider
  *and* drop the schema masked the mismatch.

## [0.2.3] - 2026-07-08

### Changed
- Admin-suite AS-5: decorated `PromptLog` `@access.ops` (a delivery/audit
  ledger written exclusively by the `services.py` completion pipeline — no
  staff add/change/delete workflow through the admin) and swapped
  `PromptLogAdmin`'s base class to `stapel_core.django.admin.base.StapelModelAdmin`,
  which now enforces the read-only contract instead of the three hand-rolled
  `has_*_permission` overrides. No model in this repo carries credential
  material, so no `@access.secret` classification applies (every provider
  API key is read lazily from settings, never persisted).

## [0.2.2] - 2026-07-06

### Changed
- Pinned `stapel-core` to the `>=0.8,<0.9` window (library-standard §7.1: one
  minor window; floor `0.8.0` is published on PyPI — no pin into the void).
- CI: added the release-track job (library-standard §7.4) — installs the package
  the way an end user does (`pip install .`, dependencies resolved from PyPI
  strictly by the declared pins, no git-main core, no editable siblings), asserts
  `stapel-core` resolves inside the `0.8` window, and runs an import smoke.
  Advisory (continue-on-error) until the whole stapel graph is on PyPI; becomes
  the blocking precondition for a `vX.Y.Z` tag once it is.


## [0.2.1] - 2026-07-06

### Packaging
- Tests excluded from the built wheel/sdist (the `stapel_agent.tests`
  subpackage is no longer listed in `[tool.setuptools] packages`). Added
  `[project.urls]`, completed the trove classifiers (MIT/OSI, Python 3.13,
  `Typing :: Typed`, OS Independent, `3 :: Only`, Development Status) and a
  `[tool.ruff]` lint section (single source shared with the git hooks/CI).


## [0.2.0] - 2026-07-05

### Changed (breaking — custom `CachePolicy` subclasses)
- **The prompt cache key now includes provider + resolved model + model
  size.** `CachePolicy.lookup()` and `.store()` gained three keyword-only
  parameters — `provider`, `model` (the resolved `MODELS[model_size]`
  after `resolve_model`) and `model_size`:

  ```python
  def lookup(self, prompt, system_prompt, source, *,
             provider, model, model_size) -> str | None: ...
  def store(self, prompt, system_prompt, source, response, *,
            provider, model, model_size) -> None: ...
  ```

  Previously the key was prompt + system_prompt + source only, so a
  cached "small" answer could satisfy a "large" request, an explicit
  `provider=` collided with the default, and bumping a model version in
  `MODELS` did not invalidate stale rows (up to `CACHE_TTL`).

  **Migration:** a custom `CachePolicy` must add the three keyword-only
  parameters to its `lookup`/`store` overrides (and fold them into its
  key if it wants correctness across sizes/providers/model versions). A
  policy that ignored them would keep the old collision behaviour, so
  they are required, not defaulted — the mismatch surfaces immediately as
  a `TypeError` at call time rather than as a silent wrong-answer cache
  hit. The default `PromptLogCachePolicy` filters on `model` +
  `model_size` + `metadata.provider`.

### Fixed
- **Unknown STT provider name no longer aborts the fallback chain.** An
  unregistered name in `STT_LANGUAGE_ROUTES` / `STT_FALLBACK_CHAIN` (e.g.
  the docstring's own `"gigaam"` example) is a config error, not bad
  audio — `transcribe()` now skips it and walks to the next provider,
  consistent with the registered-but-unloadable (`ImportError`) branch.
  A fatal `TranscriptionError` raised from *within* a provider's
  `transcribe()` (bad input, auth) still stops the walk. System check
  `W004` still warns about unknown names at startup.
- **`timeout_seconds=0` / negatives are now rejected at the boundary
  instead of silently defaulting or crashing.** The four adapters
  (whisper-http, elevenlabs, assemblyai, openai-images) replaced the
  falsy `int(timeout_seconds or <default>)` with `<default> if
  timeout_seconds is None else int(timeout_seconds)`, so an explicit `0`
  is no longer coerced to the default. `timeout_seconds` now carries a
  `minimum: 1` constraint in the request serializers and the
  `llm.transcribe` / `llm.generate_image` comm schemas — `0` and
  negatives are HTTP 400 / schema errors rather than a silent default or
  an uncaught `urllib3` `ValueError` → HTTP 500.

### Added
- **`timeout_seconds` on the `llm.generate_image` comm surface** — the
  HTTP view already accepted it; the comm schema and function now do too
  (with `minimum: 1`), aligning the two surfaces.

## [0.1.1] - 2026-07-05

### Added
- **Vision input on `llm.complete`** (HTTP + comm, backward-compatible
  additive): optional `images` — each entry `{url}` or `{data_b64,
  mime?}` (raw bytes never travel the wire; in-process callers pass
  `ImageRef(data=...)`). `AnthropicProvider` maps refs to image content
  blocks (url/base64 source), `OpenAICompatProvider` to `image_url`
  parts (url or data URI); `claude-code` has no vision and fails fast
  with `status: "failure"`. New `LlmProvider.supports_images` class
  attribute — the service passes the `images` kwarg only when non-empty
  and only to providers that opt in, so pre-vision provider subclasses
  keep working unchanged.
- **Cache correctness for multimodal requests**: the prompt cache is
  text-keyed, so image requests bypass lookup and store, and the default
  `PromptLogCachePolicy.lookup()` now excludes multimodal ledger rows —
  identical text over different pixels never collides in either
  direction. Vision ledger rows record `metadata.images = {count,
  kinds}`, never bytes.
- **Image generation surface** — `POST api/llm/generate-image` + the
  `llm.generate_image` comm Function (`{prompt, size?, n? (1-10),
  provider?}` → `{status, images: [{url? | data_b64?, mime}],
  provider_used}`); failures stay HTTP 200 with `status: "failure"`.
  Module boundary: the agent returns raw provider results and writes the
  ledger — storing images into stapel-cdn/asset libraries is the
  CALLER's job (system-design §8.8 gateway verb does metering/placement).
- **Image provider registry** — third instance of the house merge
  pattern: `images.BUILTIN_IMAGE_PROVIDERS` +
  `STAPEL_AGENT["IMAGE_PROVIDERS"]` overlay +
  `register_image_provider()` runtime; `DEFAULT_IMAGE_PROVIDER`
  (default `openai-images`); system checks `stapel_agent.W005`
  (unimportable / non-`ImageGenProvider` entry) / `W006` (unknown
  default). `ImageGenProvider` ABC (`generate(*, prompt, size, n,
  timeout_seconds) -> list[GeneratedImage]`, `supported_sizes` gate)
  with the fatal/retryable taxonomy
  (`ImageGenError`/`RetryableImageGenError`).
- **Built-in `openai-images` adapter**: OpenAI-compatible
  `POST {base}/images/generations` (OpenAI, Together, self-hosted
  compatibles) — settings `IMAGES_BASE_URL`/`IMAGES_API_KEY` (both fall
  back to the `OPENAI_COMPAT_*` pair) + optional `IMAGES_MODEL`; maps
  `b64_json`/`url` entries to `GeneratedImage`. Other vendors
  (Stability, ...) are an app-layer recipe in MODULE.md.
- **Ledger coverage for image generation**: `source=generate_image` rows
  (`model` = provider name, prompt logged, response NOT logged raw —
  `{count, mimes, bytes_total}` in metadata, token columns NULL).
  Migration `0003` extends the `source` choices.
- Public API additions (PEP 562, still Django-free at import):
  `generate_image`, `ImageRef`, `ImageGenProvider`, `GeneratedImage`,
  `register_image_provider`, `registered_image_providers`.
- **Transcription surface** — `POST api/llm/transcribe` + the
  `llm.transcribe` comm Function (URLs only over the wire, never raw
  audio bytes), backed by a second open registry with the same merge
  semantics as the LLM one: `STAPEL_AGENT["STT_PROVIDERS"]` overlay over
  `stt.BUILTIN_STT_PROVIDERS`, `None`/`""` removes a name,
  `register_stt_provider()` at runtime. Built-in adapters:
  `whisper-http` (OpenAI Whisper API or self-hosted faster-whisper —
  accepts url/path/bytes refs), `elevenlabs` (Scribe, diarization),
  `assemblyai` (async submit+poll, diarization). App-layer engines
  (GigaAM, ...) subclass `SttProvider` — see the MODULE.md worked
  example.
- **STT routing** (`stt/router.py`): explicit `provider` in the request
  (pinned — no fallback) > `STT_LANGUAGE_ROUTES[lang]` matrix >
  `DEFAULT_STT_PROVIDER` + `STT_FALLBACK_CHAIN`. The chain advances on
  `RetryableTranscriptionError` only (429/5xx/timeouts); fatal
  `TranscriptionError` (bad audio, auth) never falls back.
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
  (click-to-timestamp).
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

Initial release — Python port of a prior NestJS service (the legacy LLM
facade), per the design fixed in the Stapel monorepo's
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
- **HTTP surface**: `POST api/llm/complete` and
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

### Deliberately dropped from the source service
- The `claude` module (execute/stream proto-harness) and the `terminal`
  module (node-pty shell) — out of scope for a Django library.
- The ApiKey CRUD/entity — service auth is stapel-core's
  `SERVICE_API_KEY` / `IsServiceRequest`; no module-owned key table.
- OAuth credential reading from `~/.claude/.credentials.json` and the
  background token-refresh hack — the CLI provider owns its auth; the
  facade itself is PAYG-API-key only.
- Any Node/CLI dependency in a default path — `claude-code` is a host
  opt-in.
