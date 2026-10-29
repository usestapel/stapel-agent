"""An empty transcript over real audio is not an answer.

The incident (a client host, 2026-09-21): a 10-minute meeting came back
from the primary STT provider with zero words and zero utterances. The
router answered ``status: ok``, wrote the nothing into the checkpoint, and
served it from there — "no provider call, no charge" — to both reprocesses
that followed. The caller persisted zero segments and completed the
recording. Every gate was green; the customer had an empty meeting.

So an empty transcript for submitted audio at least
``STT_QA["EMPTY_TRANSCRIPT_MIN_MS"]`` long is a provider that did not
deliver: metered (it charged), not checkpointed (a cached nothing is
worth nothing), and the chain walks on.

Ordinary ``django_db`` tests: nothing here depends on commit ordering —
the checkpoint table and the ledger are read back inside the same
transaction the calls wrote them in, and the assertions are about which
rows exist and what the router answered.
"""
import pytest

from stapel_agent import services
from stapel_agent.models import ProviderCheckpoint, PromptLog, PromptStatus
from stapel_agent.stt.base import AudioRef, NormalizedTranscript
from stapel_agent.tests.fakes import FakeSttProvider, SecondSttProvider

AUDIO = AudioRef(url="https://minio.test/bucket/rec.mp3?X-Sig=s3cr3t")
HASH = "sha256:" + "b" * 64
TEN_MINUTES_MS = 600_018  # the recording from the incident, to the millisecond


def _empty(provider: str) -> NormalizedTranscript:
    return NormalizedTranscript(provider=provider, language="en", duration_seconds=None)


@pytest.fixture
def chain(fake_stt, settings):
    """Primary ``fake-stt``, fallback ``fake-stt-2``."""
    settings.STAPEL_AGENT = {
        **settings.STAPEL_AGENT,
        "DEFAULT_STT_PROVIDER": "fake-stt",
        "STT_FALLBACK_CHAIN": ["fake-stt-2"],
    }
    return fake_stt


@pytest.mark.django_db
class TestAnEmptyTranscriptIsAProviderFailure:
    def test_the_chain_walks_on_and_the_next_provider_answers(self, chain):
        FakeSttProvider.result = _empty("fake-stt")

        result = services.transcribe(
            AUDIO, audio_content_hash=HASH, audio_duration_ms=TEN_MINUTES_MS
        )

        assert result["status"] == "ok"
        assert result["provider_used"] == "fake-stt-2"
        assert result["fallback_used"] is True
        assert result["transcript"]["utterances"]
        assert len(FakeSttProvider.calls) == 1
        assert len(SecondSttProvider.calls) == 1

    def test_the_empty_call_is_metered_in_full_as_an_error(self, chain):
        FakeSttProvider.result = _empty("fake-stt")

        services.transcribe(
            AUDIO, audio_content_hash=HASH, audio_duration_ms=TEN_MINUTES_MS
        )

        assert PromptLog.objects.count() == 2
        empty = PromptLog.objects.get(model="fake-stt")
        served = PromptLog.objects.get(model="fake-stt-2")
        assert empty.status == PromptStatus.ERROR
        assert empty.audio_duration_ms == TEN_MINUTES_MS  # the provider charged for it
        assert "empty transcript" in (empty.error_message or "")
        assert served.status == PromptStatus.SUCCESS

    def test_the_nothing_is_not_checkpointed(self, fake_stt, settings):
        settings.STAPEL_AGENT = {**settings.STAPEL_AGENT, "STT_FALLBACK_CHAIN": []}
        FakeSttProvider.result = _empty("fake-stt")

        first = services.transcribe(
            AUDIO, audio_content_hash=HASH, audio_duration_ms=TEN_MINUTES_MS
        )
        assert first["status"] == "failure"
        assert "fake-stt (empty)" in first["reason"]
        assert ProviderCheckpoint.objects.count() == 0

        # The provider recovered. The retry must reach it — before this a
        # retry was served the checkpointed nothing for a week.
        FakeSttProvider.reset()
        second = services.transcribe(
            AUDIO, audio_content_hash=HASH, audio_duration_ms=TEN_MINUTES_MS
        )
        assert second["status"] == "ok"
        assert second["cached"] is False
        assert len(FakeSttProvider.calls) == 1  # reset() emptied the list; this is the retry

    def test_a_short_clip_may_be_silent(self, chain):
        FakeSttProvider.result = _empty("fake-stt")

        result = services.transcribe(
            AUDIO, audio_content_hash=HASH, audio_duration_ms=2_000
        )

        assert result["status"] == "ok"
        assert result["provider_used"] == "fake-stt"
        assert len(SecondSttProvider.calls) == 0
        assert ProviderCheckpoint.objects.count() == 1

    def test_unmeasured_audio_cannot_be_judged(self, chain):
        """No caller length, a remote ref nothing probed: empty is accepted,
        as before — the meter has no number to weigh it against either."""
        FakeSttProvider.result = _empty("fake-stt")

        result = services.transcribe(AUDIO, audio_content_hash=HASH)

        assert result["status"] == "ok"
        assert result["provider_used"] == "fake-stt"

    def test_the_floor_is_configuration(self, chain, settings):
        settings.STAPEL_AGENT = {
            **settings.STAPEL_AGENT,
            "STT_QA": {"MAX_GAP_SECONDS": 5.0, "EMPTY_TRANSCRIPT_MIN_MS": 0},
        }
        FakeSttProvider.result = _empty("fake-stt")

        result = services.transcribe(
            AUDIO, audio_content_hash=HASH, audio_duration_ms=TEN_MINUTES_MS
        )

        assert result["status"] == "ok"
        assert result["provider_used"] == "fake-stt"


