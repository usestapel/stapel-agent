"""STT routing and the transcribe service — chain precedence, fallback on
retryable-only, and the PromptLog ledger rows (source=transcribe)."""
import pytest

from stapel_agent import services
from stapel_agent.models import PromptLog, PromptSource, PromptStatus
from stapel_agent.stt.base import AudioRef
from stapel_agent.stt.router import select_chain
from stapel_agent.tests.fakes import (
    FakeSttProvider,
    FatalSttProvider,
    RetryableSttProvider,
    SecondSttProvider,
)

AUDIO = AudioRef(url="https://minio.test/bucket/rec.mp3?X-Sig=s3cr3t")


class TestSelectChain:
    def test_default_chain_when_nothing_configured(self, settings):
        settings.STAPEL_AGENT = {}
        assert select_chain(None) == ["whisper-http"]

    def test_default_plus_fallback_chain(self, settings):
        settings.STAPEL_AGENT = {
            "DEFAULT_STT_PROVIDER": "a",
            "STT_FALLBACK_CHAIN": ["b", "c"],
        }
        assert select_chain(None) == ["a", "b", "c"]

    def test_explicit_provider_wins_and_disables_fallback(self, settings):
        settings.STAPEL_AGENT = {
            "DEFAULT_STT_PROVIDER": "a",
            "STT_FALLBACK_CHAIN": ["b"],
            "STT_LANGUAGE_ROUTES": {"ru": ["gigaam"]},
        }
        assert select_chain("ru", provider="elevenlabs") == ["elevenlabs"]

    def test_language_route_beats_default_chain(self, settings):
        settings.STAPEL_AGENT = {
            "DEFAULT_STT_PROVIDER": "a",
            "STT_FALLBACK_CHAIN": ["b"],
            "STT_LANGUAGE_ROUTES": {"ru": ["gigaam", "whisper-http"]},
        }
        assert select_chain("ru") == ["gigaam", "whisper-http"]

    def test_language_is_normalized_before_route_lookup(self, settings):
        settings.STAPEL_AGENT = {"STT_LANGUAGE_ROUTES": {"ru": ["gigaam"]}}
        assert select_chain("ru-RU") == ["gigaam"]
        assert select_chain("RU_ru") == ["gigaam"]

    def test_unrouted_language_falls_back_to_default_chain(self, settings):
        settings.STAPEL_AGENT = {
            "DEFAULT_STT_PROVIDER": "a",
            "STT_LANGUAGE_ROUTES": {"ru": ["gigaam"]},
        }
        assert select_chain("en") == ["a"]

    def test_chain_is_deduplicated_in_order(self, settings):
        settings.STAPEL_AGENT = {
            "DEFAULT_STT_PROVIDER": "a",
            "STT_FALLBACK_CHAIN": ["b", "a", "b", "c"],
        }
        assert select_chain(None) == ["a", "b", "c"]

    def test_falsy_names_are_dropped(self, settings):
        settings.STAPEL_AGENT = {
            "DEFAULT_STT_PROVIDER": "",
            "STT_FALLBACK_CHAIN": ["b", None, ""],
        }
        assert select_chain(None) == ["b"]


