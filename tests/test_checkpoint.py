"""The paid call is a checkpoint — a retry above it must not pay again.

The incident these tests are written against, from a client stand's
production data (2026-09-12): ONE 148-minute recording was transcribed at
ElevenLabs SIX times — two tasks × three attempts — because the step that
failed was the reply (a 8.6 MB transcript against a broker that carries
8), which sits AFTER the money. 59.7% of a 23,736-credit quota went to
machine duplication of identical media. Two of those calls came back with
an empty transcript and were metered at 0 minutes, because the meter read
``transcript.duration_seconds`` — the last word's end timestamp — instead
of the audio that was submitted.

So: the provider's answer is stored under the input's key BEFORE anything
downstream runs, and the ledger meters what was SUBMITTED.
"""
import pytest

from stapel_agent import services
from stapel_agent.models import CostBasis, ProviderCheckpoint, PromptLog
from stapel_agent.stt.base import AudioRef, NormalizedTranscript
from stapel_agent.tests.fakes import FakeSttProvider, SecondSttProvider

#: A remote ref, like every real call: the audio lives in the bucket and
#: the provider fetches it. There are no local bytes to hash, which is
#: exactly why ``audio_content_hash`` has to travel.
AUDIO = AudioRef(url="https://minio.test/bucket/rec.mp3?X-Sig=s3cr3t")
HASH = "sha256:" + "a" * 64

# A 148-minute meeting, in milliseconds — the recording from the incident.
SUBMITTED_MS = 148 * 60 * 1000


@pytest.mark.django_db
class TestTranscribeCheckpoint:
    def test_a_second_call_with_the_same_key_does_not_reach_the_provider(
        self, fake_stt
    ):
        first = services.transcribe(AUDIO, audio_content_hash=HASH)
        assert first["status"] == "ok"
        assert first["cached"] is False
        assert len(FakeSttProvider.calls) == 1

        second = services.transcribe(AUDIO, audio_content_hash=HASH)

        assert second["status"] == "ok"
        assert second["cached"] is True
        assert second["transcript"] == first["transcript"]
        # THE ASSERTION THE INCIDENT IS ABOUT: the provider was called
        # once for one piece of audio, not twice.
        assert len(FakeSttProvider.calls) == 1

    def test_the_cached_row_is_metered_at_zero_and_says_why(self, fake_stt):
        services.transcribe(AUDIO, audio_content_hash=HASH)
        services.transcribe(AUDIO, audio_content_hash=HASH)

        rows = list(PromptLog.objects.order_by("created_at"))
        assert len(rows) == 2, "a checkpoint hit is metered, never hidden"
        assert rows[1].cost_basis == CostBasis.CACHED
        assert rows[1].cost_usd == 0
        assert rows[1].metadata["attempts"] == [
            {"provider": "fake-stt", "cached": True}
        ]
        assert rows[1].metadata["cached"] is True

    def test_a_different_language_is_a_different_call(self, fake_stt):
        services.transcribe(AUDIO, audio_content_hash=HASH, language="en")
        services.transcribe(AUDIO, audio_content_hash=HASH, language="de")

        # Two calls, two provider hits: the language changes what a
        # correct transcript IS, so it is part of the key.
        assert len(FakeSttProvider.calls) == 2

    def test_diarization_and_keyterms_are_part_of_the_key(self, fake_stt):
        services.transcribe(AUDIO, audio_content_hash=HASH)
        services.transcribe(AUDIO, audio_content_hash=HASH, diarization=True)
        services.transcribe(
            AUDIO, audio_content_hash=HASH, diarization=True, keyterms=["Stapel"]
        )
        assert len(FakeSttProvider.calls) == 3

    def test_a_different_provider_is_a_different_call(self, fake_stt, settings):
        services.transcribe(AUDIO, audio_content_hash=HASH, provider="fake-stt")
        services.transcribe(AUDIO, audio_content_hash=HASH, provider="fake-stt-2")

        assert len(FakeSttProvider.calls) == 1
        assert len(SecondSttProvider.calls) == 1

    def test_another_tenant_never_reads_this_ones_transcript(self, fake_stt):
        services.transcribe(AUDIO, audio_content_hash=HASH, workspace_id="ws-1")
        services.transcribe(AUDIO, audio_content_hash=HASH, workspace_id="ws-2")

        # AGENT-02: identical input, different tenant, no hit. Holding the
        # same file is not entitlement to another tenant's answer.
        assert len(FakeSttProvider.calls) == 2

    def test_an_expired_checkpoint_calls_the_provider_again(
        self, fake_stt, settings
    ):
        settings.STAPEL_AGENT = {
            **settings.STAPEL_AGENT,
            "STT_RESULT_TTL_SECONDS": 604800,
        }
        services.transcribe(AUDIO, audio_content_hash=HASH)
        assert len(FakeSttProvider.calls) == 1

        # Age the row past the window rather than the clock: the rule is
        # "older than the TTL", and that is what a test should state.
        from datetime import timedelta

        from django.utils import timezone

        ProviderCheckpoint.objects.update(
            created_at=timezone.now() - timedelta(seconds=604800 + 60)
        )

        result = services.transcribe(AUDIO, audio_content_hash=HASH)
        assert result["cached"] is False
        assert len(FakeSttProvider.calls) == 2

    def test_a_remote_ref_without_a_hash_is_never_checkpointed(self, fake_stt):
        # Nothing identifies the media, so nothing may be served for it —
        # and the call says so in the log rather than guessing.
        services.transcribe(AUDIO)
        services.transcribe(AUDIO)

        assert len(FakeSttProvider.calls) == 2
        assert ProviderCheckpoint.objects.count() == 0

    def test_the_hash_is_computed_from_the_bytes_when_none_is_given(
        self, fake_stt
    ):
        # A local ref HAS the bytes, so the caller owes nothing: the
        # identity of the media is computed here, once, without a second
        # download.
        local = AudioRef(data=b"RIFFfake-audio-bytes")
        services.transcribe(local)
        services.transcribe(local)

        assert len(FakeSttProvider.calls) == 1
        assert ProviderCheckpoint.objects.count() == 1

    def test_the_checkpoint_is_written_before_the_reply_can_fail(
        self, fake_stt, monkeypatch
    ):
        # The exact production shape: the transcription succeeds, the
        # handoff PUT fails, the task is retried. The retry must not buy
        # the transcript again.
        from stapel_agent import functions

        payload = {
            "audio_url": AUDIO.url,
            "audio_content_hash": HASH,
            "transcript_put_url": "https://minio.test/put",
            "transcript_key": "rec/1/transcript.raw.json",
        }

        def explode(*args, **kwargs):
            from stapel_agent.handoff import HandoffError

            raise HandoffError("502 from the object store")

        import stapel_agent.handoff as handoff

        monkeypatch.setattr(handoff, "put_json_or_raise", explode)

        first = functions.llm_transcribe(payload)
        assert first["status"] == "failure"
        assert first["reason"].startswith("transcript_handoff_failed")
        assert len(FakeSttProvider.calls) == 1

        second = functions.llm_transcribe(payload)
        assert second["status"] == "failure"
        # THE POINT: the retry re-ran the handoff, not the provider.
        assert len(FakeSttProvider.calls) == 1

    def test_the_setting_can_turn_it_off(self, fake_stt, settings):
        settings.STAPEL_AGENT = {
            **settings.STAPEL_AGENT,
            "CHECKPOINT_ENABLED": False,
        }
        services.transcribe(AUDIO, audio_content_hash=HASH)
        services.transcribe(AUDIO, audio_content_hash=HASH)

        assert len(FakeSttProvider.calls) == 2
        assert ProviderCheckpoint.objects.count() == 0