@pytest.mark.django_db
class TestEveryProviderEmptyIsAnInputFailure:
    """A client host, 2026-10-13: a ten-minute file of loud non-speech came
    back empty from both providers, the reply said only ``status: failure``,
    and the caller re-drove it four times — each pass paid both providers
    again for the same empty answer."""

    def test_all_empty_is_classified_as_input(self, chain):
        FakeSttProvider.result = _empty("fake-stt")
        SecondSttProvider.result = _empty("fake-stt-2")

        result = services.transcribe(
            AUDIO, audio_content_hash=HASH, audio_duration_ms=TEN_MINUTES_MS
        )

        assert result["status"] == "failure"
        assert result["failure_class"] == "input"
        assert result["reason_code"] == "empty"
        assert result["provider_attempts"] == [
            {"provider": "fake-stt", "reason": "empty"},
            {"provider": "fake-stt-2", "reason": "empty"},
        ]

    def test_empty_then_a_no_speech_job_error_is_input(self, chain):
        """The 2026-10-22 shape: the primary answers empty, the fallback runs
        the job and reports "no spoken audio". Both said the same thing about
        the audio, so the reply is an input failure the caller must not re-drive
        — before, the job error stopped the chain unclassified and the caller
        parked the recording for a paid retry."""
        from stapel_agent.stt import failures

        FakeSttProvider.result = _empty("fake-stt")
        SecondSttProvider.error = failures.job_error(
            "AssemblyAI job error: language_detection cannot be performed on "
            "files with no spoken audio.",
            provider="fake-stt-2",
        )

        result = services.transcribe(
            AUDIO, audio_content_hash=HASH, audio_duration_ms=35_006
        )

        assert result["status"] == "failure"
        assert result["failure_class"] == "input"
        assert result["reason_code"] == "empty"
        assert result["provider_attempts"] == [
            {"provider": "fake-stt", "reason": "empty"},
            {"provider": "fake-stt-2", "reason": "empty"},
        ]

    def test_any_other_job_error_still_stops_the_chain(self, chain):
        from stapel_agent.stt import failures

        FakeSttProvider.error = failures.job_error(
            "AssemblyAI job error: Transcoding failed", provider="fake-stt"
        )

        result = services.transcribe(
            AUDIO, audio_content_hash=HASH, audio_duration_ms=TEN_MINUTES_MS
        )

        assert result["status"] == "failure"
        assert "failure_class" not in result
        assert len(SecondSttProvider.calls) == 0

    def test_one_empty_and_one_transient_is_not_input(self, chain):
        from stapel_agent.stt.base import RetryableTranscriptionError

        FakeSttProvider.result = _empty("fake-stt")
        SecondSttProvider.error = RetryableTranscriptionError("timeout")

        result = services.transcribe(
            AUDIO, audio_content_hash=HASH, audio_duration_ms=TEN_MINUTES_MS
        )

        assert result["status"] == "failure"
        assert "failure_class" not in result
        assert [a["provider"] for a in result["provider_attempts"]] == [
            "fake-stt", "fake-stt-2",
        ]
