"""What a call cost — and the two ways the obvious count is wrong.

Both failure modes here are silent and directional: one under-reports spend,
the other invents it. Neither raises, and both end up in a total someone acts
on.
"""

import pytest

from stapel_agent.pricing import (
    PRICES_USD_PER_MTOK,
    USD_PER_TICK,
    billed_output_tokens,
    cost_fields,
    estimate_cost,
    is_priced,
)


class TestTable:
    def test_every_price_has_both_directions(self):
        for model, prices in PRICES_USD_PER_MTOK.items():
            assert set(prices) == {"input", "output"}, model
            assert prices["input"] > 0 and prices["output"] > 0, model

    def test_output_is_never_cheaper_than_input(self):
        """Not a law of nature, but true of every rate card seen so far.

        If it ever flips, that is worth noticing deliberately rather than
        discovering as a wrong invoice.
        """
        for model, prices in PRICES_USD_PER_MTOK.items():
            assert prices["output"] >= prices["input"], model


class TestTheCardsThatWereMissing:
    """A model absent from the table costs 0.0 and gets summed as free.

    Every entry here was reachable from a running deployment while unpriced
    — the top of the range most of all, where one silent month is real
    money.
    """

    @pytest.mark.parametrize(
        "model,expected",
        [
            ("claude-opus-5", (5.0, 25.0)),
            ("claude-opus-4-8", (5.0, 25.0)),
            ("claude-opus-4-7", (5.0, 25.0)),
            ("claude-opus-4-6", (5.0, 25.0)),
            ("claude-opus-4-5", (5.0, 25.0)),
            ("claude-fable-5", (10.0, 50.0)),
            ("claude-mythos-5", (10.0, 50.0)),
            ("claude-sonnet-5", (2.0, 10.0)),
            ("claude-haiku-4-5", (1.0, 5.0)),
        ],
    )
    def test_verified_against_the_published_list(self, model, expected):
        prices = PRICES_USD_PER_MTOK[model]
        assert (prices["input"], prices["output"]) == expected

    def test_the_default_large_model_is_priced(self):
        """``conf.MODELS['large']`` shipping unpriced is the exact shape of
        the defect: the most expensive route, estimated at nothing."""
        from stapel_agent.conf import agent_settings

        for size in ("small", "medium", "large"):
            assert is_priced(agent_settings.defaults["MODELS"][size]), size


class TestSonnet5IntroductoryPricing:
    """The scheduled increase that isn't.

    Sonnet 5 launched at an introductory $2/$10 "through 31 Aug 2026", with
    $3/$15 booked for 1 September. That increase was CANCELLED — the
    21 Aug 2026 price list says $2/$10 "is now the standard price". So the
    right mechanism here is no mechanism: a date-aware entry that flipped to
    $3/$15 would make every row computed after August wrong, which is the
    opposite of what an effective-date pair was wanted for.
    """

    def test_the_intro_price_is_the_standard_price(self):
        assert PRICES_USD_PER_MTOK["claude-sonnet-5"] == {"input": 2.0, "output": 10.0}

    def test_the_estimate_does_not_move_with_the_calendar(self):
        """Locked deliberately: nothing in this module reads a clock, so
        1 September needs no release and produces no surprise."""
        import inspect

        import stapel_agent.pricing as pricing

        source = inspect.getsource(pricing)
        for clock in ("datetime", "date.today", "time.time", "timezone.now"):
            assert clock not in source, (
                f"pricing.py grew a clock ({clock}) — a rate card that "
                "changes by itself must be justified by a scheduled price "
                "change that is actually happening"
            )

    def test_a_september_call_costs_what_an_august_call_costs(self):
        before = estimate_cost("claude-sonnet-5", 1_000_000, 1_000_000)
        assert before == pytest.approx(12.0)


class TestEstimate:
    def test_a_million_of_each(self):
        assert estimate_cost("claude-sonnet-5", 1_000_000, 1_000_000) == pytest.approx(12.0)

    def test_dated_snapshot_suffix_resolves_to_the_base_alias(self):
        """Providers hand back `claude-haiku-4-5-20251001`; the table keys the base."""
        assert estimate_cost("claude-haiku-4-5-20251001", 1_000_000, 0) == pytest.approx(1.0)

    def test_an_unknown_model_costs_zero_and_says_so(self, caplog):
        """A fabricated price does not stay isolated — it gets summed."""
        assert estimate_cost("no-such-model", 1_000, 1_000) == 0.0
        assert not is_priced("no-such-model")

    def test_free_and_unknown_are_distinguishable(self):
        """0.0 means both, and only one of them is good news."""
        assert is_priced("claude-sonnet-5")
        assert not is_priced("no-such-model")


class TestReasoningTokens:
    """The measured finding: providers disagree about what completion counts.

    xAI's completion count EXCLUDES reasoning; OpenAI-style providers include
    it. Estimating from completion alone under-counts every xAI run — and
    adding reasoning unconditionally over-counts everywhere else by the same
    amount.
    """

    def test_xai_adds_reasoning(self):
        assert billed_output_tokens("xai", 500, 200) == 700

    def test_openai_style_does_not_double_count(self):
        assert billed_output_tokens("openai-compat", 500, 200) == 500

    def test_the_difference_reaches_the_cost(self):
        xai = cost_fields(
            model="grok-4.5", provider="xai",
            input_tokens=0, output_tokens=1_000_000, thinking_tokens=1_000_000,
        )
        other = cost_fields(
            model="grok-4.5", provider="openai-compat",
            input_tokens=0, output_tokens=1_000_000, thinking_tokens=1_000_000,
        )
        assert xai["cost_usd"] == pytest.approx(12.0)
        assert other["cost_usd"] == pytest.approx(6.0)


class TestCostBasis:
    def test_a_reported_charge_beats_an_estimate(self):
        """An estimate that silently replaces a known figure is how a ledger
        drifts from an invoice."""
        fields = cost_fields(
            model="grok-4.5", provider="xai",
            input_tokens=1_000_000, output_tokens=1_000_000,
            cost_in_usd_ticks=50_000_000_000,
        )
        assert fields["cost_basis"] == "provider_ticks"
        assert fields["cost_usd"] == pytest.approx(50_000_000_000 * USD_PER_TICK)

    def test_the_table_is_used_when_nothing_was_reported(self):
        fields = cost_fields(
            model="claude-sonnet-5", provider="anthropic",
            input_tokens=1_000_000, output_tokens=0,
        )
        assert fields["cost_basis"] == "pricing_estimate"
        assert fields["cost_usd"] == pytest.approx(2.0)

    def test_unknown_is_reported_as_unknown_not_as_free(self):
        fields = cost_fields(
            model="no-such-model", provider="anthropic",
            input_tokens=1_000, output_tokens=1_000,
        )
        assert fields["cost_basis"] == "unpriced"
        assert fields["cost_usd"] == 0.0

    def test_a_boolean_is_not_a_tick_count(self):
        """`True` is an int in Python, and it would silently become $0.0000001."""
        fields = cost_fields(
            model="claude-sonnet-5", provider="anthropic",
            input_tokens=1_000_000, output_tokens=0, cost_in_usd_ticks=True,
        )
        assert fields["cost_basis"] == "pricing_estimate"