@pytest.mark.django_db
class TestSubmittedAudioIsWhatIsMetered:
    def test_an_empty_transcript_is_metered_by_what_was_submitted(
        self, fake_stt
    ):
        # The two ElevenLabs calls from the incident: nothing came back,
        # the provider billed in full, and the ledger recorded zero
        # because the duration was derived from the last word's end.
        FakeSttProvider.result = NormalizedTranscript(
            provider="fake-stt", language="en", duration_seconds=None
        )

        result = services.transcribe(
            AUDIO, audio_content_hash=HASH, audio_duration_ms=SUBMITTED_MS
        )

        assert result["status"] == "ok"
        row = PromptLog.objects.get()
        assert row.audio_duration_ms == SUBMITTED_MS
        assert row.metadata["audio_submitted_ms"] == SUBMITTED_MS
        assert row.metadata["audio_submitted_source"] == "caller"
        assert row.metadata["audio_reported_ms"] is None
        # The length is what a rate card multiplies, so recording it is
        # what makes the row priceable at all: before this change the
        # same row read 0 ms and priced as free. (This fake has no card,
        # so the basis stays "unpriced" — _stt_cost's own tests cover the
        # multiplication.)
        assert row.audio_duration_ms > 0

    def test_the_submitted_length_wins_over_the_providers_own(self, fake_stt):
        # 148 minutes submitted, the provider's last word ends at 2s. The
        # meter must not believe the second number.
        result = services.transcribe(
            AUDIO, audio_content_hash=HASH, audio_duration_ms=SUBMITTED_MS
        )
        assert result["status"] == "ok"

        row = PromptLog.objects.get()
        assert row.audio_duration_ms == SUBMITTED_MS
        assert row.metadata["audio_reported_ms"] == 2000

    def test_the_providers_number_is_the_fallback_when_nothing_measured(
        self, fake_stt
    ):
        services.transcribe(AUDIO, audio_content_hash=HASH)

        row = PromptLog.objects.get()
        assert row.metadata["audio_submitted_ms"] is None
        assert row.metadata["audio_submitted_source"] == "unknown"
        assert row.audio_duration_ms == 2000

    def test_a_wav_header_measures_the_submission_without_a_caller(
        self, fake_stt
    ):
        import io
        import wave

        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(16000)
            handle.writeframes(b"\x00\x00" * 16000 * 3)  # exactly 3 seconds

        services.transcribe(AudioRef(data=buffer.getvalue()))

        row = PromptLog.objects.get()
        assert row.metadata["audio_submitted_source"] == "wave"
        assert row.audio_duration_ms == 3000

    def test_a_cached_hit_still_meters_the_audio_at_zero_cost(self, fake_stt):
        services.transcribe(
            AUDIO, audio_content_hash=HASH, audio_duration_ms=SUBMITTED_MS
        )
        services.transcribe(
            AUDIO, audio_content_hash=HASH, audio_duration_ms=SUBMITTED_MS
        )

        rows = list(PromptLog.objects.order_by("created_at"))
        # Both rows carry the minutes (the work was asked for twice), and
        # only the first carries a price (it was paid for once). A meter
        # that bills minutes has to exclude cost_basis="cached".
        assert [r.audio_duration_ms for r in rows] == [SUBMITTED_MS, SUBMITTED_MS]
        assert rows[1].cost_usd == 0
        assert rows[1].cost_basis == CostBasis.CACHED


