"""What a completion cost, and why the obvious answer under-counts.

Prices are per MILLION tokens, in USD, and every entry carries the source it
was read from and the date it was read. That is the whole discipline: a price
without a provenance line is a number someone remembered, and model prices
move. An unknown model returns 0.0 with a warning rather than a guess — a
fabricated cost is worse than a missing one, because it gets summed.

TWO WAYS THE OBVIOUS COUNT IS WRONG
-----------------------------------
**Reasoning tokens.** They are billed, and providers disagree about whether
they are already inside the completion count. The measured finding, carried
over verbatim from the harness:

    P112 finding: xAI ``completion_tokens`` EXCLUDES reasoning (OpenAI-style
    providers include it), so estimating from completion_tokens alone
    under-counts every xAI run; the exact bill ships in
    ``usage.cost_in_usd_ticks``.

So the billed output is completion + reasoning on xAI and completion alone
elsewhere, and :func:`billed_output_tokens` is the single place that knows it.

**An exact bill beats an estimate.** When a provider reports what it actually
charged, that number wins and ``cost_basis`` says so. An estimate that silently
replaces a known figure is how a ledger drifts from an invoice.

Prices are applied at call time, not at read time. Storing the cost as computed
when the call was made keeps a year-old row honest after the rate card changes;
recomputing later would quietly restate history.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# verified https://docs.claude.com/en/docs/about-claude/pricing.md fetched 03 Jul 2026
# {model_id: {"input": USD/MTok, "output": USD/MTok}}
PRICES_USD_PER_MTOK: dict[str, dict[str, float]] = {
    # Claude Sonnet 5 — released 30 Jun 2026. Introductory $2/$10 per MTok
    # through 31 Aug 2026; standard $3/$15 from 1 Sep 2026 (intro is active now).
    # ⚠️ New tokenizer emits ~30% more tokens for the same text than Sonnet 4.6.
    "claude-sonnet-5": {"input": 2.0, "output": 10.0},
    # Legacy Sonnet — kept for comparison / explicit override.
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
    "claude-sonnet-4-5": {"input": 3.0, "output": 15.0},
    "claude-haiku-4-5": {"input": 1.0, "output": 5.0},
    "claude-opus-4-8": {"input": 5.0, "output": 25.0},
    # --- P75 non-Anthropic summary models (verified 2026-07-10, primary docs;
    # --- sources + notes in pipeline/summarize/providers.py) ----------------
    # xAI grok-4.5: docs.x.ai/developers/models ($2/$6, ctx 500k)
    "grok-4.5": {"input": 2.0, "output": 6.0},
    # --- P112 expansion-gate candidates (NOT in the UI catalog until they
    # --- pass the full gate). Prices verified LIVE 2026-07-17 via the
    # --- authenticated GET /v1/language-models (ticks 12500/25000; one
    # --- tick = 1e-10 USD -> $1.25/$2.50 per MTok). Web aggregators
    # --- claimed $2/$6 for the 4.20 SKU - refuted by the API's own catalog.
    "grok-4.3": {"input": 1.25, "output": 2.5},
    "grok-4.20-0309-non-reasoning": {"input": 1.25, "output": 2.5},
    # OpenAI gpt-5.6-luna: platform.openai.com/docs/pricing (short-context)
    "gpt-5.6-luna": {"input": 1.0, "output": 6.0},
    # Meta Model API muse-spark-1.1 (public preview 2026-07-09):
    # ai.developer.meta.com pricing page ($1.25/$4.25, cached in $0.15)
    "muse-spark-1.1": {"input": 1.25, "output": 4.25},
    # google/gemini-3.5-flash via OpenRouter (public /api/v1/models catalog)
    "or-gemini-3.5-flash": {"input": 1.5, "output": 9.0},
}


# Trailing dated snapshot suffix, e.g. "claude-haiku-4-5-20251001" -> base alias.
_DATE_SUFFIX = re.compile(r"-\d{8}$")

#: Providers whose completion count EXCLUDES reasoning tokens. Membership here
#: is a measured fact about a provider's accounting, not a preference.
_REASONING_EXCLUDED_FROM_COMPLETION = frozenset({"xai"})

#: xAI bills 1 tick = 1e-10 USD (live decomposition of
#: usage.cost_in_usd_ticks against the /v1/language-models rate card).
USD_PER_TICK = 1e-10


def _normalize_model(model: str) -> str:
    """Strip a trailing ``-YYYYMMDD`` snapshot suffix to match a base alias."""
    return _DATE_SUFFIX.sub("", model or "")


def estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    """Estimate USD cost for a completion.

    Unknown model -> 0.0 + a warning. Never a fabricated number: a made-up
    price does not stay isolated, it gets summed into a total someone acts on.
    """
    prices = PRICES_USD_PER_MTOK.get(model) or PRICES_USD_PER_MTOK.get(
        _normalize_model(model)
    )
    if not prices:
        logger.warning("pricing: unknown model %r -> cost_usd=0.0", model)
        return 0.0
    return (tokens_in * prices["input"] + tokens_out * prices["output"]) / 1_000_000


def is_priced(model: str) -> bool:
    """Whether a real price exists for ``model``.

    Exposed so a caller can tell "this call was free" from "we do not know what
    this call cost" — 0.0 means both, and only one of them is good news.
    """
    return bool(
        PRICES_USD_PER_MTOK.get(model) or PRICES_USD_PER_MTOK.get(_normalize_model(model))
    )


def billed_output_tokens(provider: str, output_tokens: int, thinking_tokens: int) -> int:
    """Output tokens as the provider bills them.

    See the module docstring: on xAI the completion count excludes reasoning,
    everywhere else it already includes it. Adding them unconditionally would
    double-count reasoning on every OpenAI-style provider, which is the same
    size of error in the other direction.
    """
    if provider in _REASONING_EXCLUDED_FROM_COMPLETION:
        return int(output_tokens or 0) + int(thinking_tokens or 0)
    return int(output_tokens or 0)


def cost_fields(
    *,
    model: str,
    provider: str,
    input_tokens: int,
    output_tokens: int,
    thinking_tokens: int = 0,
    cost_in_usd_ticks: int | None = None,
) -> dict:
    """The cost view of one completion.

    ``cost_basis`` tells the reader which number to trust: ``provider_ticks``
    when the provider reported its own charge, ``pricing_estimate`` when this
    table was used, and ``unpriced`` when neither was available — that last one
    exists so an unknown model is visible as unknown instead of as free.
    """
    billed_out = billed_output_tokens(provider, output_tokens, thinking_tokens)
    actual = (
        round(cost_in_usd_ticks * USD_PER_TICK, 6)
        if isinstance(cost_in_usd_ticks, int) and not isinstance(cost_in_usd_ticks, bool)
        else None
    )
    if actual is not None:
        basis = "provider_ticks"
    elif is_priced(model):
        basis = "pricing_estimate"
    else:
        basis = "unpriced"
    return {
        "billed_output_tokens": billed_out,
        "cost_usd": actual if actual is not None else estimate_cost(
            model, int(input_tokens or 0), billed_out
        ),
        "cost_basis": basis,
    }


__all__ = [
    "PRICES_USD_PER_MTOK",
    "USD_PER_TICK",
    "billed_output_tokens",
    "cost_fields",
    "estimate_cost",
    "is_priced",
]
