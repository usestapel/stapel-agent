"""The ledger as a meter: who the call was for, and what it cost.

Three defects closed together in 0.12.0, and they only make sense together
— each on its own leaves the table unable to answer the one question a
credits system asks, "what did this customer cost us this month":

- the comm schemas are ``additionalProperties: false`` and carried no
  identity, so every row written by product traffic had ``user_id = NULL``;
- ``cost_usd``/``cost_basis`` were computed on every completion and thrown
  away;
- a transcribe row recorded the host of an audio URL and a wall-clock
  duration, while STT bills per hour of AUDIO.

The tests below are written against the observable outcome — what is in the
row afterwards — rather than against the plumbing that puts it there.
"""
from decimal import Decimal

import pytest
from stapel_core.comm import call
from stapel_core.comm.exceptions import SchemaValidationError

from stapel_agent import services
from stapel_agent.models import CostBasis, PromptLog, PromptSource, PromptStatus
from stapel_agent.providers.base import ProviderResult
from stapel_agent.stt.base import AudioRef, NormalizedTranscript


@pytest.fixture
def clean_stt_pricing():
    """Runtime rate-card registrations, undone after the test."""
    from stapel_agent.stt.pricing import _reset_runtime_stt_pricing_modules

    _reset_runtime_stt_pricing_modules()
    yield
    _reset_runtime_stt_pricing_modules()


# ── Gap 9: identity through the bus ───────────────────────────────────


class TestSchemasAcceptIdentity:
    """Optional means BOTH directions stay valid — that is the promise.

    A required field would have been a breaking change for every existing
    host; a rejected field is what these schemas did before, and it is why
    the meter had no subject.
    """

    IDENTITY_FUNCTIONS = (
        ("llm.complete", {"prompt": "p", "model": "small"}),
        ("llm.translate", {"from_lang": "en", "to": "de", "entries": {"a": "b"}}),
        ("llm.transcribe", {"audio_url": "https://example.com/a.wav"}),
        ("llm.diarize", {"audio_url": "https://example.com/a.wav"}),
        ("llm.embed", {"texts": ["a"]}),
        ("llm.rerank", {"query": "q", "documents": ["d"]}),
        ("llm.summarize", {"text": "t"}),
        ("llm.generate_image", {"prompt": "a cat"}),
    )

    def _validate(self, name, payload):
        from stapel_core.comm import function_registry

        function_registry.validate(name, payload)

    @pytest.mark.parametrize("name,payload", IDENTITY_FUNCTIONS)
    def test_identity_is_accepted(self, name, payload):
        self._validate(name, {**payload, "user_id": "u-1", "workspace_id": "w-1"})

    @pytest.mark.parametrize("name,payload", IDENTITY_FUNCTIONS)
    def test_absent_identity_is_still_valid(self, name, payload):
        """No existing caller breaks. This is the compatibility half."""
        self._validate(name, payload)

    @pytest.mark.parametrize("name,payload", IDENTITY_FUNCTIONS)
    def test_only_these_two_were_opened(self, name, payload):
        """``additionalProperties: false`` still holds for everything else."""
        with pytest.raises(SchemaValidationError):
            self._validate(name, {**payload, "tenant_id": "sneaky"})

    def test_the_read_only_catalog_stays_closed(self):
        """``llm.stt_catalog`` writes no row, so it has nobody to attribute."""
        with pytest.raises(SchemaValidationError):
            self._validate("llm.stt_catalog", {"user_id": "u-1"})


