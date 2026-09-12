"""The client-stand regression: a provider's ACCOUNT condition walks the chain.

Production evidence (a client stand, 2026-09-09 22:02 CEST → 2026-09-12):
ElevenLabs answered an exhausted account with

    401 {"detail": {"type": "invalid_request", "code": "quota_exceeded",
         "message": "This request exceeds your quota of 23736. You have
         18 credits remaining..."}}

The adapter classified by status class — "every other 4xx is fatal" — so
the router reported failure without trying ``STT_FALLBACK_CHAIN``. 41
consecutive recordings were lost and ``fallback_used`` had never been
true in the whole history of the log table.

These tests run END TO END through ``services.transcribe`` with the REAL
ElevenLabs and AssemblyAI adapters (only ``requests`` and the audio fetch
are faked), because that seam is exactly where the defect lived: each
adapter passed its own tests, the router passed its own tests against
fakes, and the pair was wrong.
"""
import json

import pytest

from stapel_agent import services
from stapel_agent.models import PromptLog
from stapel_agent.stt.base import AudioRef
from stapel_agent.tests.fakes import serve_audio

AUDIO = AudioRef(url="https://minio.test/bucket/rec.mp3?X-Sig=s3cr3t")

#: Verbatim from the stand's telemetry (numbers included).
QUOTA_401 = json.dumps(
    {
        "detail": {
            "type": "invalid_request",
            "code": "quota_exceeded",
            "message": (
                "This request exceeds your quota of 23736. You have 18 "
                "credits remaining, while 1080 credits are required for "
                "this request."
            ),
        }
    }
)

ASSEMBLY_DONE = {
    "status": "completed",
    "language_code": "en_us",
    "audio_duration": 3,
    "words": [
        {"text": "Hi", "start": 0, "end": 400, "confidence": 0.9, "speaker": "A"},
        {"text": "all", "start": 500, "end": 900, "confidence": 0.8, "speaker": "A"},
    ],
    "utterances": [
        {"text": "Hi all", "start": 0, "end": 900, "speaker": "A", "confidence": 0.9},
    ],
}


class FakeResponse:
    def __init__(self, payload=None, status_code=200, text=None):
        self._payload = payload
        self.status_code = status_code
        self.text = text if text is not None else json.dumps(payload or {})

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


@pytest.fixture
def stand(settings, monkeypatch):
    """The stand's own configuration: ElevenLabs first, AssemblyAI behind it."""
    settings.STAPEL_AGENT = {
        "ELEVENLABS_API_KEY": "el-test",
        "ASSEMBLYAI_API_KEY": "aai-test",
        "DEFAULT_STT_PROVIDER": "elevenlabs",
        "STT_FALLBACK_CHAIN": ["assemblyai"],
    }
    monkeypatch.setattr("time.sleep", lambda s: None)  # AssemblyAI poll loop
    return settings


def wire(monkeypatch, *, elevenlabs, assemblyai_submit=None, assemblyai_poll=None):
    """Queue one answer per HTTP call per provider; return the call log.

    Dispatched on the URL and patched ONCE: ``requests`` is one module
    object, so patching ``...providers.elevenlabs.requests.post`` and
    ``...providers.assemblyai.requests.post`` sets the same attribute
    twice and the second adapter would answer for both.
    """
    calls: list[str] = []
    queues = {
        "elevenlabs": list(elevenlabs),
        "assemblyai": list(assemblyai_submit or []),
    }
    polls = list(assemblyai_poll or [])

    def fake_post(url, headers=None, files=None, data=None, json=None, timeout=None):
        if "elevenlabs" in url:
            who = "elevenlabs"
        elif "assemblyai" in url:
            who = "assemblyai"
        else:
            raise AssertionError(f"unexpected POST to {url}")
        calls.append(who)
        step = queues[who].pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    def fake_get(url, headers=None, timeout=None):
        calls.append("assemblyai-poll")
        return polls.pop(0)

    monkeypatch.setattr("requests.post", fake_post)
    monkeypatch.setattr("requests.get", fake_get)
    return calls


