"""A 9 MB transcript survives the broker without this module changing.

``llm.transcribe`` has two reply shapes and the CALLER chooses between them
(see test_transcript_handoff.py): a reference, when it hands over a
presigned PUT, and the transcript inline otherwise. The inline shape is the
one a 2h28m meeting blew up on 2026-09-09 — 8 647 617 bytes against a broker
that carries 8 388 608.

stapel-core 0.66.0 makes that survivable underneath us: a reply the broker
refuses is written to the object store both services share and replaced on
the wire by a small ``{"$ref": ...}`` envelope, which the calling transport
resolves. The point of these tests is that NOTHING HERE CHANGES — the
function still returns the transcript inline, the caller still receives the
transcript inline, and neither end knows a store was involved.
"""
import json

import pytest
from django.test import override_settings

from stapel_agent import functions

CAP = 8 * 1024 * 1024  # the fleet's NATS brokers


def long_meeting_transcript(words: int = 120_000) -> dict:
    """A transcript the shape and size of a 148-minute meeting's."""
    return {
        "provider": "elevenlabs",
        "language": "ru",
        "duration_seconds": 8898.005333,
        "words": [
            {"text": "слово", "start": i * 0.31, "end": i * 0.31 + 0.3,
             "speaker": "speaker_0"}
            for i in range(words)
        ],
        "utterances": [],
        "speakers_detected": ["speaker_0", "speaker_1"],
        "biasing": None,
    }


class MemoryStore:
    """Stand-in for the shared bucket, on both ends of the seam."""

    name = "django"

    def __init__(self):
        self.objects = {}

    def put(self, key, data, *, ttl_seconds):
        self.objects[key] = data
        return key

    def get(self, key):
        return self.objects[key]

    def delete(self, key):
        self.objects.pop(key, None)


@pytest.fixture
def transcribes_a_long_meeting(monkeypatch):
    transcript = long_meeting_transcript()
    monkeypatch.setattr(
        "stapel_agent.services.transcribe",
        lambda *a, **k: {"status": "ok", "transcript": transcript,
                         "provider_used": "elevenlabs", "fallback_used": False},
    )
    return transcript


def test_the_inline_reply_of_a_long_meeting_is_over_the_broker_cap(
    transcribes_a_long_meeting,
):
    """The premise. If this ever stops being true, the tests below are moot."""
    result = functions.llm_transcribe({"audio_url": "https://s3/a.opus"})
    wire = json.dumps({"result": result}, default=str).encode()
    assert len(wire) > CAP, f"the fixture is only {len(wire)} bytes"


def test_a_9mb_transcript_reaches_the_caller_unchanged(transcribes_a_long_meeting):
    """End to end over the transport that lost it, with a store configured.

    Provider side: what ``manage.py serve_functions`` does with this
    module's reply. Caller side: what ``comm.call()`` does with what comes
    back. Between them, a broker that refuses anything over 8 MiB.
    """
    from stapel_core.comm import nats as nats_mod
    from stapel_core.comm import overflow
    from stapel_core.django.management.commands.serve_functions import fit_reply

    store = MemoryStore()
    with override_settings(STAPEL_COMM={"LARGE_REPLY": {"STORE": store}}):
        overflow.reset_store()

        # ── provider side ────────────────────────────────────────────
        result = functions.llm_transcribe({"audio_url": "https://s3/a.opus"})
        assert "transcript" in result, "the agent's reply shape is untouched"
        reply = json.dumps({"result": result}, default=str).encode()
        wire = fit_reply(reply, CAP, "llm.transcribe")
        assert len(wire) < 1024, "what crosses the broker must fit the broker"

        # ── caller side ──────────────────────────────────────────────
        class _Bridge:
            def max_payload(self, timeout=5.0):
                return CAP

            def request(self, subject, data, timeout):
                assert len(data) <= CAP
                return wire

        import pytest as _pytest

        with _pytest.MonkeyPatch.context() as mp:
            mp.setattr(nats_mod, "get_bridge", lambda: _Bridge())
            got = nats_mod.nats_function_transport(
                "llm.transcribe", {"audio_url": "https://s3/a.opus"}
            )

        assert got == result
        assert len(got["transcript"]["words"]) == 120_000
        overflow.reset_store()


def test_without_a_store_the_same_call_still_fails_loudly(transcribes_a_long_meeting):
    """No silent regression for a deployment that configures nothing.

    The work is lost either way — but the caller is told, with the sizes and
    the setting, instead of waiting out a timeout.
    """
    from stapel_core.comm import nats as nats_mod
    from stapel_core.comm import overflow
    from stapel_core.comm.exceptions import FunctionPayloadTooLarge
    from stapel_core.django.management.commands.serve_functions import fit_reply

    with override_settings(STAPEL_COMM={"LARGE_REPLY": {}}):
        overflow.reset_store()
        result = functions.llm_transcribe({"audio_url": "https://s3/a.opus"})
        reply = json.dumps({"result": result}, default=str).encode()
        marker = fit_reply(reply, CAP, "llm.transcribe")
        assert json.loads(marker)["error_code"] == "payload_too_large"

        class _Bridge:
            def max_payload(self, timeout=5.0):
                return CAP

            def request(self, subject, data, timeout):
                return marker

        import pytest as _pytest

        with _pytest.MonkeyPatch.context() as mp:
            mp.setattr(nats_mod, "get_bridge", lambda: _Bridge())
            with _pytest.raises(FunctionPayloadTooLarge) as exc:
                nats_mod.nats_function_transport("llm.transcribe", {"audio_url": "x"})
        assert exc.value.size == len(reply)
        assert 'STAPEL_COMM["LARGE_REPLY"]["STORE"]' in str(exc.value)
