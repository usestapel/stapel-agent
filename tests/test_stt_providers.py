"""Built-in STT adapter tests — mocked ``requests``; no network, no keys.

Error taxonomy under test everywhere: 429/5xx/timeouts/transport →
``RetryableTranscriptionError`` (the service walks the fallback chain),
other 4xx / bad input / missing config → fatal ``TranscriptionError``.
"""
import json

import pytest
import requests

from stapel_agent.stt.base import (
    AudioRef,
    RetryableTranscriptionError,
    TranscriptionError,
)
from stapel_agent.stt.providers.assemblyai import AssemblyAIProvider
from stapel_agent.stt.providers.elevenlabs import ElevenLabsProvider
from stapel_agent.stt.providers.whisper_http import WhisperHttpProvider


class FakeResponse:
    def __init__(self, payload=None, status_code=200, text=None):
        self._payload = payload
        self.status_code = status_code
        self.text = text if text is not None else json.dumps(payload or {})

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def mock_post(monkeypatch, module, responses, captured):
    """Patch ``requests.post`` in *module*; *responses* is a list of
    FakeResponse/Exception consumed one per call."""
    queue = list(responses)

    def fake_post(url, headers=None, files=None, data=None, json=None, timeout=None):
        captured.append(
            {
                "url": url,
                "headers": headers,
                "files": files,
                "data": data,
                "json": json,
                "timeout": timeout,
            }
        )
        step = queue.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    monkeypatch.setattr(f"stapel_agent.stt.providers.{module}.requests.post", fake_post)


WHISPER_BODY = {
    "text": "hello world",
    "language": "english",
    "duration": 2.5,
    "words": [
        {"word": " hello", "start": 0.0, "end": 1.0},
        {"word": "world", "start": 1.2, "end": 2.4},
    ],
    "segments": [{"text": " hello world ", "start": 0.0, "end": 2.4}],
}


