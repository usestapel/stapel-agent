# Changelog

All notable changes to stapel-agent are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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