@pytest.mark.django_db
class TestQuotaWalksTheChain:
    def test_elevenlabs_quota_401_falls_back_to_assemblyai(
        self, stand, monkeypatch
    ):
        serve_audio(monkeypatch, b"mp3-bytes")
        calls = wire(
            monkeypatch,
            elevenlabs=[FakeResponse(status_code=401, text=QUOTA_401)],
            assemblyai_submit=[FakeResponse({"id": "t_1"})],
            assemblyai_poll=[FakeResponse(ASSEMBLY_DONE)],
        )

        result = services.transcribe(AUDIO, language="en")

        assert result["status"] == "ok"
        assert result["provider_used"] == "assemblyai"
        assert result["fallback_used"] is True  # never true in production
        assert calls == ["elevenlabs", "assemblyai", "assemblyai-poll"]

        log = PromptLog.objects.get()
        assert log.metadata["fallback_used"] is True
        assert [
            (a["provider"], a["error_kind"], a["reason"])
            for a in log.metadata["attempts"]
        ] == [
            ("elevenlabs", "retryable", "quota"),
            ("assemblyai", None, None),
        ]
        # The operator's question — "why did the first one decline?" — is
        # answerable from the row without reading the provider's prose.
        assert "quota_exceeded" in log.metadata["attempts"][0]["error"]

    def test_bad_key_401_walks_with_reason_auth(self, stand, monkeypatch):
        serve_audio(monkeypatch, b"mp3-bytes")
        wire(
            monkeypatch,
            elevenlabs=[
                FakeResponse(status_code=401, text='{"detail":"Invalid API key"}')
            ],
            assemblyai_submit=[FakeResponse({"id": "t_1"})],
            assemblyai_poll=[FakeResponse(ASSEMBLY_DONE)],
        )

        result = services.transcribe(AUDIO)

        assert result["provider_used"] == "assemblyai"
        assert result["fallback_used"] is True
        log = PromptLog.objects.get()
        assert log.metadata["attempts"][0]["reason"] == "auth"

    def test_media_400_is_fatal_and_no_other_provider_is_billed(
        self, stand, monkeypatch
    ):
        # The one family that MUST stop the chain: the next provider would
        # fail on the same bytes, and trying it costs money for nothing.
        serve_audio(monkeypatch, b"mp3-bytes")
        calls = wire(
            monkeypatch,
            elevenlabs=[
                FakeResponse(
                    status_code=400, text='{"detail":"unsupported audio format"}'
                )
            ],
            assemblyai_submit=[FakeResponse({"id": "t_1"})],
            assemblyai_poll=[FakeResponse(ASSEMBLY_DONE)],
        )

        result = services.transcribe(AUDIO)

        assert result["status"] == "failure"
        assert "unsupported audio format" in result["reason"]
        assert calls == ["elevenlabs"]  # AssemblyAI never called
        log = PromptLog.objects.get()
        assert log.metadata["fallback_used"] is False
        assert log.metadata["attempts"] == [
            {
                "provider": "elevenlabs",
                "error_kind": "fatal",
                "reason": "media",
                "error": log.metadata["attempts"][0]["error"],
            }
        ]

    def test_exhausted_chain_names_every_provider_and_its_reason(
        self, stand, monkeypatch
    ):
        serve_audio(monkeypatch, b"mp3-bytes")
        wire(
            monkeypatch,
            elevenlabs=[FakeResponse(status_code=401, text=QUOTA_401)],
            assemblyai_submit=[FakeResponse(status_code=503, text="upstream down")],
        )

        result = services.transcribe(AUDIO)

        assert result["status"] == "failure"
        assert result["reason"].startswith("all STT providers failed: ")
        assert "elevenlabs (quota)" in result["reason"]
        assert "assemblyai (server)" in result["reason"]
        log = PromptLog.objects.get()
        assert [a["reason"] for a in log.metadata["attempts"]] == ["quota", "server"]

    def test_language_route_is_the_chain_for_that_language(
        self, stand, monkeypatch
    ):
        # STT_LANGUAGE_ROUTES replaces the default chain wholesale, so the
        # walk has to work there too — the stand routes ru/en by language.
        stand.STAPEL_AGENT = {
            **stand.STAPEL_AGENT,
            "STT_LANGUAGE_ROUTES": {"ru": ["elevenlabs", "assemblyai"]},
        }
        serve_audio(monkeypatch, b"mp3-bytes")
        wire(
            monkeypatch,
            elevenlabs=[FakeResponse(status_code=401, text=QUOTA_401)],
            assemblyai_submit=[FakeResponse({"id": "t_1"})],
            assemblyai_poll=[FakeResponse(ASSEMBLY_DONE)],
        )

        result = services.transcribe(AUDIO, language="ru-RU")

        assert result["provider_used"] == "assemblyai"
        assert result["fallback_used"] is True

    def test_pinned_provider_still_never_falls_back(self, stand, monkeypatch):
        # An explicit provider is a QA pin: masking its failure with a
        # fallback would defeat the pin, quota or not.
        serve_audio(monkeypatch, b"mp3-bytes")
        calls = wire(
            monkeypatch,
            elevenlabs=[FakeResponse(status_code=401, text=QUOTA_401)],
            assemblyai_submit=[FakeResponse({"id": "t_1"})],
        )

        result = services.transcribe(AUDIO, provider="elevenlabs")

        assert result["status"] == "failure"
        assert "quota" in result["reason"]
        assert calls == ["elevenlabs"]
