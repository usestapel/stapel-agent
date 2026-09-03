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
recomputing later would quietly restate history. Since 0.12.0 that storage is
real: ``PromptLog.cost_usd`` / ``cost_basis`` are written by the completion
pipeline from this module's output, once, and never revisited.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# verified https://platform.claude.com/docs/en/about-claude/pricing.md
# (the docs.claude.com URL now 302s there) fetched 21 Aug 2026
# {model_id: {"input": USD/MTok, "output": USD/MTok}}
#
# Base input/output only. Deliberately NOT modelled here, because each is a
# multiplier on a request shape this facade does not choose: cache
# writes/reads (1.25x/2x/0.1x — the cache token columns are recorded, the
# multiplier is not applied), the Batch API's 50% discount, the
# ``inference_geo="us"`` 1.1x, and fast mode on Opus 5 / 4.8 ($10/$50). A row
# whose call used one of those is under-counted, and honestly so: this is
# ``pricing_estimate``, and ``provider_ticks`` beats it whenever the provider
# reports its own charge.
PRICES_USD_PER_MTOK: dict[str, dict[str, float]] = {
    # Claude Sonnet 5 — released 30 Jun 2026 at an introductory $2/$10 per
    # MTok "through 31 Aug 2026", with $3/$15 scheduled for 1 Sep 2026.
    # THAT INCREASE WAS CANCELLED: as of the 21 Aug 2026 price list, $2/$10
    # "is now the standard price" and "the previously scheduled increase
    # ... will not occur". So this table needs no effective-date machinery
    # for it, and the number below stays correct after 31 Aug 2026 with no
    # release. Costs already computed and stored at $2/$10 stay right too.
    # ⚠️ New tokenizer emits ~30% more tokens for the same text than Sonnet 4.6.
    "claude-sonnet-5": {"input": 2.0, "output": 10.0},
    # Legacy Sonnet — kept for comparison / explicit override.
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
    "claude-sonnet-4-5": {"input": 3.0, "output": 15.0},
    "claude-haiku-4-5": {"input": 1.0, "output": 5.0},
    # Fable/Mythos 5 — the top tier, and the reason an unpriced row is worth
    # shouting about: at 5x Opus, one silently-zero month is real money.
    "claude-fable-5": {"input": 10.0, "output": 50.0},
    "claude-mythos-5": {"input": 10.0, "output": 50.0},
    # The Opus family bills flat across revisions; each is listed rather
    # than aliased so a provider's echoed model id resolves to its own key
    # and a future divergence is a one-line edit, not a refactor.
    "claude-opus-5": {"input": 5.0, "output": 25.0},
    "claude-opus-4-8": {"input": 5.0, "output": 25.0},
    "claude-opus-4-7": {"input": 5.0, "output": 25.0},
    "claude-opus-4-6": {"input": 5.0, "output": 25.0},
    "claude-opus-4-5": {"input": 5.0, "output": 25.0},
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
    # OpenAI gpt-5.6-luna, short-context standard tier.
    # $1.00/$6.00 when first read 2026-07-10; re-verified 3 Sep 2026 against
    # developers.openai.com/api/docs/pricing AND the model's own page, both
    # of which now publish $0.20/$1.20 — the list price fell 5x. Corrected
    # here rather than left: an OVER-stated rate inflates every estimate that
    # touches it, which is the same defect as an unpriced call with the sign
    # flipped, and rows already computed at the old rate keep their stored
    # cost because pricing is applied at call time.
    # The "short-context" qualifier is real and now confirmed: prompts over
    # 272K input tokens bill 2x input / 1.5x output for the whole request.
    # Not modelled, for the reason the module docstring gives — this is
    # ``pricing_estimate``, and a long-context call is under-counted honestly.
    "gpt-5.6-luna": {"input": 0.2, "output": 1.2},
    # --- The models a live deployment was calling while unpriced -----------
    # A client fleet points ALL THREE ladder rungs at gpt-5.2 through the
    # openai-compat provider, so every AI-composer call — vision draft,
    # category descent, characteristic filling — stored cost_basis=unpriced
    # and metering could not cost the feature at all (352 rows over 2026-09-02
    # alone). W018 below is why that can no longer happen quietly.
    #
    # verified https://developers.openai.com/api/docs/pricing (the
    # platform.openai.com/docs/pricing URL now 301s there) AND the model's own
    # page https://developers.openai.com/api/docs/models/gpt-5.2, both fetched
    # 3 Sep 2026. STANDARD tier, which is what this facade calls: the page
    # lists Batch and Flex separately at exactly half ($0.875/$7.00), and
    # third-party aggregators were quoting that half as if it were OpenAI's
    # standard price. Same trap as the grok-4.20 entry below — the primary
    # source wins. Cached input ($0.175) is recorded by the provider but not
    # modelled here, for the reason the module docstring gives.
    "gpt-5.2": {"input": 1.75, "output": 14.0},
    "gpt-5.2-pro": {"input": 21.0, "output": 168.0},
    # Meta Model API muse-spark-1.1 (public preview 2026-07-09):
    # ai.developer.meta.com pricing page ($1.25/$4.25, cached in $0.15)
    "muse-spark-1.1": {"input": 1.25, "output": 4.25},
    # google/gemini-3.5-flash via OpenRouter (public /api/v1/models catalog)
    "or-gemini-3.5-flash": {"input": 1.5, "output": 9.0},
}


