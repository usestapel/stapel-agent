"""comm Function tests — llm.complete / llm.translate / llm.transcribe /
llm.summarize called in-process via stapel_core.comm.call with schema
validation ON."""
import pytest
from stapel_core.comm import call, function_registry
from stapel_core.comm.exceptions import SchemaValidationError

from stapel_agent.providers.base import ProviderResult


class TestRegistration:
    def test_functions_registered(self):
        names = function_registry.names()
        assert "llm.complete" in names
        assert "llm.translate" in names
        assert "llm.transcribe" in names
        assert "llm.summarize" in names


@pytest.mark.django_db
class TestLlmComplete:
    def test_happy_path(self, fake_provider):
        result = call("llm.complete", {"prompt": "give json", "model": "small"})
        assert result["status"] == "ok"
        assert result["result"] == {"answer": 42}
        assert result["usage"] == {"input_tokens": 10, "output_tokens": 5}

    def test_provider_and_system_prompt_forwarded(self, fake_provider):
        call(
            "llm.complete",
            {
                "prompt": "p",
                "model": "medium",
                "system_prompt": "custom",
                "provider": "fake",
            },
        )
        assert fake_provider.calls[0]["system_prompt"] == "custom"
        assert fake_provider.calls[0]["model"] == "claude-sonnet-5"

    def test_schema_rejects_missing_prompt(self, fake_provider):
        with pytest.raises(SchemaValidationError):
            call("llm.complete", {"model": "small"})

    def test_schema_rejects_bad_model_size(self, fake_provider):
        with pytest.raises(SchemaValidationError):
            call("llm.complete", {"prompt": "p", "model": "xl"})

    def test_schema_rejects_extra_keys(self, fake_provider):
        with pytest.raises(SchemaValidationError):
            call("llm.complete", {"prompt": "p", "model": "small", "beep": 1})

    def test_role_tag_admitted_and_ignored(self, fake_provider):
        # Multi-role pipelines tag every call with the calling role (their
        # scripted/override providers are content-addressed by it); the
        # schema admits the tag, the default pipeline ignores it. Regression:
        # additionalProperties:false used to refuse every such call the
        # moment schema validation was on.
        result = call(
            "llm.complete",
            {"prompt": "give json", "model": "small", "role": "architect"},
        )
        assert result["status"] == "ok"
        assert result["result"] == {"answer": 42}
        # the tag never leaks into the provider call
        assert "role" not in fake_provider.calls[0]

    def test_role_tag_must_be_a_string(self, fake_provider):
        with pytest.raises(SchemaValidationError):
            call("llm.complete", {"prompt": "p", "model": "small", "role": 7})

    def test_max_tokens_admitted_and_forwarded(self, fake_provider):
        # Per-role output budgets: a long structured output (e.g. a file
        # manifest) raises the cap per call instead of a global MAX_TOKENS
        # bump. Regression: the 4096 default truncated a manifest mid-file
        # (stop_reason=max_tokens) and the parse failed downstream.
        result = call(
            "llm.complete",
            {"prompt": "give json", "model": "small", "max_tokens": 16000},
        )
        assert result["status"] == "ok"
        assert fake_provider.calls[0]["max_tokens"] == 16000

    def test_max_tokens_omitted_means_provider_default(self, fake_provider):
        call("llm.complete", {"prompt": "p", "model": "small"})
        assert fake_provider.calls[0]["max_tokens"] is None

    def test_schema_rejects_bad_max_tokens(self, fake_provider):
        for bad in (0, -1, "16000", 2.5):
            with pytest.raises(SchemaValidationError):
                call(
                    "llm.complete",
                    {"prompt": "p", "model": "small", "max_tokens": bad},
                )


@pytest.mark.django_db
class TestLlmTranslate:
    def test_happy_path(self, fake_provider):
        fake_provider.result = ProviderResult(text='{"k": "Hallo"}')
        result = call(
            "llm.translate",
            {"from_lang": "auto", "to": "de", "entries": {"k": "Hello"}},
        )
        assert result == {"status": "ok", "result": {"k": "Hallo"}}

    def test_empty_entries_short_circuits(self, fake_provider):
        result = call(
            "llm.translate", {"from_lang": "auto", "to": "de", "entries": {}}
        )
        assert result == {"status": "ok", "result": {}}
        assert fake_provider.calls == []

    def test_schema_uses_from_lang_not_from(self, fake_provider):
        # The wire key "from" belongs to the HTTP surface only; the comm
        # payload uses the Python-safe "from_lang".
        with pytest.raises(SchemaValidationError):
            call("llm.translate", {"from": "auto", "to": "de", "entries": {}})

    def test_schema_rejects_non_string_entry_values(self, fake_provider):
        with pytest.raises(SchemaValidationError):
            call(
                "llm.translate",
                {"from_lang": "auto", "to": "de", "entries": {"k": 5}},
            )


