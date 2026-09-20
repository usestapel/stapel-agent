"""A retried summary must not be a second purchase.

The transcribe side learned this in 0.24.0 (one 148-minute recording
transcribed six times because the step that failed was downstream of the
money). The summarize side had the same shape and no checkpoint at all:
a long meeting is summarised map-reduce — N chunk completions plus a
merge — and the task that carries it declares three attempts. A failure
on the last part re-bought every part before it.

Counted with a fake provider; nothing here calls anyone.
"""
import pytest

from stapel_agent import services
from stapel_agent.tests.fakes import FakeProvider

LONG_ENOUGH_TO_SPLIT = " ".join(["word"] * 40_000)


@pytest.fixture
def fake(settings):
    settings.STAPEL_AGENT = {
        **getattr(settings, "STAPEL_AGENT", {}),
        "PROVIDERS": {"fake": "stapel_agent.tests.fakes.FakeProvider"},
        "DEFAULT_PROVIDER": "fake",
        "PROVIDER_FALLBACK_CHAIN": [],
        "CHECKPOINT_ENABLED": True,
        "CHECKPOINT_SURFACES": ["complete"],
    }
    FakeProvider.reset()
    yield settings
    FakeProvider.reset()


@pytest.mark.django_db
class TestSummarizeCheckpoint:
    def test_a_second_attempt_at_the_same_transcript_buys_nothing(self, fake):
        first = services.summarize("a short meeting", idempotency_key="sha256:abc")
        bought = len(FakeProvider.calls)
        assert first["status"] == "ok"
        assert bought == 1

        second = services.summarize("a short meeting", idempotency_key="sha256:abc")

        assert second["status"] == "ok"
        assert second["summary"] == first["summary"]
        assert len(FakeProvider.calls) == bought, "the retry paid again"

    def test_a_map_reduce_resumes_at_the_part_that_failed(self, fake):
        """The expensive half: N parts, a failure, and only the rest paid."""
        services.summarize(LONG_ENOUGH_TO_SPLIT, idempotency_key="sha256:long")
        parts = len(FakeProvider.calls)
        assert parts > 2, "the fixture must actually split"

        services.summarize(LONG_ENOUGH_TO_SPLIT, idempotency_key="sha256:long")

        assert len(FakeProvider.calls) == parts

    def test_each_part_keeps_its_own_answer(self, fake):
        """One key for all parts would serve part one's summary as part two's."""
        seen = []

        def responder(*, prompt, **kwargs):
            from stapel_agent.providers.base import ProviderResult

            seen.append(prompt[:24])
            return ProviderResult(text=f"summary of {prompt[:16]}", output_tokens=3)

        FakeProvider.responder = responder
        services.summarize(LONG_ENOUGH_TO_SPLIT, idempotency_key="sha256:parts")

        assert len(set(seen)) == len(seen), "two parts were asked the same question"

    def test_without_a_key_nothing_is_checkpointed(self, fake):
        """Unchanged for every caller that passes none."""
        services.summarize("a short meeting")
        services.summarize("a short meeting")

        assert len(FakeProvider.calls) == 2

    def test_a_different_transcript_is_a_different_purchase(self, fake):
        services.summarize("meeting one", idempotency_key="sha256:one")
        services.summarize("meeting two", idempotency_key="sha256:two")

        assert len(FakeProvider.calls) == 2
