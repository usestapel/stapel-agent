"""Tests for the STT adapters ported from the iron-benchmark quads —
deepgram / gladia / soniox / speechmatics / xai-stt. Mocked ``requests``;
no network, no keys.

Error taxonomy under test everywhere: 429/5xx/timeouts/transport →
``RetryableTranscriptionError``, other 4xx / bad input / missing config →
fatal ``TranscriptionError``. Keyterm wiring is asserted at the request
level (the exact provider parameter) and at the metadata level (the
``biasing`` block carries counts only).
"""
import json

import pytest
import requests

from stapel_agent.stt.base import (
    AudioRef,
    RetryableTranscriptionError,
    TranscriptionError,
)
from stapel_agent.stt.providers.deepgram import DeepgramProvider
from stapel_agent.stt.providers.gladia import GladiaProvider
from stapel_agent.stt.providers.soniox import SonioxProvider
from stapel_agent.stt.providers.speechmatics import SpeechmaticsProvider
from stapel_agent.stt.providers.xai_stt import XaiSttProvider


class FakeResponse:
    def __init__(self, payload=None, status_code=200, text=None):
        self._payload = payload
        self.status_code = status_code
        self.text = text if text is not None else json.dumps(payload or {})

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def _consume(queue, captured, **call):
    captured.append(call)
    step = queue.pop(0)
    if isinstance(step, Exception):
        raise step
    return step


def mock_http(monkeypatch, module, *, post=(), get=(), request=(), delete=()):
    """Patch requests.{post,get,request,delete} in *module*; each queue is
    consumed one per call. Returns the captured-call lists."""
    posted, gotten, requested, deleted = [], [], [], []
    post_q, get_q, req_q, del_q = list(post), list(get), list(request), list(delete)
    base = f"stapel_agent.stt.providers.{module}.requests"

    monkeypatch.setattr(
        f"{base}.post",
        lambda url, **kw: _consume(post_q, posted, url=url, **kw),
    )
    monkeypatch.setattr(
        f"{base}.get",
        lambda url, **kw: _consume(get_q, gotten, url=url, **kw),
    )
    monkeypatch.setattr(
        f"{base}.request",
        lambda method, url, **kw: _consume(
            req_q, requested, method=method, url=url, **kw
        ),
    )
    monkeypatch.setattr(
        f"{base}.delete",
        lambda url, **kw: _consume(del_q, deleted, url=url, **kw),
    )
    return posted, gotten, requested, deleted


# ─── Deepgram ──────────────────────────────────────────────────────────


DEEPGRAM_BODY = {
    "metadata": {"duration": 2.5},
    "results": {
        "channels": [
            {
                "detected_language": "en",
                "alternatives": [
                    {
                        "transcript": "Hi there. Hello.",
                        "words": [
                            {"word": "hi", "punctuated_word": "Hi", "start": 0.0, "end": 0.4, "confidence": 0.99, "speaker": 0},
                            {"word": "there", "punctuated_word": "there.", "start": 0.5, "end": 0.9, "confidence": 0.98, "speaker": 0},
                            {"word": "hello", "punctuated_word": "Hello.", "start": 1.0, "end": 1.6, "confidence": 0.97, "speaker": 1},
                        ],
                    }
                ],
            }
        ],
        "utterances": [
            {"transcript": "Hi there.", "start": 0.0, "end": 0.9, "speaker": 0, "confidence": 0.99},
            {"transcript": "Hello.", "start": 1.0, "end": 1.6, "speaker": 1, "confidence": 0.97},
        ],
    },
}


