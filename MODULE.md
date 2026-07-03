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
  `stapel-translate`'s `AgentProvider` already POSTs to them.

## What this module provides

| Area | Contents |
|---|---|
| Models (`models.py`) | `PromptLog` (immutable per-call ledger: `source`, `model`, `model_size`, `prompt`, `system_prompt`, `response`, `status` success/failure/timeout/error, `error_message`, `input_tokens`/`output_tokens`/`thinking_tokens`/`cache_read_tokens`/`cache_write_tokens`, `duration_ms`, `user_id`, JSON `metadata`, `created_at`; doubles as the cache-by-prompt store) |
| Services (`services.py`) | `complete()` (cache lookup → provider → PromptLog row → `{status, result, usage}`), `complete_json()` (JSON-API system prompt + JSON extraction — the `llm.complete` surface), `translate()` (iron's translate flow), `get_provider()` (lazy dotted-path resolution), `JSON_API_SYSTEM_PROMPT` |
| Parsing (`parsing.py`) | `parse_json_response()` (direct JSON → fenced block → object anywhere → array anywhere; surrounding prose becomes `comment`), `parse_translation_response()` — ports of the legacy agent service's extractors, Django-free |
| HTTP API (`urls.py`, `views.py`) | `api/llm/complete`, `api/llm/translate` (both `IsServiceRequest \| IsStaffUser`; hosts mount the app under `agent/`). LLM failures are HTTP 200 with `status: "failure"` — the iron contract |
| Providers (`providers/`) | `LlmProvider` ABC + `ProviderResult`/`ProviderError`/`ProviderTimeout` (`providers/base.py`), `AnthropicProvider` (SDK, default), `OpenAICompatProvider` (any `/chat/completions` dialect), `ClaudeCodeCLIProvider` (opt-in `claude -p` spawn, never the default) |
| Public API (`__init__.py`, PEP 562 lazy) | `__all__ = ["LlmProvider", "ProviderResult", "agent_settings", "complete", "translate"]` — Django-free at import |

## Extension points (fork-free)

### Settings — `STAPEL_AGENT` namespace (`conf.py`)

`agent_settings = AppSettings("STAPEL_AGENT", ...)` from `stapel_core.conf`.
Resolution order per key: `settings.STAPEL_AGENT[key]` → flat Django setting of the
same name → environment variable → default. All keys are read **lazily at call time**
(never frozen at import); caches invalidate on `setting_changed`.

| Key | Default | What it customizes |
|---|---|---|
| `MODELS` | `{"small": "claude-haiku-4-5-20251001", "medium": "claude-sonnet-5", "large": "claude-opus-4-8"}` | The size → model map every request goes through. |
| `PROVIDERS` | `{"anthropic": ..., "openai-compat": ..., "claude-code": ...}` (dotted paths) | The provider registry. Resolved lazily per request via `import_string` in `services.get_provider` (not `import_strings` — an unknown/broken entry degrades to `status: "failure"`, never an import-time crash). |
| `DEFAULT_PROVIDER` | `"anthropic"` | Provider used when the request names none. |
| `ANTHROPIC_API_KEY` | `""` | Anthropic SDK key (read lazily per call). |
| `OPENAI_COMPAT_BASE_URL` / `OPENAI_COMPAT_API_KEY` | `""` | OpenAI-compatible endpoint + bearer token (OpenAI, DeepSeek, MiMo, GLM, Kimi). |
| `OPENAI_COMPAT_MODELS` | `{}` | Per-size model names for openai-compat; missing sizes fall back to `MODELS[size]`. |
| `CLI_BINARY` / `CLI_TIMEOUT` | `"claude"` / `120` | Claude Code CLI binary and the provider timeout (seconds). |
| `MAX_TOKENS` | `4096` | Completion token cap passed to providers. |
| `CACHE_LOOKUP` | `{"llm_facade": False, "translate": True}` | Per-source cache-by-prompt toggle (latest `success` row with identical prompt+system_prompt+source). |
| `CACHE_TTL` | `604800` | Cache window in seconds; expired rows are ignored. |

### LLM providers (dotted-path swap)

Implement the ABC `stapel_agent.providers.base.LlmProvider` and register the dotted
path — no fork:

```python
# myproject/llm.py
from stapel_agent import LlmProvider, ProviderResult

class AcmeProvider(LlmProvider):
    name = "acme"
    def complete(self, *, prompt, model, system_prompt=None):
        ...
        return ProviderResult(text=..., input_tokens=..., output_tokens=...)

# settings.py
STAPEL_AGENT = {
    "PROVIDERS": {**defaults, "acme": "myproject.llm.AcmeProvider"},
    "DEFAULT_PROVIDER": "acme",
}
```

ABC contract:

| Member | Signature | Contract |
|---|---|---|
| `complete` | `(*, prompt: str, model: str, system_prompt: str \| None = None) -> ProviderResult` | Return the completion; raise `ProviderError` on failure, `ProviderTimeout` on timeout (logged as status `timeout`). Fill every token field you can — the ledger is the point. |
| `resolve_model` | `(model_size: str, default: str) -> str` | Optional override: map a size to this backend's model name; *default* is the already-resolved `MODELS[model_size]` (see `OpenAICompatProvider`). |

Providers must read credentials lazily (at call time, via `agent_settings`), never at
import — and never crash the process for a missing optional dependency (raise
`ProviderError` with a clear message instead; see `AnthropicProvider`).

### Swappable models

None. `PromptLog` has a fixed `db_table` (`agent_prompt_log`) and no user FK — it
stores `user_id` as an opaque string, so it works with any (or no) user model. Extend
per-call data via `metadata` (JSON) or an app-layer model keyed by `PromptLog.id` —
do not fork to add columns.

### Serializer seams (`views.py`)

Both views mix in `SerializerSeamMixin` (`request_serializer_class` +
`get_request_serializer_class()`); subclass the view, swap the serializer, remount the
URL — HTTP method bodies stay untouched.

| View | Route (name) | Request serializer | Response |
|---|---|---|---|
| `LlmCompleteView` | `api/llm/complete` (`llm-complete`) | `CompleteRequestSerializer` | plain contract dict (`status`/`result`/`comment`/`reason`/`usage`) |
| `LlmTranslateView` | `api/llm/translate` (`llm-translate`) | `TranslateRequestSerializer` (maps wire key `"from"` → `from_lang`) | plain contract dict |

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
- **Don't fork to add a provider.** Subclass `LlmProvider` in the app layer and add
  the dotted path to `STAPEL_AGENT["PROVIDERS"]`.
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
above: a new/replacement LLM backend (`PROVIDERS` + `LlmProvider` subclass), different
model names (`MODELS`, `OPENAI_COMPAT_MODELS`), credentials, cache policy
(`CACHE_LOOKUP` / `CACHE_TTL`), request payload shape (serializer seam + URL remount),
enabling the CLI provider in a host image.

**Upstream contribution** (Stapel-owned, via the contribution pipeline) when the change
alters module-owned contracts or invariants: new columns/indexes on `PromptLog`
(migrations live here), new `PromptSource`/`PromptStatus` values, changes to the
`LlmProvider` ABC surface, the JSON-extraction rules in `parsing.py`, the HTTP/comm
response contracts or schemas, new endpoints or comm functions, bug fixes anywhere in
this repo.

If a needed seam does not exist (e.g. pluggable cache backends or a streaming
surface), the seam itself is an upstream contribution; the code that plugs into it
stays app-layer.
