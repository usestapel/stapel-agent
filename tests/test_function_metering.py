"""Every ``llm.*`` reply carries what the call cost; every request may say
who it was for.

``metering`` is the sum of the ledger rows one Function call wrote, so the
caller's figure and the PromptLog cannot disagree; ``context`` is stored on
each of those rows as ``metadata.context``.
"""
from decimal import Decimal

import pytest
from stapel_core.comm import call, function_registry
from stapel_core.comm.exceptions import SchemaValidationError

from stapel_agent import provider_health
from stapel_agent.models import CostBasis, PromptLog, PromptStatus
from stapel_agent.providers.base import ProviderResult
from stapel_agent.stt.base import NormalizedTranscript
from stapel_agent.tests.fakes import (
    FakeSttProvider,
    OutOfCreditsProvider,
    SecondaryProvider,
)

CONTEXT = {"recording_id": "rec-1", "stage": "transcribe", "run_id": "run-9", "attempt": 2}
HASH = "sha256:" + "c" * 64
TEN_MINUTES_MS = 600_000

METERING_KEYS = {
    "provider", "model", "cost_usd", "cost_basis", "priced_by",
    "input_tokens", "output_tokens", "thinking_tokens", "cache_read_tokens",
    "cache_write_tokens", "audio_duration_ms", "duration_ms",
    "fallback_used", "calls",
}


@pytest.fixture
def priced_stt_chain(fake_stt, settings):
    """``fake-stt`` then ``fake-stt-2``, both on a real per-hour card."""
    from stapel_agent.stt.pricing import (
        _reset_runtime_stt_pricing_modules,
        register_stt_pricing_module,
    )

    settings.STAPEL_AGENT = {**settings.STAPEL_AGENT, "STT_FALLBACK_CHAIN": ["fake-stt-2"]}
    _reset_runtime_stt_pricing_modules()
    for name in ("fake-stt", "fake-stt-2"):
        register_stt_pricing_module(name, "stapel_agent.stt.pricing.elevenlabs")
    yield fake_stt
    _reset_runtime_stt_pricing_modules()


@pytest.fixture
def llm_chain(settings):
    settings.STAPEL_AGENT = {
        **getattr(settings, "STAPEL_AGENT", {}),
        "PROVIDERS": {
            "out-of-credits": "stapel_agent.tests.fakes.OutOfCreditsProvider",
            "secondary": "stapel_agent.tests.fakes.SecondaryProvider",
        },
        "DEFAULT_PROVIDER": "out-of-credits",
        "PROVIDER_FALLBACK_CHAIN": ["secondary"],
    }
    for cls in (OutOfCreditsProvider, SecondaryProvider):
        cls.reset()
    # llm.complete parses JSON out of the answer.
    SecondaryProvider.result = ProviderResult(
        text='{"answer": 1}', input_tokens=7, output_tokens=3
    )
    provider_health.clear_slot()
    yield settings
    SecondaryProvider.reset()
    provider_health.clear_slot()


class TestContextSchema:
    FUNCTIONS = (
        ("llm.complete", {"prompt": "p", "model": "small"}),
        ("llm.translate", {"from_lang": "en", "to": "de", "entries": {"a": "b"}}),
        ("llm.transcribe", {"audio_url": "https://example.com/a.wav"}),
        ("llm.diarize", {"audio_url": "https://example.com/a.wav"}),
        ("llm.embed", {"texts": ["a"]}),
        ("llm.rerank", {"query": "q", "documents": ["d"]}),
        ("llm.summarize", {"text": "t"}),
        ("llm.generate_image", {"prompt": "a cat"}),
    )

    @pytest.mark.parametrize("name,payload", FUNCTIONS)
    def test_context_is_accepted(self, name, payload):
        function_registry.validate(name, {**payload, "context": CONTEXT})

    @pytest.mark.parametrize("name,payload", FUNCTIONS)
    def test_context_is_a_closed_object(self, name, payload):
        with pytest.raises(SchemaValidationError):
            function_registry.validate(
                name, {**payload, "context": {**CONTEXT, "tenant": "x"}}
            )