@pytest.mark.django_db
class TestIdentityReachesTheRow:
    """The point of the schema change: the id has to land in the column."""

    def test_complete(self, fake_provider):
        call(
            "llm.complete",
            {"prompt": "p", "model": "small", "user_id": "u-1", "workspace_id": "w-1"},
        )
        row = PromptLog.objects.get()
        assert (row.user_id, row.workspace_id) == ("u-1", "w-1")

    def test_translate(self, fake_provider):
        fake_provider.result = ProviderResult(text='{"a": "b"}')
        call(
            "llm.translate",
            {
                "from_lang": "en",
                "to": "de",
                "entries": {"a": "hello"},
                "user_id": "u-2",
                "workspace_id": "w-2",
            },
        )
        row = PromptLog.objects.get(source=PromptSource.TRANSLATE)
        assert (row.user_id, row.workspace_id) == ("u-2", "w-2")

    def test_summarize(self, fake_provider):
        call(
            "llm.summarize",
            {"text": "a long enough text", "user_id": "u-3", "workspace_id": "w-3"},
        )
        row = PromptLog.objects.get(source=PromptSource.SUMMARIZE)
        assert (row.user_id, row.workspace_id) == ("u-3", "w-3")

    def test_transcribe(self, fake_stt):
        call(
            "llm.transcribe",
            {
                "audio_url": "https://example.com/a.wav",
                "user_id": "u-4",
                "workspace_id": "w-4",
            },
        )
        row = PromptLog.objects.get(source=PromptSource.TRANSCRIBE)
        assert (row.user_id, row.workspace_id) == ("u-4", "w-4")

    def test_diarize(self, fake_diarization):
        call(
            "llm.diarize",
            {
                "audio_url": "https://example.com/a.wav",
                "user_id": "u-5",
                "workspace_id": "w-5",
            },
        )
        row = PromptLog.objects.get(source=PromptSource.DIARIZE)
        assert (row.user_id, row.workspace_id) == ("u-5", "w-5")

    def test_embed(self, fake_embeddings):
        call("llm.embed", {"texts": ["a"], "user_id": "u-6", "workspace_id": "w-6"})
        row = PromptLog.objects.get(source=PromptSource.EMBED)
        assert (row.user_id, row.workspace_id) == ("u-6", "w-6")

    def test_rerank(self, fake_rerank):
        call(
            "llm.rerank",
            {
                "query": "q",
                "documents": ["d"],
                "user_id": "u-7",
                "workspace_id": "w-7",
            },
        )
        row = PromptLog.objects.get(source=PromptSource.RERANK)
        assert (row.user_id, row.workspace_id) == ("u-7", "w-7")

    def test_generate_image(self, fake_images):
        call(
            "llm.generate_image",
            {"prompt": "a cat", "user_id": "u-8", "workspace_id": "w-8"},
        )
        row = PromptLog.objects.get(source=PromptSource.GENERATE_IMAGE)
        assert (row.user_id, row.workspace_id) == ("u-8", "w-8")

    def test_an_unattributed_call_is_served_not_refused(self, fake_provider):
        """Metering is not authorisation. No id, no attribution, still an answer."""
        result = call("llm.complete", {"prompt": "p", "model": "small"})
        assert result["status"] == "ok"
        row = PromptLog.objects.get()
        assert row.user_id is None and row.workspace_id is None

    def test_non_string_ids_are_coerced(self, fake_provider):
        """Hosts number their users; the column is text and takes any shape."""
        services.complete("p", "small", user_id=42, workspace_id=7)
        row = PromptLog.objects.get()
        assert (row.user_id, row.workspace_id) == ("42", "7")


# ── Gap 10: the cost stops being thrown away ──────────────────────────


@pytest.mark.django_db
class TestCostIsPersisted:
    def test_the_row_carries_what_the_response_carries(self, fake_provider):
        """One computation, two readers — a dashboard and an invoice cannot
        disagree about a single call."""
        result = services.complete("p", "medium", provider="fake")
        row = PromptLog.objects.get()
        assert row.cost_basis == CostBasis.PRICING_ESTIMATE
        assert float(row.cost_usd) == pytest.approx(result["usage"]["cost_usd"])

    def test_the_number_is_the_rate_card_applied_to_the_tokens(self, fake_provider):
        # medium -> claude-sonnet-5 ($2/$10 per MTok); the fake reports
        # 10 in / 5 out, and "fake" is not an xAI-style provider so the
        # 2 reasoning tokens are already inside the completion count.
        services.complete("p", "medium", provider="fake")
        row = PromptLog.objects.get()
        assert row.cost_usd == Decimal("0.00007000")

    def test_cost_is_a_decimal_so_a_period_sums_exactly(self, fake_provider):
        from django.db.models import Sum

        for _ in range(3):
            services.complete("p", "medium", provider="fake", skip_cache=True)
        total = PromptLog.objects.aggregate(t=Sum("cost_usd"))["t"]
        assert total == Decimal("0.00021000")

    def test_an_unpriced_model_is_stored_as_unknown_not_as_free(
        self, fake_provider, settings, caplog
    ):
        """0.0 means "free" and "we have no idea" — the column has to tell
        them apart, because only one of them is good news."""
        settings.STAPEL_AGENT = {
            **settings.STAPEL_AGENT,
            "MODELS": {"small": "no-such-model-9", "medium": "m", "large": "l"},
        }
        services.complete("p", "small", provider="fake")
        row = PromptLog.objects.get()
        assert row.cost_basis == CostBasis.UNPRICED
        assert "no rate card" in caplog.text

    def test_a_failed_call_records_no_cost(self, fake_provider):
        """Providers do not bill for a call that returned nothing."""
        from stapel_agent.providers.base import ProviderError

        fake_provider.error = ProviderError("boom")
        services.complete("p", "medium", provider="fake")
        row = PromptLog.objects.get()
        assert row.status == PromptStatus.ERROR
        assert row.cost_usd is None and row.cost_basis is None

    def test_the_stored_cost_is_never_recomputed(self, fake_provider):
        """pricing.py's discipline, carried into the schema: a row keeps the
        price that was in force when the call was made. Re-reading it after
        the rate card moves must not restate history."""
        import stapel_agent.pricing as pricing

        services.complete("p", "medium", provider="fake")
        stored = PromptLog.objects.get().cost_usd

        original = dict(pricing.PRICES_USD_PER_MTOK["claude-sonnet-5"])
        pricing.PRICES_USD_PER_MTOK["claude-sonnet-5"] = {
            "input": 99.0, "output": 99.0,
        }
        try:
            assert PromptLog.objects.get().cost_usd == stored
        finally:
            pricing.PRICES_USD_PER_MTOK["claude-sonnet-5"] = original


