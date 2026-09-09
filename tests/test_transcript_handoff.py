"""``llm.transcribe`` answers with a reference when the caller asks for one.

The defect, measured on the owner's stand (2026-09-09): a 2h28m meeting's
transcript is 8 647 617 bytes and the NATS broker carries 8 388 608. The
comm Function had reused the HTTP view's response shape verbatim — correct
over HTTP, a hard ceiling over a broker — so the reply was refused and the
recording was dropped. Twice, because the user uploaded it again.

The input side of the same call already travelled by reference (``audio_url``
is a presigned GET). These tests are about the output side getting the mirror
image, and about the two properties that make it a contract rather than a
size check: the caller chooses the shape, and a failed write is a failure.
"""
import json

import pytest
from django.test import override_settings

from stapel_agent import functions
from stapel_agent.handoff import HandoffError


TRANSCRIPT = {
    "provider": "elevenlabs",
    "language": "ru",
    "duration_seconds": 8898.005333,
    "words": [{"text": "w", "start": 0.0, "end": 0.3, "speaker": "speaker_0"}],
    "utterances": [{"text": "w", "start": 0.0, "end": 0.3, "speaker": "speaker_0",
                    "word_indexes": [0]}],
    "speakers_detected": ["speaker_0", "speaker_1"],
    "raw": {"anything": "large"},
    "biasing": {"applied": True, "terms_sent": 12, "terms_truncated": 0},
}
OK = {"status": "ok", "transcript": TRANSCRIPT,
      "provider_used": "elevenlabs", "fallback_used": False}


@pytest.fixture
def transcribed(monkeypatch):
    monkeypatch.setattr("stapel_agent.services.transcribe", lambda *a, **k: dict(OK))


class TestTheCallerChoosesTheShape:
    def test_no_put_url_keeps_the_inline_shape(self, transcribed):
        out = functions.llm_transcribe({"audio_url": "https://s3/a.opus"})

        assert out["transcript"] == TRANSCRIPT
        assert "transcript_ref" not in out

    def test_a_put_url_gets_a_reference_and_no_body(self, transcribed, monkeypatch):
        written = {}

        def _put(url, body, *, timeout=120):
            written["url"] = url
            written["body"] = body
            return {"bytes": 8_647_617, "sha256": "abc123"}

        monkeypatch.setattr("stapel_agent.handoff.put_json", _put)

        out = functions.llm_transcribe({
            "audio_url": "https://s3/a.opus",
            "transcript_put_url": "https://minio/put?sig=x",
            "transcript_key": "recordings/ws/rec/transcript.raw.json",
        })

        assert "transcript" not in out, "the bulk must not travel"
        assert out["status"] == "ok"
        assert out["transcript_ref"] == {
            "key": "recordings/ws/rec/transcript.raw.json",
            "content_type": "application/json",
            "bytes": 8_647_617,
            "sha256": "abc123",
        }
        assert written["body"] == TRANSCRIPT
        assert written["url"] == "https://minio/put?sig=x"

    def test_the_reference_reply_is_bounded(self, transcribed, monkeypatch):
        """Whatever the meeting's length, the reply fits a message."""
        monkeypatch.setattr(
            "stapel_agent.handoff.put_json",
            lambda *a, **k: {"bytes": 9_000_000, "sha256": "d"},
        )
        out = functions.llm_transcribe({
            "audio_url": "https://s3/a.opus",
            "transcript_put_url": "https://minio/put",
            "transcript_key": "k",
        })

        assert len(json.dumps(out).encode()) < 4096

    def test_the_meta_carries_the_counts_a_caller_needs(self, transcribed, monkeypatch):
        monkeypatch.setattr(
            "stapel_agent.handoff.put_json",
            lambda *a, **k: {"bytes": 10, "sha256": "d"},
        )
        out = functions.llm_transcribe({
            "audio_url": "https://s3/a.opus", "transcript_put_url": "https://m/p",
        })

        assert out["transcript_meta"] == {
            "provider": "elevenlabs",
            "language": "ru",
            "duration_seconds": 8898.005333,
            "words": 1,
            "utterances": 1,
            "speakers_detected": ["speaker_0", "speaker_1"],
            "biasing": {"applied": True, "terms_sent": 12, "terms_truncated": 0},
        }


class TestAFailedWriteIsAFailure:
    def test_it_never_falls_back_to_the_inline_body(self, transcribed, monkeypatch):
        """The fallback would fire exactly when the payload is too big."""
        def _boom(*a, **k):
            raise HandoffError("artifact PUT returned HTTP 403: expired")

        monkeypatch.setattr("stapel_agent.handoff.put_json", _boom)

        out = functions.llm_transcribe({
            "audio_url": "https://s3/a.opus", "transcript_put_url": "https://m/p",
        })

        assert out["status"] == "failure"
        assert "transcript_handoff_failed" in out["reason"]
        assert "403" in out["reason"]
        assert "transcript" not in out

    def test_a_transcription_failure_is_unchanged(self, monkeypatch):
        monkeypatch.setattr(
            "stapel_agent.services.transcribe",
            lambda *a, **k: {"status": "failure", "reason": "no provider"},
        )
        out = functions.llm_transcribe({
            "audio_url": "https://s3/a.opus", "transcript_put_url": "https://m/p",
        })

        assert out == {"status": "failure", "reason": "no provider"}


class TestTheWriter:
    def test_it_refuses_a_non_http_destination(self):
        from stapel_agent.handoff import put_json

        with pytest.raises(HandoffError) as exc:
            put_json("file:///etc/passwd", {"a": 1})
        assert "refusing" in str(exc.value)

    def test_a_non_2xx_is_an_error_carrying_the_body(self, monkeypatch):
        from stapel_agent import handoff

        class _Resp:
            status_code = 403
            text = "SignatureDoesNotMatch"

        monkeypatch.setattr("requests.put", lambda *a, **k: _Resp())
        with pytest.raises(HandoffError) as exc:
            handoff.put_json("https://minio/put", {"a": 1})
        assert "403" in str(exc.value)
        assert "SignatureDoesNotMatch" in str(exc.value)

    def test_the_digest_is_of_what_was_sent(self, monkeypatch):
        import hashlib

        from stapel_agent import handoff

        sent = {}

        class _Resp:
            status_code = 200
            text = ""

        def _put(url, data=None, headers=None, timeout=None):
            sent["data"] = data
            return _Resp()

        monkeypatch.setattr("requests.put", _put)
        ref = handoff.put_json("https://minio/put", {"a": 1})

        assert ref["bytes"] == len(sent["data"])
        assert ref["sha256"] == hashlib.sha256(sent["data"]).hexdigest()


def test_the_schema_accepts_the_two_new_fields():
    props = functions.TRANSCRIBE_SCHEMA["properties"]
    assert props["transcript_put_url"]["type"] == "string"
    assert props["transcript_key"]["type"] == "string"
    # additionalProperties stays false — which is why an older agent REJECTS
    # a payload carrying them, and why the deploy order is agent first.
    assert functions.TRANSCRIBE_SCHEMA["additionalProperties"] is False


@override_settings(STAPEL_AGENT={"STT_SEGMENTATION": {}})
def test_summary_of_an_empty_transcript_does_not_explode():
    assert functions.transcript_summary({})["words"] == 0