@pytest.mark.django_db
class TestTranscribeMetering:
    def test_a_fallback_chain_is_summed_and_named_by_the_answering_provider(
        self, priced_stt_chain
    ):
        FakeSttProvider.result = NormalizedTranscript(
            provider="fake-stt", language="en", duration_seconds=None
        )

        reply = call(
            "llm.transcribe",
            {
                "audio_url": "https://example.com/a.wav",
                "audio_content_hash": HASH,
                "audio_duration_ms": TEN_MINUTES_MS,
                "context": CONTEXT,
            },
        )

        assert reply["status"] == "ok"
        rows = list(PromptLog.objects.order_by("created_at"))
        assert [r.status for r in rows] == [PromptStatus.ERROR, PromptStatus.SUCCESS]
        m = reply["metering"]
        assert set(m) == METERING_KEYS
        assert m["calls"] == 2
        assert m["provider"] == "fake-stt-2"
        assert m["fallback_used"] is True
        assert m["audio_duration_ms"] == 2 * TEN_MINUTES_MS
        assert m["cost_basis"] == CostBasis.PRICING_ESTIMATE
        assert m["priced_by"] == "fake-stt-2"
        assert Decimal(str(m["cost_usd"])) == sum(r.cost_usd for r in rows)
        assert rows[0].cost_usd > 0  # the empty answer was paid for too

    def test_context_lands_on_every_row(self, priced_stt_chain):
        FakeSttProvider.result = NormalizedTranscript(
            provider="fake-stt", language="en", duration_seconds=None
        )
        call(
            "llm.transcribe",
            {
                "audio_url": "https://example.com/a.wav",
                "audio_duration_ms": TEN_MINUTES_MS,
                "context": CONTEXT,
            },
        )
        rows = PromptLog.objects.all()
        assert rows.count() == 2
        assert all(r.metadata["context"] == CONTEXT for r in rows)

    def test_a_checkpoint_hit_reports_zero_cached(self, priced_stt_chain):
        payload = {
            "audio_url": "https://example.com/a.wav",
            "audio_content_hash": HASH,
            "audio_duration_ms": TEN_MINUTES_MS,
        }
        first = call("llm.transcribe", payload)
        second = call("llm.transcribe", payload)

        assert first["metering"]["cost_basis"] == CostBasis.PRICING_ESTIMATE
        assert second["metering"]["cost_usd"] == 0
        assert second["metering"]["cost_basis"] == CostBasis.CACHED
        assert second["metering"]["calls"] == 1

    def test_without_context_the_row_has_none(self, priced_stt_chain):
        call("llm.transcribe", {"audio_url": "https://example.com/a.wav"})
        assert "context" not in (PromptLog.objects.get().metadata or {})


@pytest.mark.django_db
class TestCompletionMetering:
    def test_a_provider_chain_sums_both_calls(self, llm_chain):
        reply = call(
            "llm.complete",
            {"prompt": "p", "model": "small", "context": {"stage": "merge"}},
        )

        assert reply["status"] == "ok"
        m = reply["metering"]
        assert m["calls"] == 2
        assert m["provider"] == "secondary"
        assert m["fallback_used"] is True
        assert (m["input_tokens"], m["output_tokens"]) == (7, 3)
        assert all(
            r.metadata["context"] == {"stage": "merge"} for r in PromptLog.objects.all()
        )

    def test_a_checkpoint_hit_reports_zero_cached(self, fake_provider):
        payload = {"prompt": "p", "model": "small", "idempotency_key": "job-1"}
        call("llm.complete", payload)
        second = call("llm.complete", payload)

        assert second["metering"]["cost_usd"] == 0
        assert second["metering"]["cost_basis"] == CostBasis.CACHED

    def test_a_prompt_cache_hit_reports_zero_cached(self, fake_provider, settings):
        settings.STAPEL_AGENT = {
            **settings.STAPEL_AGENT,
            "CACHE_LOOKUP": {"translate": True},
        }
        fake_provider.result = type(fake_provider.result)(text='{"a": "B"}')
        payload = {"from_lang": "en", "to": "de", "entries": {"a": "b"}, "user_id": "u-1"}
        call("llm.translate", payload)
        second = call("llm.translate", payload)

        assert len(fake_provider.calls) == 1
        assert second["metering"]["cost_usd"] == 0
        assert second["metering"]["cost_basis"] == CostBasis.CACHED
        assert second["metering"]["calls"] == 1

    def test_a_call_refused_before_any_provider_carries_no_metering(self, fake_provider):
        reply = call("llm.complete", {"prompt": "p", "model": "small", "provider": "nope"})
        assert reply["status"] == "failure"
        assert "metering" not in reply


@pytest.mark.django_db
class TestSummarizeMetering:
    def test_a_map_reduce_sums_every_part(self, fake_provider, monkeypatch):
        from stapel_agent import summary

        monkeypatch.setattr(summary, "DEFAULT_CHUNK_TOKENS", 50)
        text = "\n\n".join(f"Paragraph {i}. " + "word " * 120 for i in range(4))

        reply = call("llm.summarize", {"text": text, "context": {"stage": "summarize"}})

        assert reply["status"] == "ok"
        rows = PromptLog.objects.count()
        assert rows > 2
        m = reply["metering"]
        assert m["calls"] == rows
        assert m["input_tokens"] == 10 * rows
        assert m["output_tokens"] == 5 * rows

    def test_the_task_path_carries_metering(self, fake_provider):
        from stapel_core.comm.tasks import _handlers

        reply = _handlers["llm.summarize"]({"text": "short text"})
        assert reply["metering"]["calls"] == 1