class TestDeepgram:
    @pytest.fixture
    def configured(self, settings):
        settings.STAPEL_AGENT = {"DEEPGRAM_API_KEY": "dg-test"}
        return settings

    def _run(self, monkeypatch, responses, *, audio=None, **kwargs):
        posted, _, _, _ = mock_http(monkeypatch, "deepgram", post=responses)
        transcript = DeepgramProvider().transcribe(
            audio=audio or AudioRef(data=b"RIFFbytes", mime="audio/wav"), **kwargs
        )
        return transcript, posted

    def test_missing_key_is_fatal(self, settings):
        settings.STAPEL_AGENT = {"DEEPGRAM_API_KEY": ""}
        with pytest.raises(TranscriptionError, match="DEEPGRAM_API_KEY"):
            DeepgramProvider().transcribe(audio=AudioRef(data=b"x"))

    def test_happy_path_raw_bytes_and_normalization(self, configured, monkeypatch):
        transcript, posted = self._run(
            monkeypatch, [FakeResponse(DEEPGRAM_BODY)], diarization=True, language="en"
        )
        call = posted[0]
        assert call["url"] == "https://api.deepgram.com/v1/listen"
        # Auth scheme is Token (NOT Bearer); body is raw bytes, not multipart.
        assert call["headers"]["Authorization"] == "Token dg-test"
        assert call["headers"]["Content-Type"] == "audio/wav"
        assert call["data"] == b"RIFFbytes"
        params = call["params"]
        assert params["model"] == "nova-3"
        assert params["smart_format"] == "true"
        assert params["utterances"] == "true"
        assert params["language"] == "en"
        # diarize_model both enables diarization and picks the version;
        # the deprecated boolean `diarize` must never be sent.
        assert params["diarize_model"] == "latest"
        assert "diarize" not in params
        # punctuated words preferred; int speakers → speaker_{n} labels
        assert [w.text for w in transcript.words] == ["Hi", "there.", "Hello."]
        assert transcript.words[0].speaker == "speaker_0"
        assert [(u.speaker, u.text) for u in transcript.utterances] == [
            ("speaker_0", "Hi there."),
            ("speaker_1", "Hello."),
        ]
        assert transcript.speakers_detected == ["speaker_0", "speaker_1"]
        assert transcript.duration_seconds == 2.5
        assert transcript.language == "en"
        assert transcript.biasing is None  # no keyterms requested

    def test_no_diarization_omits_diarize_model(self, configured, monkeypatch):
        _, posted = self._run(monkeypatch, [FakeResponse(DEEPGRAM_BODY)])
        assert "diarize_model" not in posted[0]["params"]
        assert "diarize" not in posted[0]["params"]

    def test_keyterms_repeated_param_and_biasing(self, configured, monkeypatch):
        transcript, posted = self._run(
            monkeypatch,
            [FakeResponse(DEEPGRAM_BODY)],
            keyterms=["IronMemo", "SSH", "iron memo"],
        )
        # Plain list value → the repeated query param; never comma-joined.
        assert posted[0]["params"]["keyterm"] == ["IronMemo", "SSH", "iron memo"]
        assert transcript.biasing == {
            "applied": True,
            "terms_sent": 3,
            "terms_truncated": 0,
        }

    def test_keyterm_legacy_intensifier_and_duplicates_truncated(
        self, configured, monkeypatch
    ):
        transcript, posted = self._run(
            monkeypatch,
            [FakeResponse(DEEPGRAM_BODY)],
            # `term:2` is the nova-2 keywords syntax — biasing a literal
            # ":2" on nova-3; duplicates differ only by case.
            keyterms=["SSH", "boost:2", "ssh"],
        )
        assert posted[0]["params"]["keyterm"] == ["SSH"]
        assert transcript.biasing == {
            "applied": True,
            "terms_sent": 1,
            "terms_truncated": 2,
        }

    def test_keyterm_token_budget_truncates_not_errors(self, configured, monkeypatch):
        huge = "x " * 501  # ~501 estimated tokens — over the 500 cap alone
        transcript, posted = self._run(
            monkeypatch, [FakeResponse(DEEPGRAM_BODY)], keyterms=[huge, "ok"]
        )
        # The over-budget term is dropped; a later smaller term still fits.
        assert posted[0]["params"]["keyterm"] == ["ok"]
        assert transcript.biasing == {
            "applied": True,
            "terms_sent": 1,
            "terms_truncated": 1,
        }

    def test_all_keyterms_truncated_reports_not_applied(self, configured, monkeypatch):
        transcript, posted = self._run(
            monkeypatch, [FakeResponse(DEEPGRAM_BODY)], keyterms=["w:2"]
        )
        assert "keyterm" not in posted[0]["params"]
        assert transcript.biasing == {
            "applied": False,
            "terms_sent": 0,
            "terms_truncated": 1,
        }

    def test_provider_options_win_over_adapter_params(self, configured, monkeypatch):
        _, posted = self._run(
            monkeypatch,
            [FakeResponse(DEEPGRAM_BODY)],
            provider_options={"model": "nova-2-general", "filler_words": "true"},
        )
        # Applied AFTER the adapter's own params: the caller's pin wins,
        # and unknown keys pass through untouched.
        assert posted[0]["params"]["model"] == "nova-2-general"
        assert posted[0]["params"]["filler_words"] == "true"

    def test_429_is_retryable(self, configured, monkeypatch):
        with pytest.raises(RetryableTranscriptionError, match="rate-limited"):
            self._run(monkeypatch, [FakeResponse(status_code=429, text="429")])

    def test_5xx_is_retryable(self, configured, monkeypatch):
        with pytest.raises(RetryableTranscriptionError, match="503"):
            self._run(monkeypatch, [FakeResponse(status_code=503, text="down")])

    def test_4xx_is_fatal(self, configured, monkeypatch):
        with pytest.raises(TranscriptionError, match="401") as e:
            self._run(monkeypatch, [FakeResponse(status_code=401, text="key?")])
        assert not isinstance(e.value, RetryableTranscriptionError)

    def test_timeout_is_retryable(self, configured, monkeypatch):
        with pytest.raises(RetryableTranscriptionError, match="timed out"):
            self._run(monkeypatch, [requests.Timeout("slow")])

    def test_non_json_is_retryable(self, configured, monkeypatch):
        with pytest.raises(RetryableTranscriptionError, match="non-JSON"):
            self._run(monkeypatch, [FakeResponse(payload=None, text="<html>")])