class TestWhisperHttp:
    @pytest.fixture
    def configured(self, settings):
        settings.STAPEL_AGENT = {
            "WHISPER_BASE_URL": "http://faster-whisper:8000/v1/",
            "WHISPER_MODEL": "large-v3",
        }
        return settings

    def _run(self, monkeypatch, responses, *, audio, **kwargs):
        captured = []
        mock_post(monkeypatch, "whisper_http", responses, captured)
        transcript = WhisperHttpProvider().transcribe(audio=audio, **kwargs)
        return transcript, captured

    def test_unconfigured_base_url_is_fatal(self, settings):
        settings.STAPEL_AGENT = {"WHISPER_BASE_URL": ""}
        with pytest.raises(TranscriptionError, match="WHISPER_BASE_URL"):
            WhisperHttpProvider().transcribe(audio=AudioRef(data=b"x"))

    def test_multipart_from_bytes(self, configured, monkeypatch):
        transcript, captured = self._run(
            monkeypatch,
            [FakeResponse(WHISPER_BODY)],
            audio=AudioRef(data=b"OGGdata", mime="audio/ogg"),
            language="en-US",
        )
        call = captured[0]
        assert call["url"] == "http://faster-whisper:8000/v1/audio/transcriptions"
        assert call["files"]["file"] == ("audio", b"OGGdata", "audio/ogg")
        assert call["data"]["model"] == "large-v3"
        assert call["data"]["response_format"] == "verbose_json"
        assert call["data"]["language"] == "en"  # normalized BCP-47 → ISO
        # No key configured → no Authorization header (self-hosted server).
        assert "Authorization" not in call["headers"]
        assert transcript.provider == "whisper-http"
        assert transcript.text == "hello world"
        assert [w.text for w in transcript.words] == ["hello", "world"]
        assert transcript.duration_seconds == 2.5
        assert transcript.raw == WHISPER_BODY

    def test_multipart_from_path(self, configured, monkeypatch, tmp_path):
        f = tmp_path / "rec.wav"
        f.write_bytes(b"RIFFbytes")
        _, captured = self._run(
            monkeypatch, [FakeResponse(WHISPER_BODY)], audio=AudioRef(path=str(f))
        )
        assert captured[0]["files"]["file"][1] == b"RIFFbytes"
        # No mime hint → generic content type.
        assert captured[0]["files"]["file"][2] == "application/octet-stream"

    def test_multipart_from_url_downloads_first(self, configured, monkeypatch):
        class DownloadResp:
            content = b"downloaded-audio"

            def raise_for_status(self):
                pass

        monkeypatch.setattr("requests.get", lambda *a, **kw: DownloadResp())
        _, captured = self._run(
            monkeypatch,
            [FakeResponse(WHISPER_BODY)],
            audio=AudioRef(url="https://cdn.test/a.mp3"),
        )
        assert captured[0]["files"]["file"][1] == b"downloaded-audio"

    def test_api_key_becomes_bearer_header(self, configured, monkeypatch, settings):
        settings.STAPEL_AGENT = {
            **settings.STAPEL_AGENT,
            "WHISPER_API_KEY": "sk-whisper",
        }
        _, captured = self._run(
            monkeypatch, [FakeResponse(WHISPER_BODY)], audio=AudioRef(data=b"x")
        )
        assert captured[0]["headers"]["Authorization"] == "Bearer sk-whisper"

    def test_timeout_seconds_caps_the_request(self, configured, monkeypatch):
        _, captured = self._run(
            monkeypatch,
            [FakeResponse(WHISPER_BODY)],
            audio=AudioRef(data=b"x"),
            timeout_seconds=42,
        )
        assert captured[0]["timeout"] == 42

    def test_none_timeout_uses_stt_timeout_default(self, configured, monkeypatch):
        # None (not passed) → STT_TIMEOUT; an explicit 0 must NOT be
        # coerced to the default by a falsy `or`.
        _, captured = self._run(
            monkeypatch, [FakeResponse(WHISPER_BODY)], audio=AudioRef(data=b"x")
        )
        assert captured[0]["timeout"] == 1800  # STT_TIMEOUT default

    def test_zero_timeout_is_passed_through(self, configured, monkeypatch):
        _, captured = self._run(
            monkeypatch,
            [FakeResponse(WHISPER_BODY)],
            audio=AudioRef(data=b"x"),
            timeout_seconds=0,
        )
        assert captured[0]["timeout"] == 0

    def test_429_is_retryable(self, configured, monkeypatch):
        with pytest.raises(RetryableTranscriptionError, match="rate-limited") as e:
            self._run(
                monkeypatch,
                [FakeResponse(status_code=429, text="slow down")],
                audio=AudioRef(data=b"x"),
            )
        assert e.value.status_code == 429

    def test_5xx_is_retryable(self, configured, monkeypatch):
        with pytest.raises(RetryableTranscriptionError, match="503"):
            self._run(
                monkeypatch,
                [FakeResponse(status_code=503, text="upstream down")],
                audio=AudioRef(data=b"x"),
            )

    def test_4xx_is_fatal(self, configured, monkeypatch):
        with pytest.raises(TranscriptionError, match="400") as e:
            self._run(
                monkeypatch,
                [FakeResponse(status_code=400, text="bad audio format")],
                audio=AudioRef(data=b"x"),
            )
        assert not isinstance(e.value, RetryableTranscriptionError)

    def test_timeout_is_retryable(self, configured, monkeypatch):
        with pytest.raises(RetryableTranscriptionError, match="timed out"):
            self._run(
                monkeypatch, [requests.Timeout("slow")], audio=AudioRef(data=b"x")
            )

    def test_transport_error_is_retryable(self, configured, monkeypatch):
        with pytest.raises(RetryableTranscriptionError, match="transport"):
            self._run(
                monkeypatch,
                [requests.ConnectionError("refused")],
                audio=AudioRef(data=b"x"),
            )

    def test_non_json_body_is_retryable(self, configured, monkeypatch):
        with pytest.raises(RetryableTranscriptionError, match="non-JSON"):
            self._run(
                monkeypatch,
                [FakeResponse(payload=None, text="<html>oops</html>")],
                audio=AudioRef(data=b"x"),
            )

    def test_text_only_body_synthesizes_one_utterance(self, configured, monkeypatch):
        transcript, _ = self._run(
            monkeypatch,
            [FakeResponse({"text": " just text ", "language": "en"})],
            audio=AudioRef(data=b"x"),
        )
        assert [u.text for u in transcript.utterances] == ["just text"]
        assert transcript.words == []
        assert transcript.duration_seconds is None

    def test_unparsable_duration_falls_back_to_word_ends(
        self, configured, monkeypatch
    ):
        body = {**WHISPER_BODY, "duration": "not-a-number"}
        transcript, _ = self._run(
            monkeypatch, [FakeResponse(body)], audio=AudioRef(data=b"x")
        )
        assert transcript.duration_seconds == 2.4  # max word end


