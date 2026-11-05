"""The transcribe reply names the wire model; summarize can ask for the
speaker names the transcript states, in the same call."""
import pytest

from stapel_agent import functions, metering, services, summary
from stapel_agent.models import PromptLog, ProviderCheckpoint
from stapel_agent.providers.base import ProviderResult
from stapel_agent.stt.base import AudioRef
from stapel_agent.tests.fakes import FakeSttProvider

AUDIO = AudioRef(url="https://minio.test/bucket/rec.mp3")
HASH = "sha256:" + "b" * 64


@pytest.fixture
def pinned(fake_stt, monkeypatch):
    monkeypatch.setattr(FakeSttProvider, "speech_model", "fake_model_v2")
    return FakeSttProvider


@pytest.mark.django_db
class TestTranscribeModel:
    def test_the_reply_carries_the_wire_model_not_the_registration(self, pinned):
        reply = services.transcribe(AUDIO, audio_content_hash=HASH)
        assert reply["provider_used"] == "fake-stt"
        assert reply["model"] == "fake_model_v2"
        assert reply["diar_model"] is None

    def test_the_ledger_keeps_the_registration_and_meters_the_model(self, pinned):
        with metering.scope() as meter:
            services.transcribe(AUDIO, audio_content_hash=HASH)
        row = PromptLog.objects.get()
        assert row.model == "fake-stt"
        assert row.metadata["model"] == "fake_model_v2"
        reply = meter.summary()
        assert reply["provider"] == "fake-stt"
        assert reply["model"] == "fake_model_v2"

    def test_a_checkpoint_hit_names_the_model_that_was_paid_for(
        self, pinned, monkeypatch
    ):
        services.transcribe(AUDIO, audio_content_hash=HASH)
        # The configured default moves; the pin (part of the key) does not.
        monkeypatch.setattr(
            FakeSttProvider, "default_speech_model", lambda self: "fake_model_v3"
        )
        hit = services.transcribe(AUDIO, audio_content_hash=HASH)
        assert hit["cached"] is True
        assert hit["model"] == "fake_model_v2"

    def test_an_old_checkpoint_without_a_model_answers_null(self, pinned):
        services.transcribe(AUDIO, audio_content_hash=HASH)
        cp = ProviderCheckpoint.objects.get()
        value = dict(cp.value)
        value.pop("model")
        cp.value = value
        cp.save()
        hit = services.transcribe(AUDIO, audio_content_hash=HASH)
        assert hit["cached"] is True
        assert hit["model"] is None

    def test_an_adapter_without_a_model_answers_null(self, fake_stt):
        reply = services.transcribe(AUDIO, audio_content_hash=HASH)
        assert reply["model"] is None

    def test_the_reference_reply_carries_the_model(self, pinned, monkeypatch):
        monkeypatch.setattr(
            "stapel_agent.handoff.put_json_or_raise",
            lambda url, data, key="": {"key": key, "bytes": 1, "sha256": "x",
                                       "content_type": "application/json"},
        )
        reply = functions.llm_transcribe(
            {"audio_url": AUDIO.url, "audio_content_hash": HASH,
             "transcript_put_url": "https://minio.test/put", "transcript_key": "k"}
        )
        assert reply["transcript_ref"]["key"] == "k"
        assert reply["model"] == "fake_model_v2"
        assert reply["diar_model"] is None
        assert reply["metering"]["model"] == "fake_model_v2"
        assert reply["metering"]["provider"] == "fake-stt"


GUESSES = (
    '[{"speaker": "spk_0", "suggested_name": "Alice", '
    '"evidence_text": "Hi, I am Alice", "confidence": "high"}, '
    '{"speaker": "spk_1", "suggested_name": "", "evidence_text": "x", '
    '"confidence": "high"}]'
)


@pytest.mark.django_db
class TestSpeakerNames:
    def test_off_the_prompts_and_reply_are_unchanged(self, fake_provider):
        fake_provider.result = ProviderResult(text="The summary.")
        reply = services.summarize("short text", language="de")
        (call,) = fake_provider.calls
        assert call["system_prompt"] == summary.SUMMARY_SYSTEM_PROMPT + " Respond in de."
        assert "speaker_name_guesses" not in reply
        assert "speaker_names" not in reply
        assert reply["summary"] == "The summary."

    def test_on_the_rule_goes_before_the_language_and_the_block_is_cut(
        self, fake_provider
    ):
        fake_provider.result = ProviderResult(
            text=f"The summary.\n\n{summary.SPEAKER_NAMES_MARKER}\n{GUESSES}"
        )
        reply = services.summarize("short text", language="de", speaker_names=True)
        (call,) = fake_provider.calls
        assert call["system_prompt"] == (
            summary.SUMMARY_SYSTEM_PROMPT + summary.SPEAKER_NAMES_RULE + " Respond in de."
        )
        assert reply["summary"] == "The summary."
        assert reply["speaker_name_guesses"] == [
            {"speaker": "spk_0", "suggested_name": "Alice",
             "evidence_text": "Hi, I am Alice", "confidence": "high"}
        ]
        assert reply["speaker_names"] == {"parts": 1, "parsed": 1, "dropped": 1}

    def test_a_reply_without_the_block_says_so(self, fake_provider):
        fake_provider.result = ProviderResult(text="The summary.")
        reply = services.summarize("short text", speaker_names=True)
        assert reply["speaker_name_guesses"] == []
        assert reply["speaker_names"] == {"parts": 1, "parsed": 0, "dropped": 0}

    def test_chunks_are_asked_and_the_merge_is_not(self, fake_provider):
        fake_provider.result = ProviderResult(
            text=f"Part notes.\n{summary.SPEAKER_NAMES_MARKER}\n```json\n{GUESSES}\n```"
        )
        long_text = "\n\n".join(f"Paragraph {i} " + "word " * 30 for i in range(6))
        reply = services.summarize(long_text, chunk_tokens=20, speaker_names=True)
        *chunks, merge = fake_provider.calls
        assert chunks and all(summary.SPEAKER_NAMES_RULE in c["system_prompt"] for c in chunks)
        assert summary.SPEAKER_NAMES_RULE not in merge["system_prompt"]
        assert summary.SPEAKER_NAMES_MARKER not in merge["prompt"]
        assert len(reply["speaker_name_guesses"]) == len(chunks)

    def test_the_part_keys_differ_with_the_rule(self, fake_provider):
        fake_provider.result = ProviderResult(text="The summary.")
        services.summarize("short text", idempotency_key="summary:abc")
        services.summarize("short text", idempotency_key="summary:abc", speaker_names=True)
        assert len(fake_provider.calls) == 2

    def test_the_comm_schema_accepts_the_flag(self):
        assert functions.SUMMARIZE_SCHEMA["properties"]["speaker_names"]["type"] == "boolean"