# ─── Gladia ────────────────────────────────────────────────────────────


GLADIA_DONE = {
    "status": "done",
    "file": {"audio_duration": 3.1},
    "result": {
        "metadata": {"audio_duration": 3.0},
        "transcription": {
            "full_transcript": "Hi all. Hey.",
            "languages": ["en"],
            "utterances": [
                {
                    "text": "Hi all.",
                    "start": 0.0,
                    "end": 0.9,
                    "speaker": 0,
                    "confidence": 0.9,
                    "words": [
                        {"word": " Hi", "start": 0.0, "end": 0.4, "confidence": 0.9},
                        {"word": "all.", "start": 0.5, "end": 0.9, "confidence": 0.9},
                    ],
                },
                {
                    "text": "Hey.",
                    "start": 1.0,
                    "end": 1.5,
                    "speaker": 1,
                    "confidence": 0.95,
                    "words": [
                        {"word": "Hey.", "start": 1.0, "end": 1.5, "confidence": 0.95}
                    ],
                },
            ],
        },
    },
}


class TestGladia:
    @pytest.fixture
    def configured(self, settings, monkeypatch):
        settings.STAPEL_AGENT = {"GLADIA_API_KEY": "gl-test"}
        monkeypatch.setattr("time.sleep", lambda s: None)
        return settings

    def _run(self, monkeypatch, *, post, get=(), audio=None, **kwargs):
        posted, gotten, _, _ = mock_http(monkeypatch, "gladia", post=post, get=get)
        transcript = GladiaProvider().transcribe(
            audio=audio or AudioRef(data=b"OGG", mime="audio/ogg"), **kwargs
        )
        return transcript, posted, gotten

    def test_missing_key_is_fatal(self, settings):
        settings.STAPEL_AGENT = {"GLADIA_API_KEY": ""}
        with pytest.raises(TranscriptionError, match="GLADIA_API_KEY"):
            GladiaProvider().transcribe(audio=AudioRef(data=b"x"))

    def test_upload_create_poll_happy_path(self, configured, monkeypatch):
        transcript, posted, gotten = self._run(
            monkeypatch,
            post=[
                FakeResponse({"audio_url": "https://gladia.cdn/a"}),
                FakeResponse({"id": "job_1"}),
            ],
            get=[
                FakeResponse({"status": "processing"}),
                FakeResponse(GLADIA_DONE),
            ],
            language="en-US",
            diarization=True,
        )
        upload, create = posted
        assert upload["url"] == "https://api.gladia.io/v2/upload"
        assert upload["headers"] == {"x-gladia-key": "gl-test"}
        assert upload["files"]["audio"] == ("audio", b"OGG", "audio/ogg")
        assert create["url"] == "https://api.gladia.io/v2/pre-recorded"
        body = create["json"]
        assert body["audio_url"] == "https://gladia.cdn/a"
        assert body["model"] == "solaria-1"  # always explicit, never omitted
        assert body["diarization"] is True
        assert body["language_config"] == {"languages": ["en"]}
        assert [g["url"] for g in gotten] == [
            "https://api.gladia.io/v2/pre-recorded/job_1"
        ] * 2
        # words inherit the utterance's integer speaker as speaker_{n}
        assert [w.text for w in transcript.words] == ["Hi", "all.", "Hey."]
        assert transcript.words[0].speaker == "speaker_0"
        assert [(u.speaker, u.text) for u in transcript.utterances] == [
            ("speaker_0", "Hi all."),
            ("speaker_1", "Hey."),
        ]
        assert transcript.utterances[0].word_indexes == [0, 1]
        assert transcript.duration_seconds == 3.0  # result.metadata wins
        assert transcript.language == "en"
        assert transcript.biasing is None

    def test_keyterms_report_not_applied(self, configured, monkeypatch):
        # No Gladia biasing param is covered by the pinned sources —
        # requested terms are reported, never silently dropped or fatal.
        transcript, posted, _ = self._run(
            monkeypatch,
            post=[
                FakeResponse({"audio_url": "https://gladia.cdn/a"}),
                FakeResponse({"id": "job_1"}),
            ],
            get=[FakeResponse(GLADIA_DONE)],
            keyterms=["IronMemo", "SSH"],
        )
        assert transcript.biasing == {
            "applied": False,
            "terms_sent": 0,
            "terms_truncated": 2,
        }
        # ...and the terms are NOT smuggled into the request.
        assert "IronMemo" not in json.dumps(posted[1]["json"])

    def test_provider_options_merge_into_create_body(self, configured, monkeypatch):
        _, posted, _ = self._run(
            monkeypatch,
            post=[
                FakeResponse({"audio_url": "https://gladia.cdn/a"}),
                FakeResponse({"id": "job_1"}),
            ],
            get=[FakeResponse(GLADIA_DONE)],
            provider_options={"custom_vocabulary_config": {"vocabulary": ["x"]}},
        )
        assert posted[1]["json"]["custom_vocabulary_config"] == {"vocabulary": ["x"]}

    def test_upload_429_is_retryable(self, configured, monkeypatch):
        with pytest.raises(RetryableTranscriptionError, match="rate-limited"):
            self._run(monkeypatch, post=[FakeResponse(status_code=429, text="429")])

    def test_create_4xx_is_fatal(self, configured, monkeypatch):
        with pytest.raises(TranscriptionError, match="401") as e:
            self._run(
                monkeypatch,
                post=[
                    FakeResponse({"audio_url": "https://gladia.cdn/a"}),
                    FakeResponse(status_code=401, text="bad key"),
                ],
            )
        assert not isinstance(e.value, RetryableTranscriptionError)

    def test_job_error_status_is_fatal(self, configured, monkeypatch):
        with pytest.raises(TranscriptionError, match="job error"):
            self._run(
                monkeypatch,
                post=[
                    FakeResponse({"audio_url": "https://gladia.cdn/a"}),
                    FakeResponse({"id": "job_1"}),
                ],
                get=[FakeResponse({"status": "error", "error_code": 400})],
            )

    def test_poll_deadline_is_retryable(self, configured, monkeypatch):
        import itertools

        ticks = itertools.count(step=100.0)
        monkeypatch.setattr("time.monotonic", lambda: next(ticks))
        with pytest.raises(RetryableTranscriptionError, match="polling exceeded"):
            self._run(
                monkeypatch,
                post=[
                    FakeResponse({"audio_url": "https://gladia.cdn/a"}),
                    FakeResponse({"id": "job_1"}),
                ],
                get=[],
                timeout_seconds=60,
            )

    def test_transport_error_is_retryable(self, configured, monkeypatch):
        with pytest.raises(RetryableTranscriptionError, match="transport"):
            self._run(monkeypatch, post=[requests.ConnectionError("refused")])


