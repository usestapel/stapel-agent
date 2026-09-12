"""The one classification table every STT adapter now shares.

``stt/failures.py`` decides fatal-vs-retryable per RESPONSE. The rule it
encodes: only the media itself is fatal, because only the media fails
identically on the next provider. Everything else — quota, billing, auth,
throttling, 5xx, an endpoint that is not there — is "this provider cannot
serve this request", and the router walks the chain.
"""
import json

import pytest

from stapel_agent.stt import failures
from stapel_agent.stt.base import RetryableTranscriptionError, TranscriptionError

ELEVENLABS_QUOTA_401 = json.dumps(
    {
        "detail": {
            "type": "invalid_request",
            "code": "quota_exceeded",
            "message": (
                "This request exceeds your quota of 23736. You have 18 "
                "credits remaining, while 1080 credits are required."
            ),
        }
    }
)


class TestClassifyStatus:
    @pytest.mark.parametrize(
        "status,body,reason",
        [
            # The production answer this release exists for.
            (401, ELEVENLABS_QUOTA_401, "quota"),
            # Same condition, the statuses other providers pick for it.
            (402, "", "quota"),
            (403, '{"error":"billing disabled for this project"}', "quota"),
            (429, '{"error":"monthly quota exceeded"}', "quota"),
            (400, "insufficient credits on your account", "quota"),
            # Auth: a plain bad key is "this provider", not "this audio".
            (401, '{"detail":"Invalid API key"}', "auth"),
            (401, "", "auth"),
            (403, "forbidden", "auth"),
            (407, "", "auth"),
            (422, "api key is malformed", "auth"),
            # Throttling keeps its own reason: it clears by itself.
            (429, "slow down", "rate"),
            (429, "", "rate"),
            # Provider-side breakage.
            (500, "boom", "server"),
            (503, "upstream down", "server"),
            # Endpoint/config, never the audio.
            (404, "no such route", "unavailable"),
            (405, "", "unavailable"),
            (409, "", "unavailable"),
            (451, "", "unavailable"),
            # The media itself — the ONLY fatal family.
            (400, "unsupported audio format", "media"),
            (413, "file too large", "media"),
            (415, "unsupported media type", "media"),
            (422, "audio is corrupt", "media"),
            (400, "", "media"),
        ],
    )
    def test_table(self, status, body, reason):
        fatal, got = failures.classify_status(status, body)
        assert got == reason
        assert fatal is (reason in failures.FATAL_REASONS)

    def test_only_media_and_job_are_fatal(self):
        assert failures.FATAL_REASONS == {"media", "job"}

    def test_markers_are_case_insensitive(self):
        _, reason = failures.classify_status(401, "QUOTA_EXCEEDED")
        assert reason == "quota"


class _Resp:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text


class TestRaiseForStatus:
    def test_2xx_raises_nothing(self):
        for status in (200, 201, 204, 299):
            assert failures.raise_for_status(_Resp(status), provider="p") is None

    def test_quota_401_is_retryable_and_carries_the_reason(self):
        with pytest.raises(RetryableTranscriptionError) as e:
            failures.raise_for_status(
                _Resp(401, ELEVENLABS_QUOTA_401),
                provider="elevenlabs",
                label="ElevenLabs",
            )
        assert e.value.reason == "quota"
        assert e.value.status_code == 401
        assert e.value.provider == "elevenlabs"
        # The message still names the provider, the status and the body.
        assert "ElevenLabs 401" in str(e.value)
        assert "quota_exceeded" in str(e.value)

    def test_media_400_is_fatal(self):
        with pytest.raises(TranscriptionError) as e:
            failures.raise_for_status(
                _Resp(400, "unsupported audio format"),
                provider="whisper",
                label="whisper",
            )
        assert not isinstance(e.value, RetryableTranscriptionError)
        assert e.value.reason == "media"

    def test_op_is_named_for_multi_stage_providers(self):
        with pytest.raises(TranscriptionError) as e:
            failures.raise_for_status(
                _Resp(503, "down"), provider="aai", label="AssemblyAI", op="submit"
            )
        assert "AssemblyAI submit 503" in str(e.value)

    def test_unreadable_body_still_classifies(self):
        class Hostile:
            status_code = 429

            @property
            def text(self):
                raise RuntimeError("stream consumed")

        with pytest.raises(RetryableTranscriptionError) as e:
            failures.raise_for_status(Hostile(), provider="p")
        assert e.value.reason == "rate"

    def test_body_is_truncated_in_the_message(self):
        with pytest.raises(TranscriptionError) as e:
            failures.raise_for_status(_Resp(400, "x" * 5000), provider="p")
        assert len(str(e.value)) < 400


class TestConstructors:
    def test_missing_credentials_is_retryable_auth(self):
        exc = failures.missing_credentials("ELEVENLABS_API_KEY", provider="elevenlabs")
        assert isinstance(exc, RetryableTranscriptionError)
        assert exc.reason == "auth"
        assert "ELEVENLABS_API_KEY" in str(exc)

    def test_unsupported_is_retryable(self):
        # "gladia cannot do Russian" says nothing about the next provider.
        exc = failures.unsupported("no language pack", provider="gladia")
        assert isinstance(exc, RetryableTranscriptionError)
        assert exc.reason == "unsupported"

    @pytest.mark.parametrize(
        "make,reason",
        [
            (failures.timed_out, "timeout"),
            (failures.transport, "transport"),
            (failures.unavailable, "unavailable"),
        ],
    )
    def test_retryable_constructors(self, make, reason):
        exc = make("x", provider="p")
        assert isinstance(exc, RetryableTranscriptionError)
        assert exc.reason == reason

    @pytest.mark.parametrize(
        "make,reason",
        [(failures.job_failed, "job"), (failures.bad_media, "media")],
    )
    def test_fatal_constructors(self, make, reason):
        exc = make("x", provider="p")
        assert isinstance(exc, TranscriptionError)
        assert not isinstance(exc, RetryableTranscriptionError)
        assert exc.reason == reason


class TestEveryAdapterUsesTheHelper:
    """No adapter may hand-roll the taxonomy again — that is how eight
    copies of one rule drifted into one production incident."""

    def test_no_provider_module_raises_the_error_classes_directly(self):
        import pathlib

        import stapel_agent.stt.providers as pkg

        offenders = []
        for path in pathlib.Path(pkg.__file__).parent.glob("*.py"):
            source = path.read_text()
            if "raise TranscriptionError(" in source or (
                "raise RetryableTranscriptionError(" in source
            ):
                offenders.append(path.name)
        assert offenders == []

    def test_no_provider_module_branches_on_a_status_class(self):
        import pathlib

        import stapel_agent.stt.providers as pkg

        offenders = []
        for path in pathlib.Path(pkg.__file__).parent.glob("*.py"):
            for line in path.read_text().splitlines():
                stripped = line.strip()
                # `>= 500` in a poll loop is a keep-polling decision, not a
                # taxonomy call; `>= 400` / `== 429` were the taxonomy.
                if "status_code >= 400" in stripped or "status_code == 429" in stripped:
                    offenders.append((path.name, stripped))
        assert offenders == []