ELEVENLABS_BODY = {
    "language_code": "en",
    "text": "Hi there Hello",
    "words": [
        {"text": "Hi", "start": 0.0, "end": 0.4, "type": "word", "speaker_id": "speaker_0"},
        {"text": " ", "start": 0.4, "end": 0.5, "type": "spacing", "speaker_id": "speaker_0"},
        {"text": "there", "start": 0.5, "end": 0.9, "type": "word", "speaker_id": "speaker_0"},
        {"text": "Hello", "start": 1.0, "end": 1.6, "type": "word", "speaker_id": "speaker_1"},
    ],
}


class TestElevenLabs:
    @pytest.fixture
    def configured(self, settings, monkeypatch):
        settings.STAPEL_AGENT = {"ELEVENLABS_API_KEY": "xi-test"}

        class DownloadResp:
            content = b"mp3-bytes"

            def raise_for_status(self):
                pass

        monkeypatch.setattr("requests.get", lambda *a, **kw: DownloadResp())
        return settings

    def _run(self, monkeypatch, responses, *, audio=None, **kwargs):
        captured = []
        mock_post(monkeypatch, "elevenlabs", responses, captured)
        transcript = ElevenLabsProvider().transcribe(
            audio=audio or AudioRef(url="https://cdn.test/a.mp3"), **kwargs
        )
        return transcript, captured

    def test_missing_key_is_fatal(self, settings):
        settings.STAPEL_AGENT = {"ELEVENLABS_API_KEY": ""}
        with pytest.raises(TranscriptionError, match="ELEVENLABS_API_KEY"):
            ElevenLabsProvider().transcribe(audio=AudioRef(url="https://x/a"))

    def test_bytes_ref_is_rejected(self, configured):
        with pytest.raises(TranscriptionError, match="requires an audio URL"):
            ElevenLabsProvider().transcribe(audio=AudioRef(data=b"x"))

    def test_happy_path_multipart_and_normalization(self, configured, monkeypatch):
        transcript, captured = self._run(
            monkeypatch,
            [FakeResponse(ELEVENLABS_BODY)],
            language="en-GB",
            diarization=True,
        )
        call = captured[0]
        assert call["url"] == "https://api.elevenlabs.io/v1/speech-to-text"
        assert call["headers"] == {"xi-api-key": "xi-test"}
        assert call["files"]["file"][1] == b"mp3-bytes"
        assert call["data"]["model_id"] == "scribe_v2"
        assert call["data"]["diarize"] == "true"
        assert call["data"]["language_code"] == "en"
        # spacing tokens skipped; same-speaker words grouped
        assert [w.text for w in transcript.words] == ["Hi", "there", "Hello"]
        assert [(u.speaker, u.text) for u in transcript.utterances] == [
            ("speaker_0", "Hi there"),
            ("speaker_1", "Hello"),
        ]
        assert transcript.utterances[0].word_indexes == [0, 1]
        assert transcript.speakers_detected == ["speaker_0", "speaker_1"]
        assert transcript.duration_seconds == 1.6
        assert transcript.language == "en"

    def test_diarization_off_by_default(self, configured, monkeypatch):
        _, captured = self._run(monkeypatch, [FakeResponse(ELEVENLABS_BODY)])
        assert captured[0]["data"]["diarize"] == "false"
        assert "language_code" not in captured[0]["data"]

    def test_429_is_retryable(self, configured, monkeypatch):
        with pytest.raises(RetryableTranscriptionError, match="rate-limited"):
            self._run(monkeypatch, [FakeResponse(status_code=429, text="429")])

    def test_5xx_is_retryable(self, configured, monkeypatch):
        with pytest.raises(RetryableTranscriptionError, match="502"):
            self._run(monkeypatch, [FakeResponse(status_code=502, text="bad gw")])

    def test_4xx_is_fatal(self, configured, monkeypatch):
        with pytest.raises(TranscriptionError, match="422") as e:
            self._run(monkeypatch, [FakeResponse(status_code=422, text="bad file")])
        assert not isinstance(e.value, RetryableTranscriptionError)

    def test_timeout_is_retryable(self, configured, monkeypatch):
        with pytest.raises(RetryableTranscriptionError, match="timed out"):
            self._run(monkeypatch, [requests.Timeout("slow")])

    def test_transport_error_is_retryable(self, configured, monkeypatch):
        with pytest.raises(RetryableTranscriptionError, match="transport"):
            self._run(monkeypatch, [requests.ConnectionError("refused")])

    def test_non_json_is_retryable(self, configured, monkeypatch):
        with pytest.raises(RetryableTranscriptionError, match="non-JSON"):
            self._run(monkeypatch, [FakeResponse(payload=None, text="oops")])

    def test_empty_word_tokens_are_skipped(self, configured, monkeypatch):
        body = {
            "language_code": "en",
            "words": [
                {"text": "", "start": 0.0, "end": 0.1, "type": "word"},
                {"text": "ok", "start": 0.2, "end": 0.5, "type": "word"},
            ],
        }
        transcript, _ = self._run(monkeypatch, [FakeResponse(body)])
        assert [w.text for w in transcript.words] == ["ok"]
        assert transcript.speakers_detected == []
        assert transcript.utterances[0].speaker is None