# ─── Soniox ────────────────────────────────────────────────────────────


SONIOX_TRANSCRIPT = {
    "id": "tr_1",
    "text": "Beautiful day? Hi",
    "tokens": [
        {"text": "Beau", "start_ms": 0, "end_ms": 100, "confidence": 0.9, "speaker": "1"},
        {"text": "ti", "start_ms": 100, "end_ms": 200, "confidence": 0.8, "speaker": "1"},
        {"text": "ful", "start_ms": 200, "end_ms": 300, "confidence": 1.0, "speaker": "1"},
        {"text": " day", "start_ms": 300, "end_ms": 500, "confidence": 0.9, "speaker": "1"},
        {"text": "?", "start_ms": 500, "end_ms": 550, "confidence": 0.9, "speaker": "1"},
        {"text": " Hi", "start_ms": 600, "end_ms": 800, "confidence": 0.95, "speaker": "2"},
    ],
}


class TestSoniox:
    @pytest.fixture
    def configured(self, settings, monkeypatch):
        settings.STAPEL_AGENT = {"SONIOX_API_KEY": "sx-test"}
        monkeypatch.setattr("time.sleep", lambda s: None)
        return settings

    def _run(self, monkeypatch, *, request, get=(), delete=None, audio=None, **kwargs):
        if delete is None:
            delete = [FakeResponse({}), FakeResponse({})]
        _, gotten, requested, deleted = mock_http(
            monkeypatch, "soniox", request=request, get=get, delete=delete
        )
        transcript = SonioxProvider().transcribe(
            audio=audio or AudioRef(data=b"WAV", mime="audio/wav"), **kwargs
        )
        return transcript, requested, gotten, deleted

    def test_missing_key_is_fatal(self, settings):
        settings.STAPEL_AGENT = {"SONIOX_API_KEY": ""}
        with pytest.raises(TranscriptionError, match="SONIOX_API_KEY"):
            SonioxProvider().transcribe(audio=AudioRef(data=b"x"))

    def test_five_step_happy_path_with_cleanup(self, configured, monkeypatch):
        transcript, requested, gotten, deleted = self._run(
            monkeypatch,
            request=[
                FakeResponse({"id": "file_1"}),
                FakeResponse({"id": "tr_1", "status": "queued"}),
                FakeResponse(SONIOX_TRANSCRIPT),
            ],
            get=[
                FakeResponse({"status": "processing"}),
                FakeResponse({"status": "completed", "audio_duration_ms": 900}),
            ],
            language="en-US",
            diarization=True,
        )
        upload, create, fetch = requested
        assert upload["method"] == "POST"
        assert upload["url"] == "https://api.soniox.com/v1/files"
        assert upload["headers"]["Authorization"] == "Bearer sx-test"
        assert upload["files"]["file"] == ("audio", b"WAV", "audio/wav")
        assert create["url"] == "https://api.soniox.com/v1/transcriptions"
        body = create["json"]
        assert body["model"] == "stt-async-v5"  # explicit, never omitted
        assert body["file_id"] == "file_1"
        assert body["enable_speaker_diarization"] is True
        assert body["language_hints"] == ["en"]
        assert fetch["method"] == "GET"
        assert fetch["url"].endswith("/v1/transcriptions/tr_1/transcript")
        # sub-word tokens merged into words; string speakers labelled
        assert [w.text for w in transcript.words] == ["Beautiful", "day?", "Hi"]
        assert transcript.words[0].speaker == "speaker_1"
        assert transcript.words[0].start == 0.0
        assert transcript.words[0].end == 0.3
        assert [(u.speaker, u.text) for u in transcript.utterances] == [
            ("speaker_1", "Beautiful day?"),
            ("speaker_2", "Hi"),
        ]
        assert transcript.duration_seconds == 0.9  # job audio_duration_ms
        # cleanup: transcription deleted on success + file deleted always
        assert [d["url"] for d in deleted] == [
            "https://api.soniox.com/v1/transcriptions/tr_1",
            "https://api.soniox.com/v1/files/file_1",
        ]
        assert transcript.biasing is None

    def test_no_language_omits_hints(self, configured, monkeypatch):
        _, requested, _, _ = self._run(
            monkeypatch,
            request=[
                FakeResponse({"id": "file_1"}),
                FakeResponse({"id": "tr_1"}),
                FakeResponse(SONIOX_TRANSCRIPT),
            ],
            get=[FakeResponse({"status": "completed"})],
        )
        assert "language_hints" not in requested[1]["json"]

    def test_keyterms_report_not_applied(self, configured, monkeypatch):
        transcript, _, _, _ = self._run(
            monkeypatch,
            request=[
                FakeResponse({"id": "file_1"}),
                FakeResponse({"id": "tr_1"}),
                FakeResponse(SONIOX_TRANSCRIPT),
            ],
            get=[FakeResponse({"status": "completed"})],
            keyterms=["IronMemo"],
        )
        assert transcript.biasing == {
            "applied": False,
            "terms_sent": 0,
            "terms_truncated": 1,
        }

    def test_provider_options_merge_into_create_body(self, configured, monkeypatch):
        _, requested, _, _ = self._run(
            monkeypatch,
            request=[
                FakeResponse({"id": "file_1"}),
                FakeResponse({"id": "tr_1"}),
                FakeResponse(SONIOX_TRANSCRIPT),
            ],
            get=[FakeResponse({"status": "completed"})],
            provider_options={"context": {"terms": ["IronMemo"]}},
        )
        assert requested[1]["json"]["context"] == {"terms": ["IronMemo"]}

    def test_failed_job_deletes_file_not_transcription(self, configured, monkeypatch):
        _, _, _, deleted = mock_http(
            monkeypatch,
            "soniox",
            request=[FakeResponse({"id": "file_1"}), FakeResponse({"id": "tr_1"})],
            get=[FakeResponse({"status": "error", "error_type": "boom"})],
            delete=[FakeResponse({})],
        )
        with pytest.raises(TranscriptionError, match="job failed"):
            SonioxProvider().transcribe(audio=AudioRef(data=b"WAV"))
        # Only the FILE is cleaned up — the failed transcription object is
        # kept for forensics (error fields live on it).
        assert [d["url"] for d in deleted] == [
            "https://api.soniox.com/v1/files/file_1"
        ]

    def test_upload_5xx_is_retryable(self, configured, monkeypatch):
        with pytest.raises(RetryableTranscriptionError, match="500"):
            self._run(
                monkeypatch,
                request=[FakeResponse(status_code=500, text="boom")],
                delete=[],
            )

    def test_create_4xx_is_fatal(self, configured, monkeypatch):
        with pytest.raises(TranscriptionError, match="422") as e:
            self._run(
                monkeypatch,
                request=[
                    FakeResponse({"id": "file_1"}),
                    FakeResponse(status_code=422, text="bad model"),
                ],
                delete=[FakeResponse({})],
            )
        assert not isinstance(e.value, RetryableTranscriptionError)