@pytest.mark.django_db
class TestTranscribeService:
    def test_happy_path(self, fake_stt):
        result = services.transcribe(AUDIO, language="en", diarization=True)
        assert result["status"] == "ok"
        assert result["provider_used"] == "fake-stt"
        assert result["fallback_used"] is False
        transcript = result["transcript"]
        assert transcript["provider"] == "fake-stt"
        assert transcript["utterances"][0]["text"] == "hello world"
        call = fake_stt.calls[0]
        assert call["audio"] is AUDIO
        assert call["language"] == "en"
        assert call["diarization"] is True

    def test_non_audioref_input_is_failure(self, fake_stt):
        result = services.transcribe("https://x/a.mp3")
        assert result == {"status": "failure", "reason": "audio must be an AudioRef"}
        assert fake_stt.calls == []

    def test_unknown_explicit_provider_is_failure(self, fake_stt):
        result = services.transcribe(AUDIO, provider="ghost")
        assert result["status"] == "failure"
        assert "ghost" in result["reason"]
        assert fake_stt.calls == []

    def test_empty_chain_is_failure(self, settings):
        settings.STAPEL_AGENT = {"DEFAULT_STT_PROVIDER": ""}
        result = services.transcribe(AUDIO)
        assert result == {"status": "failure", "reason": "No STT provider configured"}

    def test_retryable_failure_walks_the_chain(self, settings, fake_stt):
        settings.STAPEL_AGENT = {
            **settings.STAPEL_AGENT,
            "DEFAULT_STT_PROVIDER": "retry-stt",
            "STT_FALLBACK_CHAIN": ["fake-stt-2"],
        }
        result = services.transcribe(AUDIO)
        assert result["status"] == "ok"
        assert result["provider_used"] == "fake-stt-2"
        assert result["fallback_used"] is True
        assert len(RetryableSttProvider.calls) == 1
        assert len(SecondSttProvider.calls) == 1

    def test_fatal_failure_does_not_fall_back(self, settings, fake_stt):
        settings.STAPEL_AGENT = {
            **settings.STAPEL_AGENT,
            "DEFAULT_STT_PROVIDER": "fatal-stt",
            "STT_FALLBACK_CHAIN": ["fake-stt"],
        }
        result = services.transcribe(AUDIO)
        assert result == {"status": "failure", "reason": "audio is not decodable"}
        assert len(FatalSttProvider.calls) == 1
        assert FakeSttProvider.calls == []  # never consulted

    def test_explicit_provider_never_falls_back(self, settings, fake_stt):
        settings.STAPEL_AGENT = {
            **settings.STAPEL_AGENT,
            "STT_FALLBACK_CHAIN": ["fake-stt"],
        }
        result = services.transcribe(AUDIO, provider="retry-stt")
        assert result == {"status": "failure", "reason": "stt rate limited"}
        assert FakeSttProvider.calls == []

    def test_all_retryable_reports_last_reason(self, settings, fake_stt):
        settings.STAPEL_AGENT = {
            **settings.STAPEL_AGENT,
            "DEFAULT_STT_PROVIDER": "retry-stt",
            "STT_FALLBACK_CHAIN": [],
        }
        result = services.transcribe(AUDIO)
        assert result == {"status": "failure", "reason": "stt rate limited"}

    def test_unloadable_provider_is_skipped(self, settings, fake_stt):
        settings.STAPEL_AGENT = {
            **settings.STAPEL_AGENT,
            "STT_PROVIDERS": {
                **settings.STAPEL_AGENT["STT_PROVIDERS"],
                "broken": "no.such.module.SttCls",
            },
            "DEFAULT_STT_PROVIDER": "broken",
            "STT_FALLBACK_CHAIN": ["fake-stt"],
        }
        result = services.transcribe(AUDIO)
        assert result["status"] == "ok"
        assert result["provider_used"] == "fake-stt"
        assert result["fallback_used"] is True

    def test_language_route_drives_provider_choice(self, settings, fake_stt):
        settings.STAPEL_AGENT = {
            **settings.STAPEL_AGENT,
            "STT_LANGUAGE_ROUTES": {"ru": ["fake-stt-2"]},
        }
        result = services.transcribe(AUDIO, language="ru-RU")
        assert result["provider_used"] == "fake-stt-2"
        assert FakeSttProvider.calls == []


@pytest.mark.django_db
class TestTranscribeLedger:
    def test_success_row_has_null_tokens_and_metadata(self, fake_stt):
        services.transcribe(
            AUDIO, language="en", user_id="u-9", metadata={"origin": "test"}
        )
        log = PromptLog.objects.get()
        assert log.source == PromptSource.TRANSCRIBE
        assert log.status == PromptStatus.SUCCESS
        assert log.model == "fake-stt"  # provider name, not an LLM model
        assert log.model_size == ""
        # PII-safe prompt: host only, no signed query string
        assert log.prompt == "url:minio.test"
        assert log.response == "hello world"
        # STT has no token accounting — the ledger columns stay NULL.
        assert log.input_tokens is None
        assert log.output_tokens is None
        assert log.thinking_tokens is None
        assert log.cache_read_tokens is None
        assert log.cache_write_tokens is None
        assert log.duration_ms is not None
        assert log.user_id == "u-9"
        assert log.metadata["origin"] == "test"
        assert log.metadata["audio"] == "url:minio.test"
        assert log.metadata["language"] == "en"
        assert log.metadata["fallback_used"] is False
        assert log.metadata["attempts"] == [
            {"provider": "fake-stt", "error_kind": None, "error": None}
        ]

    def test_fallback_success_row_records_attempts(self, settings, fake_stt):
        settings.STAPEL_AGENT = {
            **settings.STAPEL_AGENT,
            "DEFAULT_STT_PROVIDER": "retry-stt",
            "STT_FALLBACK_CHAIN": ["fake-stt"],
        }
        services.transcribe(AUDIO)
        log = PromptLog.objects.get()
        assert log.status == PromptStatus.SUCCESS
        assert log.model == "fake-stt"
        assert log.metadata["fallback_used"] is True
        assert [a["error_kind"] for a in log.metadata["attempts"]] == [
            "retryable",
            None,
        ]

    def test_fatal_failure_row(self, settings, fake_stt):
        settings.STAPEL_AGENT = {
            **settings.STAPEL_AGENT,
            "DEFAULT_STT_PROVIDER": "fatal-stt",
        }
        services.transcribe(AUDIO)
        log = PromptLog.objects.get()
        assert log.status == PromptStatus.ERROR
        assert log.model == "fatal-stt"
        assert log.error_message == "audio is not decodable"
        assert log.response is None
        assert log.metadata["attempts"][0]["error_kind"] == "fatal"

    def test_exhausted_chain_writes_one_error_row(self, settings, fake_stt):
        settings.STAPEL_AGENT = {
            **settings.STAPEL_AGENT,
            "DEFAULT_STT_PROVIDER": "retry-stt",
        }
        services.transcribe(AUDIO)
        log = PromptLog.objects.get()
        assert log.status == PromptStatus.ERROR
        assert log.error_message == "stt rate limited"