# ── Gap 11: an auditable transcribe row ───────────────────────────────


@pytest.mark.django_db
class TestTranscribeIsAuditable:
    def test_the_billable_quantity_is_the_audio_not_the_wait(self, fake_stt):
        """``duration_ms`` is how long WE waited; the invoice is computed
        from how long the AUDIO was, and only the second is billable."""
        services.transcribe(AudioRef(url="https://example.com/a.wav"))
        row = PromptLog.objects.get()
        assert row.audio_duration_ms == 2000  # the fake reports 2.0 seconds
        assert row.duration_ms != row.audio_duration_ms

    def test_a_host_registered_card_prices_the_row(self, fake_stt, clean_stt_pricing):
        """The adapter with no catalog entry — a host's own registration.
        Refusing to price it would leave a live paid route reading as
        unknown for no better reason than where its class lives."""
        from stapel_agent.stt.pricing import register_stt_pricing_module

        register_stt_pricing_module(
            "fake-stt", "stapel_agent.stt.pricing.elevenlabs"
        )
        services.transcribe(AudioRef(url="https://example.com/a.wav"))
        row = PromptLog.objects.get()
        # 2 s of the $0.22/hour card, at the card's own 6-place rounding.
        assert row.cost_basis == CostBasis.PRICING_ESTIMATE
        assert row.cost_usd == Decimal("0.00012200")
        assert row.metadata["priced_by"] == "fake-stt"

    def test_a_catalogued_provider_is_priced_through_its_model_config(
        self, settings, fake_stt
    ):
        """The config, not the bare provider, is what knows the price
        VARIANTS — and the row says which config priced it, so the number
        is falsifiable a quarter later."""
        settings.STAPEL_AGENT = {
            **settings.STAPEL_AGENT,
            "STT_PROVIDERS": {"elevenlabs": "stapel_agent.tests.fakes.FakeSttProvider"},
            "DEFAULT_STT_PROVIDER": "elevenlabs",
        }
        services.transcribe(AudioRef(url="https://example.com/a.wav"))
        row = PromptLog.objects.get()
        assert row.metadata["priced_by"] == "elevenlabs_scribe_v2_default"
        assert row.cost_usd == Decimal("0.00012200")

    def test_an_unpriced_provider_is_loud(self, fake_stt, clean_stt_pricing, caplog):
        """The silent zero is the whole defect. A live billable call with no
        rate card has to announce itself."""
        services.transcribe(AudioRef(url="https://example.com/a.wav"))
        row = PromptLog.objects.get()
        assert row.cost_basis == CostBasis.UNPRICED
        assert row.cost_usd is None
        assert "no rate card for STT provider" in caplog.text
        assert "fake-stt" in caplog.text

    def test_a_provider_that_reports_no_duration_says_so(self, fake_stt, caplog):
        """Then the row is NOT reconstructable, and that is worth a line in
        the log rather than a quiet null."""
        fake_stt.result = NormalizedTranscript(
            provider="fake-stt", language="en", duration_seconds=None
        )
        services.transcribe(AudioRef(url="https://example.com/a.wav"))
        row = PromptLog.objects.get()
        assert row.audio_duration_ms is None
        assert row.cost_basis == CostBasis.UNPRICED
        assert "reported no audio duration" in caplog.text

    def test_a_failed_transcription_has_no_audio_and_no_cost(self, fake_stt):
        from stapel_agent.stt.base import TranscriptionError

        fake_stt.error = TranscriptionError("bad audio", provider="fake-stt")
        services.transcribe(AudioRef(url="https://example.com/a.wav"))
        row = PromptLog.objects.get()
        assert row.status == PromptStatus.ERROR
        assert row.audio_duration_ms is None and row.cost_usd is None

    def test_diarization_records_its_billable_audio_too(self, fake_diarization):
        """pyannoteAI bills per audio hour as well. The number was already
        measured and already in metadata — it belongs in the column the
        meter queries."""
        services.diarize(AudioRef(url="https://example.com/a.wav"))
        row = PromptLog.objects.get()
        assert row.audio_duration_ms == 4000  # the fake reports 4.0 seconds