@pytest.mark.django_db
class TestCompleteCheckpoint:
    def test_without_an_idempotency_key_every_call_reaches_the_provider(
        self, fake_provider
    ):
        services.complete("hi", "medium")
        services.complete("hi", "medium")

        # A completion is SAMPLED: two deliberate asks must be allowed to
        # differ, so the input alone is never a checkpoint key.
        assert len(fake_provider.calls) == 2
        assert ProviderCheckpoint.objects.count() == 0

    def test_the_same_attempt_retried_is_served_from_the_checkpoint(
        self, fake_provider
    ):
        first = services.complete("hi", "medium", idempotency_key="task-7")
        second = services.complete("hi", "medium", idempotency_key="task-7")

        assert len(fake_provider.calls) == 1
        assert second["result"] == first["result"]
        assert second["usage"]["cost_basis"] == CostBasis.CACHED
        assert second["usage"]["cost_usd"] == 0

    def test_a_changed_prompt_under_the_same_key_is_not_served_stale(
        self, fake_provider
    ):
        services.complete("hi", "medium", idempotency_key="task-7")
        services.complete("different", "medium", idempotency_key="task-7")

        assert len(fake_provider.calls) == 2


@pytest.mark.django_db
class TestCheckpointHousekeeping:
    def test_erasure_deletes_the_subjects_checkpoints(self, fake_stt):
        from stapel_agent.gdpr import erase_subject

        services.transcribe(AUDIO, audio_content_hash=HASH, user_id="u-1")
        assert ProviderCheckpoint.objects.filter(user_id="u-1").count() == 1

        erase_subject("account", "u-1")

        # A ledger row is scrubbed and kept for accounting; a checkpoint
        # is content with no accounting half, so it goes.
        assert ProviderCheckpoint.objects.filter(user_id="u-1").count() == 0
        assert PromptLog.objects.count() == 1

    def test_retention_deletes_expired_checkpoints_only(self, fake_stt):
        from datetime import timedelta

        from django.utils import timezone

        from stapel_agent.retention import purge_checkpoints

        services.transcribe(AUDIO, audio_content_hash=HASH)
        ProviderCheckpoint.objects.create(
            key="f" * 64, surface="transcribe", provider="fake-stt", value={}
        )
        ProviderCheckpoint.objects.filter(key="f" * 64).update(
            created_at=timezone.now() - timedelta(days=30)
        )

        assert purge_checkpoints() == 1
        assert ProviderCheckpoint.objects.count() == 1

    def test_a_storage_failure_never_costs_the_answer(self, fake_stt, monkeypatch):
        # The store is best effort by design: losing a checkpoint costs
        # money on a future retry, losing the transcript costs the call.
        def explode(*args, **kwargs):
            raise RuntimeError("database gone")

        monkeypatch.setattr(
            ProviderCheckpoint.objects, "update_or_create", explode
        )
        result = services.transcribe(AUDIO, audio_content_hash=HASH)
        assert result["status"] == "ok"
