# stapel-agent

[![CI](https://github.com/usestapel/stapel-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/usestapel/stapel-agent/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/usestapel/stapel-agent/graph/badge.svg)](https://codecov.io/gh/usestapel/stapel-agent)

> LLM facade — one JSON-completion/translation surface in front of swappable model providers, with a prompt cache and a token ledger

Part of the [Stapel framework](https://github.com/usestapel) — composable Django apps for building production-grade platforms.

Python port of the `the legacy agent service` NestJS service. Same HTTP paths and contracts
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

# urls.py — paths stay 1:1 with the legacy agent service under the agent/ mount
urlpatterns = [
    ...
    path("agent/", include("stapel_agent.urls")),
]
```

Two surfaces, same contracts:

```bash
# HTTP (service-to-service: X-API-KEY, or a staff session)
POST /agent/api/llm/complete   {"prompt": "...", "model": "small|medium|large",
                                "provider"?: "...", "system_prompt"?: "..."}
POST /agent/api/llm/translate  {"from": "auto", "to": "de", "entries": {"key": "text"}}
```

```python
# comm (in-process in a monolith, transport chosen by STAPEL_COMM)
from stapel_core.comm import call

call("llm.complete", {"prompt": "...", "model": "small"})
call("llm.translate", {"from_lang": "auto", "to": "de", "entries": {...}})
```

Responses follow the the legacy agent service contract: LLM failures are **HTTP 200** with
`{"status": "failure", "reason": ...}` — 4xx/5xx are reserved for request
validation and auth. Successful completions return the parsed JSON in
`result`, prose around it in `comment`, and snake_case `usage`
(`input_tokens` / `output_tokens`).

Every provider call writes a `PromptLog` row: model, size, source, status,
duration and the full token ledger (input / output / thinking / cache-read /
cache-write) — per-user and per-source cost accounting needs no other table.

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
| `CACHE_LOOKUP` | `{"llm_facade": False, "translate": True}` | Per-source cache-by-prompt toggle (used by the default cache policy) |
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
`DEFAULT_PROVIDER` override). Unlike the legacy agent service there is no OAuth credential
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

## License

MIT — see [LICENSE](LICENSE)