# ── The card for the route that had none ──────────────────────────────


class TestXiaomiMimoRateCard:
    """A live, paid, key-configured ``zh`` route was priced at nothing.

    Not because the price was secret — it is on Xiaomi's own pay-as-you-go
    page — but because the adapter lives in a host and nothing here knew
    the number. The card ships; the host registers it beside its adapter.
    """

    def test_the_published_overseas_rate(self):
        from stapel_agent.stt.pricing import xiaomi_mimo

        assert xiaomi_mimo.MIMO_V25_ASR_PRICE_PER_HOUR == 0.074
        assert xiaomi_mimo.estimate_cost(3_600_000) == pytest.approx(0.074)

    def test_the_domestic_rate_is_carried_but_never_converted(self):
        """¥0.5/h is on the same page. Which list an account bills against
        is a property of the account, and a pinned FX rate would produce a
        plausible number for the wrong customers."""
        from stapel_agent.stt.pricing import xiaomi_mimo

        assert xiaomi_mimo.MIMO_V25_ASR_PRICE_PER_HOUR_CNY == 0.5
        assert xiaomi_mimo.estimate_cost(3_600_000) != 0.5

    def test_another_model_is_unpriced_not_free(self):
        from stapel_agent.stt.pricing import xiaomi_mimo

        assert xiaomi_mimo.estimate_cost(3_600_000, model="mimo-v9-asr") is None

    def test_it_is_not_a_builtin_because_the_adapter_is_not(self):
        """The builtin map's invariant — its keys are providers THIS package
        registers — is why the card is registerable rather than wired."""
        from stapel_agent.stt import BUILTIN_STT_PROVIDERS
        from stapel_agent.stt.pricing import BUILTIN_STT_PRICING_MODULES

        assert "xiaomi_mimo" not in BUILTIN_STT_PRICING_MODULES
        assert set(BUILTIN_STT_PRICING_MODULES) <= set(BUILTIN_STT_PROVIDERS)

    def test_a_host_can_wire_it_to_its_own_adapter_name(self, clean_stt_pricing):
        from stapel_agent.stt.pricing import (
            pricing_module,
            register_stt_pricing_module,
        )

        register_stt_pricing_module(
            "xiaomi_mimo", "stapel_agent.stt.pricing.xiaomi_mimo"
        )
        assert pricing_module("xiaomi_mimo").estimate_cost(
            3_600_000
        ) == pytest.approx(0.074)


# ── The erasure path still works on a wider row ───────────────────────


@pytest.mark.django_db
class TestErasureKeepsTheAccounting:
    def test_the_subject_goes_and_the_numbers_stay(self, fake_provider):
        """Deleting the rows outright would destroy the ledger finance
        reads; the tenant is not personal data and stays with them."""
        from stapel_agent.gdpr import AgentGDPRProvider

        services.complete("p", "medium", provider="fake", user_id=11, workspace_id=99)
        AgentGDPRProvider().delete(11)
        row = PromptLog.objects.get()
        assert row.user_id is None
        assert row.workspace_id == "99"
        assert row.cost_usd is not None

    def test_the_export_survives_a_decimal(self, fake_provider):
        """``json.dumps`` refuses a Decimal outright — a column added for
        accounting must not be the reason a subject-access export raises."""
        import json

        from stapel_agent.gdpr import AgentGDPRProvider

        services.complete("p", "medium", provider="fake", user_id=11)
        exported = AgentGDPRProvider().export(11)
        json.dumps(exported)  # must not raise
        assert exported["prompts"][0]["cost_usd"] > 0
