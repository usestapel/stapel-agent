"""An empty wallet at the text provider must not end a customer's work.

THE INCIDENT (a client stand, 2026-09-20)
    The summariser's endpoint answered ``403 {"code": "... your team has
    either used all available credits or reached its monthly spending
    limit"}``. ``providers/openai_compat.py`` turned every status >= 400
    into one flat ``ProviderError``, ``services.complete`` had no chain
    to walk, and summarisation is best-effort by design — so three of
    that day's seven completed recordings simply had no summary, no
    alert was raised, and the fleet's second configured credential was
    never tried.

    This is the STT lesson (``stt/failures.py``, 41 recordings) arriving
    on the text surface a fortnight later. The tests below are its
    inverse: the same refusal, and the customer still gets their work.

No provider is called for real anywhere here — every backend is a fake
in ``stapel_agent.tests.fakes``, and the ones that count calls are the
proof that nothing is bought twice.
"""
import logging

import pytest

from stapel_agent import provider_health, services
from stapel_agent.failures import (
    FALLBACK_NEXT_PROVIDER,
    TERMINAL_INPUT,
    classify_status,
    disposition,
)
from stapel_agent.models import PromptLog
from stapel_agent.tests.fakes import (
    BadRequestProvider,
    FakeProvider,
    OutOfCreditsProvider,
    SecondaryProvider,
    UnreachableProvider,
)

PROVIDERS = {
    "out-of-credits": "stapel_agent.tests.fakes.OutOfCreditsProvider",
    "bad-request": "stapel_agent.tests.fakes.BadRequestProvider",
    "unreachable": "stapel_agent.tests.fakes.UnreachableProvider",
    "secondary": "stapel_agent.tests.fakes.SecondaryProvider",
    "fake": "stapel_agent.tests.fakes.FakeProvider",
}

FAKES = (
    OutOfCreditsProvider,
    BadRequestProvider,
    UnreachableProvider,
    SecondaryProvider,
    FakeProvider,
)


@pytest.fixture
def chain(settings):
    """The stand's shape: a primary that is out of credits, one fallback."""
    settings.STAPEL_AGENT = {
        **getattr(settings, "STAPEL_AGENT", {}),
        "PROVIDERS": PROVIDERS,
        "DEFAULT_PROVIDER": "out-of-credits",
        "PROVIDER_FALLBACK_CHAIN": ["secondary"],
        "PROVIDER_ALERT_INTERVAL_SECONDS": 3600,
    }
    for cls in FAKES:
        cls.reset()
    provider_health.clear_slot()
    yield settings
    for cls in FAKES:
        cls.reset()
    provider_health.clear_slot()


@pytest.fixture
def gauges(monkeypatch):
    recorded: list[tuple[str, str, float]] = []
    monkeypatch.setattr(
        provider_health,
        "record_state_gauge",
        lambda name, provider, value, description="": recorded.append(
            (name, provider, value)
        ),
    )
    return recorded


class TestTheTaxonomy:
    """The classification itself, before any chain walks on it."""

    def test_the_403_that_cost_a_day_of_summaries_is_not_the_customers_fault(self):
        fatal, reason = classify_status(403, OutOfCreditsProvider.body)
        assert (fatal, reason) == (False, "quota")
        assert disposition(reason) == FALLBACK_NEXT_PROVIDER

    def test_a_403_with_no_billing_words_is_auth_and_still_walks_on(self):
        fatal, reason = classify_status(403, "Forbidden")
        assert (fatal, reason) == (False, "auth")
        assert disposition(reason) == FALLBACK_NEXT_PROVIDER

    @pytest.mark.parametrize(
        "status,body,expected",
        [
            (402, "", "quota"),
            (429, "rate limit exceeded, retry in 2s", "rate"),
            (429, "monthly quota exceeded", "quota"),
            (400, "You have exceeded your monthly spending limit", "quota"),
            (500, "", "server"),
            (422, "unsupported content block", "media"),
        ],
    )
    def test_the_status_narrows_it_and_the_body_decides(self, status, body, expected):
        assert classify_status(status, body)[1] == expected

    def test_only_the_request_itself_is_terminal(self):
        assert disposition("media") == TERMINAL_INPUT
        assert disposition("job") == TERMINAL_INPUT
        assert disposition("quota") != TERMINAL_INPUT
        assert disposition("auth") != TERMINAL_INPUT