# ─── Speechmatics ──────────────────────────────────────────────────────


SM_TRANSCRIPT = {
    "job": {"duration": 3},
    "metadata": {"transcription_config": {"language": "multi", "model": "melia-1"}},
    "results": [
        {"type": "word", "start_time": 0.0, "end_time": 0.4,
         "alternatives": [{"content": "Hi", "confidence": 0.9, "speaker": "S1"}]},
        {"type": "word", "start_time": 0.5, "end_time": 0.9,
         "alternatives": [{"content": "all", "confidence": 0.9, "speaker": "S1"}]},
        {"type": "punctuation", "end_time": 0.9, "attaches_to": "previous",
         "is_eos": True, "alternatives": [{"content": "."}]},
        {"type": "word", "start_time": 1.0, "end_time": 1.5,
         "alternatives": [{"content": "Hey", "confidence": 0.95, "speaker": "S2"}]},
        {"type": "word", "start_time": 1.6, "end_time": 1.9,
         "alternatives": [{"content": "you", "speaker": "UU"}]},
    ],
}


class TestSpeechmatics:
    @pytest.fixture
    def configured(self, settings, monkeypatch):
        settings.STAPEL_AGENT = {"SPEECHMATICS_API_KEY": "sm-test"}
        monkeypatch.setattr("time.sleep", lambda s: None)
        return settings

    def _run(self, monkeypatch, *, post, get=(), audio=None, **kwargs):
        posted, gotten, _, _ = mock_http(
            monkeypatch, "speechmatics", post=post, get=get
        )
        transcript = SpeechmaticsProvider().transcribe(
            audio=audio or AudioRef(data=b"WAV", mime="audio/wav"), **kwargs
        )
        return transcript, posted, gotten

    def test_missing_key_is_fatal(self, settings):
        settings.STAPEL_AGENT = {"SPEECHMATICS_API_KEY": ""}
        with pytest.raises(TranscriptionError, match="SPEECHMATICS_API_KEY"):
            SpeechmaticsProvider().transcribe(audio=AudioRef(data=b"x"))

    def test_submit_poll_fetch_happy_path(self, configured, monkeypatch):
        transcript, posted, gotten = self._run(
            monkeypatch,
            post=[FakeResponse({"id": "sm_1"})],
            get=[
                FakeResponse({"job": {"status": "running"}}),
                FakeResponse({"job": {"status": "done"}}),
                FakeResponse(SM_TRANSCRIPT),
            ],
            language="en-GB",
            diarization=True,
        )
        submit = posted[0]
        assert submit["url"] == "https://eu1.asr.api.speechmatics.com/v2/jobs/"
        assert submit["headers"]["Authorization"] == "Bearer sm-test"
        assert submit["files"]["data_file"] == ("audio", b"WAV", "audio/wav")
        config = json.loads(submit["data"]["config"])
        assert config["type"] == "transcription"
        tc = config["transcription_config"]
        # melia-1: wire language is always "multi", the request becomes a hint
        assert tc["model"] == "melia-1"
        assert tc["language"] == "multi"
        assert tc["language_hints"] == ["en"]
        assert tc["diarization"] == "speaker"
        assert [g["url"] for g in gotten] == [
            "https://eu1.asr.api.speechmatics.com/v2/jobs/sm_1",
            "https://eu1.asr.api.speechmatics.com/v2/jobs/sm_1",
            "https://eu1.asr.api.speechmatics.com/v2/jobs/sm_1/transcript",
        ]
        assert [w.text for w in transcript.words] == ["Hi", "all", "Hey", "you"]
        # punctuation glued into the utterance text, never a word cell;
        # is_eos closes the sentence; "UU" is not a speaker
        assert [(u.speaker, u.text) for u in transcript.utterances] == [
            ("S1", "Hi all."),
            ("S2", "Hey"),
            (None, "you"),
        ]
        assert transcript.speakers_detected == ["S1", "S2"]
        assert transcript.duration_seconds == 3.0
        assert transcript.language is None  # "multi" carries no single code
        assert transcript.biasing is None

    def test_standard_model_requires_language(self, configured, settings):
        settings.STAPEL_AGENT = {
            **settings.STAPEL_AGENT,
            "SPEECHMATICS_MODEL": "standard",
        }
        with pytest.raises(TranscriptionError, match="language pack"):
            SpeechmaticsProvider().transcribe(audio=AudioRef(data=b"x"))

    def test_standard_model_sends_language_pack(self, configured, settings, monkeypatch):
        settings.STAPEL_AGENT = {
            **settings.STAPEL_AGENT,
            "SPEECHMATICS_MODEL": "standard",
        }
        _, posted, _ = self._run(
            monkeypatch,
            post=[FakeResponse({"id": "sm_1"})],
            get=[
                FakeResponse({"job": {"status": "done"}}),
                FakeResponse(SM_TRANSCRIPT),
            ],
            language="ru",
        )
        tc = json.loads(posted[0]["data"]["config"])["transcription_config"]
        assert tc["language"] == "ru"
        assert "language_hints" not in tc

    def test_keyterms_report_not_applied(self, configured, monkeypatch):
        transcript, posted, _ = self._run(
            monkeypatch,
            post=[FakeResponse({"id": "sm_1"})],
            get=[
                FakeResponse({"job": {"status": "done"}}),
                FakeResponse(SM_TRANSCRIPT),
            ],
            keyterms=["IronMemo"],
        )
        assert transcript.biasing == {
            "applied": False,
            "terms_sent": 0,
            "terms_truncated": 1,
        }
        assert "IronMemo" not in posted[0]["data"]["config"]

    def test_provider_options_merge_into_transcription_config(
        self, configured, monkeypatch
    ):
        _, posted, _ = self._run(
            monkeypatch,
            post=[FakeResponse({"id": "sm_1"})],
            get=[
                FakeResponse({"job": {"status": "done"}}),
                FakeResponse(SM_TRANSCRIPT),
            ],
            provider_options={"additional_vocab": [{"content": "IronMemo"}]},
        )
        tc = json.loads(posted[0]["data"]["config"])["transcription_config"]
        assert tc["additional_vocab"] == [{"content": "IronMemo"}]

    def test_rejected_job_is_fatal(self, configured, monkeypatch):
        with pytest.raises(TranscriptionError, match="rejected") as e:
            self._run(
                monkeypatch,
                post=[FakeResponse({"id": "sm_1"})],
                get=[
                    FakeResponse(
                        {"job": {"status": "rejected",
                                 "errors": [{"message": "bad audio"}]}}
                    )
                ],
            )
        assert "bad audio" in str(e.value)
        assert not isinstance(e.value, RetryableTranscriptionError)

    def test_submit_429_is_retryable(self, configured, monkeypatch):
        with pytest.raises(RetryableTranscriptionError, match="rate-limited"):
            self._run(monkeypatch, post=[FakeResponse(status_code=429, text="429")])

    def test_submit_4xx_is_fatal(self, configured, monkeypatch):
        with pytest.raises(TranscriptionError, match="403") as e:
            self._run(monkeypatch, post=[FakeResponse(status_code=403, text="no")])
        assert not isinstance(e.value, RetryableTranscriptionError)