@pytest.mark.django_db
class TestLlmTranscribe:
    def test_happy_path(self, fake_stt):
        result = call(
            "llm.transcribe",
            {
                "audio_url": "https://minio.test/rec.mp3",
                "language": "en",
                "diarization": True,
            },
        )
        assert result["status"] == "ok"
        assert result["provider_used"] == "fake-stt"
        assert result["fallback_used"] is False
        assert result["transcript"]["utterances"][0]["text"] == "hello world"
        # the URL payload arrives as a url-kind AudioRef
        audio = fake_stt.calls[0]["audio"]
        assert audio.kind == "url"
        assert audio.url == "https://minio.test/rec.mp3"
        assert fake_stt.calls[0]["diarization"] is True

    def test_provider_pin_forwarded(self, fake_stt):
        from stapel_agent.tests.fakes import SecondSttProvider

        result = call(
            "llm.transcribe",
            {"audio_url": "https://x/a.mp3", "provider": "fake-stt-2"},
        )
        assert result["provider_used"] == "fake-stt-2"
        assert len(SecondSttProvider.calls) == 1

    def test_failure_is_a_status_dict_not_an_exception(self, fake_stt):
        result = call(
            "llm.transcribe",
            {"audio_url": "https://x/a.mp3", "provider": "fatal-stt"},
        )
        assert result == {"status": "failure", "reason": "audio is not decodable"}

    def test_schema_rejects_missing_audio_url(self, fake_stt):
        with pytest.raises(SchemaValidationError):
            call("llm.transcribe", {"language": "en"})

    def test_schema_rejects_raw_audio_bytes(self, fake_stt):
        # comm carries URLs only — bytes/path refs are HTTP-tier concerns;
        # any such key is rejected by additionalProperties: false.
        with pytest.raises(SchemaValidationError):
            call("llm.transcribe", {"audio_url": "https://x/a", "data": "UklGRg=="})
        with pytest.raises(SchemaValidationError):
            call("llm.transcribe", {"audio_url": "https://x/a", "path": "/tmp/a.wav"})

    def test_schema_rejects_non_integer_timeout(self, fake_stt):
        with pytest.raises(SchemaValidationError):
            call(
                "llm.transcribe",
                {"audio_url": "https://x/a", "timeout_seconds": "300"},
            )

    def test_schema_rejects_non_positive_timeout(self, fake_stt):
        # minimum: 1 — 0 and negatives are unexpressible to `requests`.
        for bad in (0, -1):
            with pytest.raises(SchemaValidationError):
                call(
                    "llm.transcribe",
                    {"audio_url": "https://x/a", "timeout_seconds": bad},
                )


@pytest.mark.django_db
class TestLlmSummarize:
    def test_happy_path_text(self, fake_provider):
        fake_provider.result = ProviderResult(
            text="## Summary", input_tokens=8, output_tokens=2
        )
        result = call("llm.summarize", {"text": "long meeting notes"})
        assert result == {
            "status": "ok",
            "summary": "## Summary",
            "usage": {"input_tokens": 8, "output_tokens": 2},
        }

    def test_happy_path_transcript(self, fake_provider):
        transcript = {
            "provider": "fake-stt",
            "language": "en",
            "duration_seconds": 2.0,
            "utterances": [
                {"text": "hello world", "start": 0.0, "end": 2.0, "speaker": "A"}
            ],
        }
        result = call("llm.summarize", {"transcript": transcript, "model": "small"})
        assert result["status"] == "ok"
        assert "[00:00] A: hello world" in fake_provider.calls[0]["prompt"]

    def test_schema_rejects_neither_input(self, fake_provider):
        with pytest.raises(SchemaValidationError):
            call("llm.summarize", {"language": "en"})

    def test_schema_rejects_both_inputs(self, fake_provider):
        # oneOf: text XOR transcript — sending both matches two branches.
        with pytest.raises(SchemaValidationError):
            call("llm.summarize", {"text": "t", "transcript": {"provider": "x"}})

    def test_schema_rejects_bad_model_size(self, fake_provider):
        with pytest.raises(SchemaValidationError):
            call("llm.summarize", {"text": "t", "model": "xl"})

    def test_schema_rejects_extra_keys(self, fake_provider):
        with pytest.raises(SchemaValidationError):
            call("llm.summarize", {"text": "t", "beep": 1})

    def test_failure_is_a_status_dict(self, fake_provider):
        from stapel_agent.providers.base import ProviderError

        fake_provider.error = ProviderError("llm down")
        result = call("llm.summarize", {"text": "t"})
        assert result == {"status": "failure", "reason": "llm down"}
