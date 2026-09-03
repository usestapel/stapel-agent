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
            # The models a live client fleet was actually calling while the
            # table had never heard of them: all three ladder rungs pointed
            # at gpt-5.2 through the openai-compat provider, so
            # every composer call — vision draft, category descent,
            # characteristic filling — logged cost_basis=unpriced.
            ("gpt-5.2", (1.75, 14.0)),
            ("gpt-5.2-pro", (21.0, 168.0)),
        ],
    )
    def test_verified_against_the_published_list(self, model, expected):
        prices = PRICES_USD_PER_MTOK[model]
        assert (prices["input"], prices["output"]) == expected

    def test_the_default_large_model_is_priced(self):
        """``conf.MODELS['large']`` shipping unpriced is the exact shape of
        the defect: the most expensive route, estimated at nothing."""
        from stapel_agent.conf import agent_settings

        for size in ("small", "medium", "large", "xlarge"):
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

    def test_a_hyphenated_snapshot_suffix_also_resolves(self):
        """OpenAI dates its snapshots ``-YYYY-MM-DD``, not ``-YYYYMMDD``.

        The published id for the model this fleet calls is
        ``gpt-5.2-2025-12-11``. A normalizer that only knows Anthropic's
        spelling prices the base alias and leaves every snapshot id
        unpriced — the same blindness, one string away.
        """
        assert estimate_cost("gpt-5.2-2025-12-11", 1_000_000, 0) == pytest.approx(1.75)
        assert is_priced("gpt-5.2-2025-12-11")

    def test_a_non_date_suffix_is_not_stripped(self):
        """Only a date is a snapshot. A normalizer greedy enough to eat any
        trailing segment would make one model's price answer for another's,
        which is a fabricated cost with extra steps."""
        assert not is_priced("gpt-5.2-turbo")
        assert not is_priced("claude-sonnet-5-cheap")

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


class TestTheDeploymentsOwnModelsAreChecked:
    """W018: the table being right is worth nothing if this deployment
    calls something that is not in it.

    ``test_the_default_large_model_is_priced`` above guards the SHIPPED
    ladder and passed all along, while a live client fleet pointed all
    three rungs at ``gpt-5.2`` through ``OPENAI_COMPAT_MODELS`` and logged
    every composer call — vision draft, category descent, characteristic
    filling — with ``cost_basis=unpriced``. Nine hundred rows, one warning
    line per row in a worker's log, and metering that could not cost the
    feature at all. The gate was green about the wrong models.

    So the check resolves the models THIS deployment will actually call,
    through the same ``resolve_model`` seam ``complete()`` uses, and says
    so at ``manage.py check`` time — before the first call, not once per
    call.
    """

    def _run(self):
        from stapel_agent.checks import check_configured_models_are_priced

        return check_configured_models_are_priced(app_configs=None)

    def _ids(self):
        return [issue.id for issue in self._run()]

    def test_the_shipped_ladder_is_clean(self, settings):
        settings.STAPEL_AGENT = {}
        assert self._ids() == []

    def test_an_unpriced_configured_model_warns(self, settings):
        settings.STAPEL_AGENT = {
            "MODELS": {"small": "no-such-model-9", "medium": "claude-sonnet-5"},
        }
        assert self._ids() == ["stapel_agent.W018"]

    def test_the_warning_names_the_model_and_the_rung(self, settings):
        """A warning that says "some model is unpriced" sends the reader
        looking; this one hands over the string to paste into the table."""
        settings.STAPEL_AGENT = {
            "MODELS": {"small": "no-such-model-9", "medium": "claude-sonnet-5"},
        }
        message = self._run()[0].msg
        assert "no-such-model-9" in message
        assert "small" in message
        assert "claude-sonnet-5" not in message

    def test_it_is_a_warning_not_an_error(self, settings):
        """A deployment may not care what its calls cost, and a provider
        that reports its own charge never lands on the table at all."""
        from django.core import checks as django_checks

        settings.STAPEL_AGENT = {"MODELS": {"small": "no-such-model-9"}}
        assert self._run()[0].level == django_checks.WARNING

    def test_the_hint_names_the_table_to_edit(self, settings):
        settings.STAPEL_AGENT = {"MODELS": {"small": "no-such-model-9"}}
        hint = self._run()[0].hint
        assert "PRICES_USD_PER_MTOK" in hint
        assert "cost_basis=unpriced" in hint

    def test_the_openai_compat_overlay_is_what_gets_checked(self, settings):
        """The exact live shape: the Anthropic ladder underneath is fully
        priced, and every rung is overridden to a model the table has never
        heard of. Reading MODELS alone reports all clear."""
        settings.STAPEL_AGENT = {
            "DEFAULT_PROVIDER": "openai-compat",
            "OPENAI_COMPAT_BASE_URL": "https://api.openai.com/v1",
            "MODELS": {
                "small": "claude-haiku-4-5-20251001",
                "medium": "claude-sonnet-5",
                "large": "claude-opus-5",
            },
            "OPENAI_COMPAT_MODELS": {
                "small": "no-such-model-9",
                "medium": "no-such-model-9",
                "large": "no-such-model-9",
            },
        }
        issues = self._run()
        assert [i.id for i in issues] == ["stapel_agent.W018"]
        assert "no-such-model-9" in issues[0].msg

    def test_the_overlay_that_this_fleet_now_ships_is_priced(self, settings):
        """gpt-5.2 on all three rungs — the client fleet's stand .env, the
        configuration that produced the unpriced rows."""
        settings.STAPEL_AGENT = {
            "DEFAULT_PROVIDER": "openai-compat",
            "OPENAI_COMPAT_BASE_URL": "https://api.openai.com/v1",
            "OPENAI_COMPAT_MODELS": {
                "small": "gpt-5.2",
                "medium": "gpt-5.2",
                "large": "gpt-5.2",
            },
        }
        assert self._ids() == []

    def test_an_unknown_default_provider_is_left_to_E001(self, settings):
        """Two findings for one typo is noise; E001 already names it, and
        this check cannot resolve models without a provider."""
        settings.STAPEL_AGENT = {"DEFAULT_PROVIDER": "nope"}
        assert self._ids() == []

    def test_a_provider_that_explodes_on_construction_does_not_break_check(
        self, settings
    ):
        """PROVIDERS is an open extension point: a host-registered class may
        raise anything at all from its constructor. A system check that goes
        down with it blocks the very deploy it was added to inform — and the
        broken provider is already W001/W002/W016's finding, not this one's."""

        class Exploding:
            def __init__(self):
                raise RuntimeError("no credentials, and I am rude about it")

        settings.STAPEL_AGENT = {
            "DEFAULT_PROVIDER": "boom",
            "PROVIDERS": {"boom": Exploding},
            "MODELS": {"small": "no-such-model-9"},
        }
        assert self._ids() == []

    def test_it_is_registered(self):
        from django.core.checks.registry import registry

        from stapel_agent.checks import check_configured_models_are_priced

        assert check_configured_models_are_priced in registry.registered_checks