# --- Embeddings ---------------------------------------------------------
# A SECOND table, because an embeddings call has a different shape, not just
# different numbers: it bills input tokens and has no output tokens at all, so
# a row of ``{"input", "output"}`` and an estimate that multiplies both cannot
# express it. USD per MILLION input tokens.
#
# verified https://developers.openai.com/api/docs/pricing, fetched 3 Sep 2026.
# The page lists embeddings with an input price and no output column.
EMBEDDING_PRICES_USD_PER_MTOK: dict[str, float] = {
    "text-embedding-3-small": 0.02,
    "text-embedding-3-large": 0.13,
    "text-embedding-ada-002": 0.10,
}

# Deliberately NO entry here for a self-hosted model. A library that shipped
# ``{"bge-m3": 0.0}`` would be asserting a price about somebody else's
# endpoint — the same fabrication as guessing a published one, and harder to
# spot because it looks like good news. The host declares its own: see
# ``extra_prices`` below and ``STAPEL_AGENT["EMBEDDING_PRICES"]``.


# Trailing dated snapshot suffix -> base alias. Providers date their snapshots
# in two spellings and a table that knows only one prices the base id and
# leaves every snapshot unpriced — the same blindness as a missing row, one
# string away:
#   Anthropic  "claude-haiku-4-5-20251001"   -YYYYMMDD
#   OpenAI     "gpt-5.2-2025-12-11"          -YYYY-MM-DD
# Only a date is stripped. A normalizer greedy enough to eat any trailing
# segment would let "gpt-5.2-turbo" answer with gpt-5.2's price, which is a
# fabricated cost with extra steps.
_DATE_SUFFIX = re.compile(r"-\d{8}$|-\d{4}-\d{2}-\d{2}$")

#: Providers whose completion count EXCLUDES reasoning tokens. Membership here
#: is a measured fact about a provider's accounting, not a preference.
_REASONING_EXCLUDED_FROM_COMPLETION = frozenset({"xai"})

#: xAI bills 1 tick = 1e-10 USD (live decomposition of
#: usage.cost_in_usd_ticks against the /v1/language-models rate card).
USD_PER_TICK = 1e-10


def _normalize_model(model: str) -> str:
    """Strip a trailing dated snapshot suffix (either spelling) to match a base alias."""
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


def embedding_price(model: str, extra_prices: dict | None = None) -> float | None:
    """USD per MTok of input for *model*, or None if nothing prices it.

    ``extra_prices`` is the host's own table, and it WINS over the shipped
    one: a negotiated rate is a fact about this deployment's invoice, and a
    published list price is only the default.

    Returning None for a miss (rather than 0.0, as the completion table does)
    is the whole point. On the completion side the 0.0 is paired with
    ``cost_basis="unpriced"`` and the column carries the basis, so the pair
    stays honest. Here the cost column itself is left NULL, which is what the
    audio surfaces already do, and it means a SUM over the table cannot
    quietly absorb an unknown as if it were nothing.

    A DECLARED 0.0, by contrast, is a real answer: a host that runs its own
    embedder is not billed per query, and saying so is different from never
    having asked. That difference survives here because 0.0 is a value in the
    table and a miss is not.
    """
    for table in (extra_prices or {}, EMBEDDING_PRICES_USD_PER_MTOK):
        for key in (model or "", _normalize_model(model or "")):
            if key and key in table:
                return float(table[key])
    return None


def embedding_cost_fields(
    *,
    model: str,
    input_tokens: int | None,
    extra_prices: dict | None = None,
) -> dict:
    """The cost view of one embeddings call.

    Two ways to end up unpriced, and they are the same finding from opposite
    ends: nothing prices the model, or the provider reported no billable
    quantity to price. A rate with nothing to multiply is not a cost.
    """
    rate = embedding_price(model, extra_prices)
    if rate is None or input_tokens is None:
        return {"cost_usd": None, "cost_basis": "unpriced"}
    return {
        "cost_usd": round(int(input_tokens) * rate / 1_000_000, 8),
        "cost_basis": "pricing_estimate",
    }


__all__ = [
    "EMBEDDING_PRICES_USD_PER_MTOK",
    "PRICES_USD_PER_MTOK",
    "USD_PER_TICK",
    "billed_output_tokens",
    "cost_fields",
    "embedding_cost_fields",
    "embedding_price",
    "estimate_cost",
    "is_priced",
]