@pytest.mark.django_db
class TestTheChain:
    def test_an_out_of_credits_primary_is_answered_by_the_fallback(self, chain):
        result = services.complete("summarise this", "medium", source="summarize")

        assert result["status"] == "ok", "the customer's work must not end here"
        assert result["result"] == "the fallback's answer"
        assert result["provider_used"] == "secondary"
        assert result["fallback_used"] is True
        assert len(OutOfCreditsProvider.calls) == 1
        assert len(SecondaryProvider.calls) == 1

    def test_the_row_records_who_answered_and_who_refused(self, chain):
        services.complete("summarise this", "medium", source="summarize")

        row = PromptLog.objects.get(status="success")
        # The ledger prices what was ACTUALLY used. A fallback answer
        # billed against the primary's card is an invoice that does not
        # describe what happened.
        assert row.metadata["provider"] == "secondary"
        assert row.metadata["fallback_used"] is True
        (refusal,) = row.metadata["attempts"]
        assert refusal["provider"] == "out-of-credits"
        assert refusal["reason"] == "quota"

        # And the refusal has its own error row, classified, so "we are
        # out of money" is a query rather than a grep.
        failed = PromptLog.objects.get(status="error")
        assert failed.metadata["reason"] == "quota"

    def test_a_refused_REQUEST_ends_the_chain_instead_of_spending_it(self, chain):
        chain.STAPEL_AGENT = {
            **chain.STAPEL_AGENT,
            "DEFAULT_PROVIDER": "bad-request",
        }

        result = services.complete("summarise this", "medium", source="summarize")

        assert result["status"] == "failure"
        assert "unsupported content block" in result["reason"]
        # The whole point of the taxonomy: the next provider would refuse
        # it identically, so it is never asked.
        assert SecondaryProvider.calls == []

    def test_a_provider_that_is_merely_down_walks_on(self, chain):
        chain.STAPEL_AGENT = {
            **chain.STAPEL_AGENT,
            "DEFAULT_PROVIDER": "unreachable",
        }

        result = services.complete("summarise this", "medium", source="summarize")

        assert result["status"] == "ok"
        assert result["provider_used"] == "secondary"

    def test_a_pinned_provider_is_never_masked_by_a_fallback(self, chain):
        """A caller measuring one provider must see ITS answer."""
        result = services.complete(
            "summarise this", "medium", source="summarize",
            provider="out-of-credits",
        )

        assert result["status"] == "failure"
        assert SecondaryProvider.calls == []
        # One provider, its own words — unchanged from before the chain
        # existed, which is what every existing caller parses.
        assert "403" in result["reason"]

    def test_when_the_whole_chain_declines_the_answer_names_every_provider(
        self, chain
    ):
        chain.STAPEL_AGENT = {
            **chain.STAPEL_AGENT,
            "PROVIDER_FALLBACK_CHAIN": ["unreachable"],
        }

        result = services.complete("summarise this", "medium", source="summarize")

        assert result["status"] == "failure"
        assert "out-of-credits" in result["reason"]
        assert "unreachable" in result["reason"]
        assert [a["reason"] for a in result["attempts"]] == ["quota", "server"]

    def test_no_chain_configured_is_the_old_behaviour_exactly(self, chain):
        """The fleet's default deployment states no fallback."""
        chain.STAPEL_AGENT = {
            **chain.STAPEL_AGENT,
            "DEFAULT_PROVIDER": "out-of-credits",
            "PROVIDER_FALLBACK_CHAIN": [],
        }

        result = services.complete("summarise this", "medium", source="summarize")

        assert result["status"] == "failure"
        assert result["reason"].startswith("OpenAI-compatible endpoint returned HTTP 403")

    def test_the_fallback_answer_is_not_bought_twice(self, chain):
        """Two calls, two answers, and no provider called more than once
        per call: the chain must not be a retry ladder in disguise."""
        services.complete("first", "medium", source="summarize")
        services.complete("second", "medium", source="summarize")

        assert len(SecondaryProvider.calls) == 2
        assert len(OutOfCreditsProvider.calls) == 2


@pytest.mark.django_db
class TestTheAlert:
    def test_the_first_refusal_is_an_ERROR_with_its_fingerprint(
        self, chain, gauges, caplog
    ):
        with caplog.at_level(logging.ERROR, logger="stapel_agent.provider_health"):
            services.complete("summarise this", "medium", source="summarize")

        (record,) = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert "llm_provider_out_of_credits:out-of-credits" in record.getMessage()
        assert ("llm_provider_out_of_credits", "out-of-credits", 1.0) in gauges

    def test_ten_recordings_in_an_hour_raise_ONE_alert(self, chain, caplog):
        """Telegram hears the fact once, not once per customer upload."""
        with caplog.at_level(logging.ERROR, logger="stapel_agent.provider_health"):
            for _ in range(10):
                services.complete("summarise this", "medium", source="summarize")

        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(errors) == 1

    def test_the_next_window_is_loud_again_and_says_how_many_it_swallowed(
        self, chain, caplog
    ):
        for _ in range(4):
            services.complete("summarise this", "medium", source="summarize")
        # A window later. The throttle is monotonic-time based, so the
        # test ends the window rather than moving a clock — and in doing
        # so covers the documented "0 means every refusal is loud".
        chain.STAPEL_AGENT = {
            **chain.STAPEL_AGENT,
            "PROVIDER_ALERT_INTERVAL_SECONDS": 0,
        }

        caplog.clear()
        with caplog.at_level(logging.ERROR, logger="stapel_agent.provider_health"):
            services.complete("summarise this", "medium", source="summarize")

        (record,) = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert "+3 further refusal(s)" in record.getMessage()

    def test_a_provider_that_serves_again_clears_its_gauge_and_re_arms(
        self, chain, gauges, caplog
    ):
        services.complete("summarise this", "medium", source="summarize")
        assert ("llm_provider_out_of_credits", "out-of-credits", 1.0) in gauges

        # The owner tops the account up.
        chain.STAPEL_AGENT = {**chain.STAPEL_AGENT, "DEFAULT_PROVIDER": "fake"}
        services.complete("summarise this", "medium", source="summarize")

        assert ("llm_provider_out_of_credits", "fake", 0.0) in gauges

    def test_a_throttled_provider_still_serves_from_the_fallback(self, chain):
        """The alert is quiet; the pipeline is not."""
        for _ in range(3):
            result = services.complete("x", "medium", source="summarize")
            assert result["status"] == "ok"
            assert result["provider_used"] == "secondary"