ASSEMBLY_DONE = {
    "status": "completed",
    "language_code": "en_us",
    "audio_duration": 3,
    "words": [
        {"text": "Hi", "start": 0, "end": 400, "confidence": 0.9, "speaker": "A"},
        {"text": "all", "start": 500, "end": 900, "confidence": 0.8, "speaker": "A"},
        {"text": "Hey", "start": 1000, "end": 1500, "confidence": 0.95, "speaker": "B"},
    ],
    "utterances": [
        {"text": "Hi all", "start": 0, "end": 900, "speaker": "A", "confidence": 0.85},
        {"text": "Hey", "start": 1000, "end": 1500, "speaker": "B", "confidence": 0.95},
    ],
}


class TestAssemblyAI:
    @pytest.fixture
    def configured(self, settings, monkeypatch):
        settings.STAPEL_AGENT = {"ASSEMBLYAI_API_KEY": "aai-test"}
        # Poll loop sleeps before every GET — neutralize it for tests.
        monkeypatch.setattr("time.sleep", lambda s: None)
        return settings

    def _mock_get(self, monkeypatch, responses, captured):
        queue = list(responses)

        def fake_get(url, headers=None, timeout=None):
            captured.append({"url": url, "headers": headers, "timeout": timeout})
            step = queue.pop(0)
            if isinstance(step, Exception):
                raise step
            return step

        monkeypatch.setattr(
            "stapel_agent.stt.providers.assemblyai.requests.get", fake_get
        )

    def _run(self, monkeypatch, *, submit, polls=(), audio=None, **kwargs):
        posted, polled = [], []
        mock_post(monkeypatch, "assemblyai", submit, posted)
        self._mock_get(monkeypatch, list(polls), polled)
        transcript = AssemblyAIProvider().transcribe(
            audio=audio or AudioRef(url="https://cdn.test/a.mp3"), **kwargs
        )
        return transcript, posted, polled

    def test_missing_key_is_fatal(self, settings):
        settings.STAPEL_AGENT = {"ASSEMBLYAI_API_KEY": ""}
        with pytest.raises(TranscriptionError, match="ASSEMBLYAI_API_KEY"):
            AssemblyAIProvider().transcribe(audio=AudioRef(url="https://x/a"))

    def test_bytes_ref_is_rejected(self, configured):
        with pytest.raises(TranscriptionError, match="requires an audio URL"):
            AssemblyAIProvider().transcribe(audio=AudioRef(data=b"x"))

    def test_submit_poll_happy_path(self, configured, monkeypatch):
        transcript, posted, polled = self._run(
            monkeypatch,
            submit=[FakeResponse({"id": "tr_1", "status": "queued"})],
            polls=[
                FakeResponse({"status": "processing"}),
                FakeResponse(ASSEMBLY_DONE),
            ],
            language="en-US",
            diarization=True,
        )
        assert posted[0]["url"] == "https://api.assemblyai.com/v2/transcript"
        assert posted[0]["headers"]["Authorization"] == "aai-test"
        body = posted[0]["json"]
        assert body["audio_url"] == "https://cdn.test/a.mp3"
        assert body["speech_model"] == "universal"
        assert body["speaker_labels"] is True
        assert body["language_code"] == "en_us"  # separators normalized
        assert "language_detection" not in body
        assert [p["url"] for p in polled] == [
            "https://api.assemblyai.com/v2/transcript/tr_1",
        ] * 2
        # ms → s conversion + duration passthrough
        assert transcript.words[0].end == 0.4
        assert transcript.utterances[1].start == 1.0
        assert transcript.duration_seconds == 3.0
        assert transcript.speakers_detected == ["A", "B"]
        assert transcript.language == "en_us"
        assert transcript.text == "Hi all\nHey"

    def test_no_language_turns_on_detection(self, configured, monkeypatch):
        _, posted, _ = self._run(
            monkeypatch,
            submit=[FakeResponse({"id": "tr_1"})],
            polls=[FakeResponse(ASSEMBLY_DONE)],
        )
        assert posted[0]["json"]["language_detection"] is True
        assert "language_code" not in posted[0]["json"]

    def test_submit_429_is_retryable(self, configured, monkeypatch):
        with pytest.raises(RetryableTranscriptionError, match="rate-limited"):
            self._run(monkeypatch, submit=[FakeResponse(status_code=429, text="429")])

    def test_submit_5xx_is_retryable(self, configured, monkeypatch):
        with pytest.raises(RetryableTranscriptionError, match="500"):
            self._run(monkeypatch, submit=[FakeResponse(status_code=500, text="boom")])

    def test_submit_4xx_is_fatal(self, configured, monkeypatch):
        with pytest.raises(TranscriptionError, match="401") as e:
            self._run(monkeypatch, submit=[FakeResponse(status_code=401, text="key?")])
        assert not isinstance(e.value, RetryableTranscriptionError)

    def test_submit_timeout_is_retryable(self, configured, monkeypatch):
        with pytest.raises(RetryableTranscriptionError, match="timed out"):
            self._run(monkeypatch, submit=[requests.Timeout("slow")])

    def test_submit_transport_error_is_retryable(self, configured, monkeypatch):
        with pytest.raises(RetryableTranscriptionError, match="transport"):
            self._run(monkeypatch, submit=[requests.ConnectionError("refused")])

    def test_submit_non_json_is_retryable(self, configured, monkeypatch):
        with pytest.raises(RetryableTranscriptionError, match="non-JSON"):
            self._run(
                monkeypatch, submit=[FakeResponse(payload=None, text="<html>")]
            )

    def test_submit_without_id_is_retryable(self, configured, monkeypatch):
        with pytest.raises(RetryableTranscriptionError, match="lacked id"):
            self._run(monkeypatch, submit=[FakeResponse({"status": "queued"})])

    def test_poll_5xx_then_completed_keeps_polling(self, configured, monkeypatch):
        transcript, _, polled = self._run(
            monkeypatch,
            submit=[FakeResponse({"id": "tr_1"})],
            polls=[
                FakeResponse(status_code=503, text="blip"),
                requests.ConnectionError("hiccup"),
                FakeResponse(payload=None, text="not json"),
                FakeResponse(ASSEMBLY_DONE),
            ],
        )
        assert len(polled) == 4
        assert transcript.text == "Hi all\nHey"

    def test_normalization_edge_cases(self, configured, monkeypatch):
        body = {
            "status": "completed",
            "audio_duration": "not-a-number",
            "words": [
                {"text": "", "start": 0, "end": 100},  # skipped
                {"text": "Hi", "start": 0, "end": 400},
            ],
            "utterances": [
                {"text": "", "start": 0, "end": 100},  # skipped
                {"text": "Hi", "start": 0, "end": 400, "speaker": "C"},
            ],
        }
        transcript, _, _ = self._run(
            monkeypatch,
            submit=[FakeResponse({"id": "tr_1"})],
            polls=[FakeResponse(body)],
        )
        assert [w.text for w in transcript.words] == ["Hi"]
        assert [u.text for u in transcript.utterances] == ["Hi"]
        # speaker seen only on the utterance still lands in the roster
        assert transcript.speakers_detected == ["C"]
        # bad audio_duration → fall back to the last word end (ms → s)
        assert transcript.duration_seconds == 0.4

    def test_poll_job_error_is_fatal(self, configured, monkeypatch):
        with pytest.raises(TranscriptionError, match="job error: bad codec") as e:
            self._run(
                monkeypatch,
                submit=[FakeResponse({"id": "tr_1"})],
                polls=[FakeResponse({"status": "error", "error": "bad codec"})],
            )
        assert not isinstance(e.value, RetryableTranscriptionError)

    def test_poll_deadline_is_retryable(self, configured, monkeypatch):
        # Fake clock: every look at time.monotonic() jumps 100s forward, so
        # a 60s budget is over before the first poll GET goes out.
        import itertools

        ticks = itertools.count(step=100.0)
        monkeypatch.setattr("time.monotonic", lambda: next(ticks))
        with pytest.raises(RetryableTranscriptionError, match="polling exceeded"):
            self._run(
                monkeypatch,
                submit=[FakeResponse({"id": "tr_1"})],
                polls=[],
                timeout_seconds=60,
            )