# ─── xAI STT ───────────────────────────────────────────────────────────


XAI_BODY = {
    "text": "Hi there",
    "language": "",
    "duration": 2.0,
    "words": [
        {"text": "Hi", "start": 0.0, "end": 0.4, "confidence": 0.07, "speaker": 0},
        {"text": "there", "start": 0.5, "end": 0.9, "speaker": 1},
    ],
}


class TestXaiStt:
    @pytest.fixture
    def configured(self, settings):
        settings.STAPEL_AGENT = {"XAI_API_KEY": "xai-test"}
        return settings

    def _run(self, monkeypatch, responses, *, audio=None, **kwargs):
        posted, _, _, _ = mock_http(monkeypatch, "xai_stt", post=responses)
        transcript = XaiSttProvider().transcribe(
            audio=audio or AudioRef(data=b"WAV", mime="audio/wav"), **kwargs
        )
        return transcript, posted

    def test_missing_key_is_fatal(self, settings):
        settings.STAPEL_AGENT = {"XAI_API_KEY": ""}
        with pytest.raises(TranscriptionError, match="XAI_API_KEY"):
            XaiSttProvider().transcribe(audio=AudioRef(data=b"x"))

    def test_happy_path_multipart_and_normalization(self, configured, monkeypatch):
        transcript, posted = self._run(
            monkeypatch,
            [FakeResponse(XAI_BODY)],
            language="ru-RU",
            diarization=True,
        )
        call = posted[0]
        assert call["url"] == "https://api.x.ai/v1/stt"
        assert call["headers"]["Authorization"] == "Bearer xai-test"
        assert call["files"]["file"] == ("audio", b"WAV", "audio/wav")
        data = call["data"]
        assert data["diarize"] == "true"
        assert data["language"] == "ru"
        # ru is in the 25-language formatting list → format goes along
        assert data["format"] == "true"
        assert [w.text for w in transcript.words] == ["Hi", "there"]
        assert transcript.words[0].speaker == "speaker_0"
        assert transcript.words[1].speaker == "speaker_1"
        # documented quirk: language "" (detection off) → None, never ""
        assert transcript.language is None
        assert transcript.duration_seconds == 2.0
        assert transcript.biasing is None

    def test_unformattable_language_sends_no_format(self, configured, monkeypatch):
        # uk is NOT in the documented formatting list — sending
        # format=true would be undefined; language alone still goes.
        _, posted = self._run(monkeypatch, [FakeResponse(XAI_BODY)], language="uk")
        assert posted[0]["data"]["language"] == "uk"
        assert "format" not in posted[0]["data"]

    def test_no_language_omits_the_format_pair(self, configured, monkeypatch):
        # format=true without language is a documented 400 — the pair is
        # sent together or not at all.
        _, posted = self._run(monkeypatch, [FakeResponse(XAI_BODY)])
        assert "language" not in posted[0]["data"]
        assert "format" not in posted[0]["data"]

    def test_keyterms_repeated_field_and_biasing(self, configured, monkeypatch):
        transcript, posted = self._run(
            monkeypatch, [FakeResponse(XAI_BODY)], keyterms=["IronMemo", "SSH"]
        )
        assert posted[0]["data"]["keyterm"] == ["IronMemo", "SSH"]
        assert transcript.biasing == {
            "applied": True,
            "terms_sent": 2,
            "terms_truncated": 0,
        }

    def test_keyterm_limits_truncate_not_error(self, configured, monkeypatch):
        long_term = "x" * 51  # over the 50-char cap
        many = [f"t{i}" for i in range(100)]
        transcript, posted = self._run(
            monkeypatch,
            [FakeResponse(XAI_BODY)],
            keyterms=[long_term, *many, "overflow"],  # 102 requested
        )
        # 100 accepted; the long term and the 101st are truncated.
        assert posted[0]["data"]["keyterm"] == many
        assert transcript.biasing == {
            "applied": True,
            "terms_sent": 100,
            "terms_truncated": 2,
        }

    def test_provider_options_win_over_adapter_fields(self, configured, monkeypatch):
        _, posted = self._run(
            monkeypatch,
            [FakeResponse(XAI_BODY)],
            diarization=True,
            provider_options={"diarize": "false", "filler_words": "true"},
        )
        assert posted[0]["data"]["diarize"] == "false"
        assert posted[0]["data"]["filler_words"] == "true"

    def test_text_only_body_synthesizes_one_utterance(self, configured, monkeypatch):
        transcript, _ = self._run(
            monkeypatch,
            [FakeResponse({"text": "just text", "language": "", "duration": 1.5})],
        )
        assert [u.text for u in transcript.utterances] == ["just text"]
        assert transcript.words == []

    def test_429_is_retryable(self, configured, monkeypatch):
        with pytest.raises(RetryableTranscriptionError, match="rate-limited"):
            self._run(monkeypatch, [FakeResponse(status_code=429, text="429")])

    def test_502_audio_fetch_is_retryable(self, configured, monkeypatch):
        with pytest.raises(RetryableTranscriptionError, match="502"):
            self._run(monkeypatch, [FakeResponse(status_code=502, text="bad fetch")])

    def test_413_too_large_is_fatal(self, configured, monkeypatch):
        with pytest.raises(TranscriptionError, match="413") as e:
            self._run(monkeypatch, [FakeResponse(status_code=413, text="too big")])
        assert not isinstance(e.value, RetryableTranscriptionError)

    def test_timeout_is_retryable(self, configured, monkeypatch):
        with pytest.raises(RetryableTranscriptionError, match="timed out"):
            self._run(monkeypatch, [requests.Timeout("slow")])
