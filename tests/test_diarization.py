"""Diarization seam — normalized schema, the pyannote-http adapter
(mocked ``requests``; no network, no keys), registry merge semantics,
W007/W008 checks, the ``services.diarize`` surface, the ``llm.diarize``
comm function and the HTTP endpoint.

Error taxonomy under test everywhere: 429/5xx/timeouts/transport →
``RetryableDiarizationError``, other 4xx / bad input / bad knobs /
missing config → fatal ``DiarizationError``.
"""
import json

import pytest
import requests

from stapel_core.comm import call, function_registry
from stapel_core.comm.exceptions import SchemaValidationError

from stapel_agent import services
from stapel_agent.checks import check_diarization_providers
from stapel_agent.diarization import (
    BUILTIN_DIARIZATION_PROVIDERS,
    _reset_runtime_diarization_providers,
    register_diarization_provider,
    registered_diarization_providers,
)
from stapel_agent.diarization.base import (
    DiarizationError,
    DiarTurn,
    NormalizedDiarization,
    RetryableDiarizationError,
    turns_from_segments,
    validate_speaker_counts,
)
from stapel_agent.diarization.providers.pyannote_http import PyannoteHttpProvider
from stapel_agent.models import PromptLog
from stapel_agent.providers.base import ProviderError
from stapel_agent.stt.base import AudioRef
from stapel_agent.tests.fakes import FakeDiarizationProvider

DIARIZE_URL = "/agent/api/v1/llm/diarize"


@pytest.fixture(autouse=True)
def clean_runtime_registry():
    _reset_runtime_diarization_providers()
    yield
    _reset_runtime_diarization_providers()


# ─── base ──────────────────────────────────────────────────────────────


class TestBase:
    def test_error_taxonomy_joins_the_house_hierarchy(self):
        assert issubclass(DiarizationError, ProviderError)
        assert issubclass(RetryableDiarizationError, DiarizationError)
        err = DiarizationError("boom", provider="p", status_code=400)
        assert err.provider == "p"
        assert err.status_code == 400

    def test_to_dict_roundtrips_turns(self):
        diar = NormalizedDiarization(
            provider="x",
            duration_seconds=3.0,
            turns=[DiarTurn(speaker="A", start=0.0, end=1.5, confidence=0.8)],
            speakers_detected=["A"],
            raw={"k": "v"},
        )
        data = diar.to_dict()
        assert data["turns"] == [
            {"speaker": "A", "start": 0.0, "end": 1.5, "confidence": 0.8}
        ]
        assert data["duration_seconds"] == 3.0
        assert data["raw"] == {"k": "v"}

    def test_turns_from_segments_preserves_order_and_clamps_inverted(self):
        turns = turns_from_segments(
            [
                {"speaker": "B", "start": 2.0, "end": 3.0},
                # inverted wire segment: clamped, never dropped
                {"speaker": "A", "start": 5.0, "end": 4.0},
            ]
        )
        assert [t.speaker for t in turns] == ["B", "A"]
        assert turns[1].start == 5.0
        assert turns[1].end == 5.0  # clamped up to start

    def test_speaker_count_validation(self):
        # valid combinations pass
        validate_speaker_counts(num_speakers=2)
        validate_speaker_counts(min_speakers=1, max_speakers=3)
        # exact count XOR bounds
        with pytest.raises(DiarizationError, match="contradictory"):
            validate_speaker_counts(num_speakers=2, min_speakers=1)
        # every count >= 1
        with pytest.raises(DiarizationError, match=">= 1"):
            validate_speaker_counts(num_speakers=0)
        with pytest.raises(DiarizationError, match=">= 1"):
            validate_speaker_counts(min_speakers=0, max_speakers=2)
        # min <= max
        with pytest.raises(DiarizationError, match="min_speakers 3 > max_speakers 2"):
            validate_speaker_counts(min_speakers=3, max_speakers=2)


# ─── pyannote-http adapter ─────────────────────────────────────────────


class FakeResponse:
    def __init__(self, payload=None, status_code=200, text=None):
        self._payload = payload
        self.status_code = status_code
        self.text = text if text is not None else json.dumps(payload or {})

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


PYANNOTE_BODY = {
    "diarization": [
        {"speaker": "SPEAKER_00", "start": 0.0, "end": 2.5},
        {"speaker": "SPEAKER_01", "start": 2.5, "end": 4.0, "confidence": 0.9},
        {"speaker": "SPEAKER_00", "start": 4.0, "end": 6.0},
    ],
    "duration": 6.2,
}


class TestPyannoteHttp:
    @pytest.fixture
    def configured(self, settings):
        settings.STAPEL_AGENT = {
            "PYANNOTE_BASE_URL": "http://pyannote:8000/",
            "PYANNOTE_API_KEY": "diar-key",
        }
        return settings

    def _run(self, monkeypatch, responses, *, audio=None, **kwargs):
        captured = []
        queue = list(responses)

        def fake_post(url, headers=None, files=None, data=None, timeout=None):
            captured.append(
                {
                    "url": url,
                    "headers": headers,
                    "files": files,
                    "data": data,
                    "timeout": timeout,
                }
            )
            step = queue.pop(0)
            if isinstance(step, Exception):
                raise step
            return step

        monkeypatch.setattr(
            "stapel_agent.diarization.providers.pyannote_http.requests.post",
            fake_post,
        )
        result = PyannoteHttpProvider().diarize(
            audio=audio or AudioRef(data=b"wav-bytes", mime="audio/wav"), **kwargs
        )
        return result, captured

    def test_missing_base_url_is_fatal(self, settings):
        settings.STAPEL_AGENT = {}
        with pytest.raises(DiarizationError, match="PYANNOTE_BASE_URL") as exc_info:
            PyannoteHttpProvider().diarize(audio=AudioRef(data=b"x"))
        assert not isinstance(exc_info.value, RetryableDiarizationError)

    def test_happy_path_exact_request_shape(self, configured, monkeypatch):
        result, captured = self._run(
            monkeypatch,
            [FakeResponse(PYANNOTE_BODY)],
            num_speakers=2,
            timeout_seconds=90,
        )
        req = captured[0]
        # trailing slash stripped, documented path
        assert req["url"] == "http://pyannote:8000/diarize"
        assert req["headers"] == {"Authorization": "Bearer diar-key"}
        name, payload, mime = req["files"]["file"]
        assert payload == b"wav-bytes"
        assert mime == "audio/wav"
        assert req["data"] == {"num_speakers": 2}
        assert req["timeout"] == 90

        assert result.provider == "pyannote-http"
        assert result.duration_seconds == 6.2
        assert [t.speaker for t in result.turns] == [
            "SPEAKER_00", "SPEAKER_01", "SPEAKER_00",
        ]
        assert result.turns[1].confidence == 0.9
        assert result.turns[0].confidence is None
        assert result.speakers_detected == ["SPEAKER_00", "SPEAKER_01"]
        assert result.raw == PYANNOTE_BODY

    def test_no_key_means_no_auth_header(self, settings, monkeypatch):
        settings.STAPEL_AGENT = {"PYANNOTE_BASE_URL": "http://pyannote:8000"}
        _, captured = self._run(monkeypatch, [FakeResponse(PYANNOTE_BODY)])
        assert captured[0]["headers"] == {}

    def test_bounds_travel_via_provider_options(self, configured, monkeypatch):
        _, captured = self._run(
            monkeypatch,
            [FakeResponse(PYANNOTE_BODY)],
            provider_options={"min_speakers": 2, "max_speakers": 4, "beta": "x"},
        )
        # min/max become first-class form fields, the rest passes through
        assert captured[0]["data"] == {
            "min_speakers": 2,
            "max_speakers": 4,
            "beta": "x",
        }

    def test_exact_count_with_bounds_is_fatal_before_any_call(
        self, configured, monkeypatch
    ):
        called = []
        monkeypatch.setattr(
            "stapel_agent.diarization.providers.pyannote_http.requests.post",
            lambda *a, **k: called.append(1),
        )
        with pytest.raises(DiarizationError, match="contradictory"):
            PyannoteHttpProvider().diarize(
                audio=AudioRef(data=b"x"),
                num_speakers=2,
                provider_options={"min_speakers": 1},
            )
        assert called == []

    def test_duration_inferred_from_turns_when_absent(self, configured, monkeypatch):
        body = {"diarization": [{"speaker": "S", "start": 0.0, "end": 7.5}]}
        result, _ = self._run(monkeypatch, [FakeResponse(body)])
        assert result.duration_seconds == 7.5

    def test_empty_diarization_is_data_not_an_error(self, configured, monkeypatch):
        # The iron-benchmark empty=error gate is merge policy — NOT ported.
        result, _ = self._run(monkeypatch, [FakeResponse({"diarization": []})])
        assert result.turns == []
        assert result.speakers_detected == []
        assert result.duration_seconds is None

    def test_missing_diarization_key_is_fatal(self, configured, monkeypatch):
        with pytest.raises(DiarizationError, match="no 'diarization' list"):
            self._run(monkeypatch, [FakeResponse({"status": "done"})])

    def test_malformed_segment_is_fatal(self, configured, monkeypatch):
        body = {"diarization": [{"speaker": "S", "start": "zero"}]}
        with pytest.raises(DiarizationError, match="segment malformed"):
            self._run(monkeypatch, [FakeResponse(body)])

    @pytest.mark.parametrize("status", [429, 500, 503])
    def test_transient_statuses_are_retryable(self, configured, monkeypatch, status):
        with pytest.raises(RetryableDiarizationError):
            self._run(monkeypatch, [FakeResponse(status_code=status, text="busy")])

    def test_client_error_is_fatal(self, configured, monkeypatch):
        with pytest.raises(DiarizationError) as exc_info:
            self._run(monkeypatch, [FakeResponse(status_code=422, text="bad audio")])
        assert not isinstance(exc_info.value, RetryableDiarizationError)
        assert exc_info.value.status_code == 422

    def test_timeout_and_transport_are_retryable(self, configured, monkeypatch):
        with pytest.raises(RetryableDiarizationError):
            self._run(monkeypatch, [requests.Timeout("slow")])
        with pytest.raises(RetryableDiarizationError):
            self._run(monkeypatch, [requests.ConnectionError("down")])

    def test_non_json_body_is_retryable(self, configured, monkeypatch):
        with pytest.raises(RetryableDiarizationError, match="non-JSON"):
            self._run(monkeypatch, [FakeResponse(text="<html>proxy</html>")])

    def test_bad_audio_path_is_fatal_under_diar_taxonomy(self, configured):
        with pytest.raises(DiarizationError) as exc_info:
            PyannoteHttpProvider().diarize(
                audio=AudioRef(path="/no/such/file.wav")
            )
        assert not isinstance(exc_info.value, RetryableDiarizationError)


# ─── registry ──────────────────────────────────────────────────────────


class TestRegistry:
    def test_builtin_pyannote_registered(self):
        assert "pyannote-http" in registered_diarization_providers()
        assert "pyannote-http" in BUILTIN_DIARIZATION_PROVIDERS

    def test_settings_merge_over_builtins(self, settings):
        settings.STAPEL_AGENT = {
            "DIARIZATION_PROVIDERS": {
                "custom": "stapel_agent.tests.fakes.FakeDiarizationProvider",
                "pyannote-http": None,  # None removes a name
            }
        }
        effective = registered_diarization_providers()
        assert "custom" in effective
        assert "pyannote-http" not in effective

    def test_runtime_beats_settings(self, settings):
        settings.STAPEL_AGENT = {
            "DIARIZATION_PROVIDERS": {"x": "not.a.real.Path"}
        }
        register_diarization_provider("x", FakeDiarizationProvider)
        assert registered_diarization_providers()["x"] is FakeDiarizationProvider

    def test_register_rejects_non_provider(self):
        with pytest.raises(TypeError):
            register_diarization_provider("bad", object)


class TestSystemChecks:
    def test_clean_default_config(self):
        assert check_diarization_providers(None) == []

    def test_unimportable_entry_is_w007(self, settings):
        settings.STAPEL_AGENT = {
            "DIARIZATION_PROVIDERS": {"broken": "no.such.module.Cls"}
        }
        issues = check_diarization_providers(None)
        assert [i.id for i in issues] == ["stapel_agent.W007"]

    def test_non_provider_class_is_w007(self, settings):
        settings.STAPEL_AGENT = {
            "DIARIZATION_PROVIDERS": {
                "bad": "stapel_agent.tests.fakes.NotADiarizationProvider"
            }
        }
        assert [i.id for i in check_diarization_providers(None)] == [
            "stapel_agent.W007"
        ]

    def test_unknown_default_is_w008(self, settings):
        settings.STAPEL_AGENT = {"DEFAULT_DIARIZATION_PROVIDER": "ghost"}
        issues = check_diarization_providers(None)
        assert [i.id for i in issues] == ["stapel_agent.W008"]
        assert "ghost" in issues[0].msg

    def test_registered_with_django(self):
        from django.core.checks.registry import registry

        assert check_diarization_providers in registry.registered_checks


# ─── service ───────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestDiarizeService:
    def test_happy_path_envelope(self, fake_diarization):
        result = services.diarize(
            AudioRef(url="https://minio.test/rec.mp3"), num_speakers=2
        )
        assert result["status"] == "ok"
        assert result["provider_used"] == "fake-diar"
        assert result["diarization"]["provider"] == "fake-diar"
        assert [t["speaker"] for t in result["diarization"]["turns"]] == [
            "SPEAKER_00", "SPEAKER_01",
        ]
        call = fake_diarization.calls[0]
        assert call["audio"].url == "https://minio.test/rec.mp3"
        assert call["num_speakers"] == 2

    def test_success_writes_a_count_only_ledger_row(self, fake_diarization):
        services.diarize(AudioRef(url="https://minio.test/rec.mp3?sig=SECRET"))
        log = PromptLog.objects.get()
        assert log.source == "diarize"
        assert log.model == "fake-diar"
        assert log.status == "success"
        # PII-safe descriptor: host only, never the signed query string
        assert log.prompt == "url:minio.test"
        assert "SECRET" not in json.dumps(
            {"prompt": log.prompt, "metadata": log.metadata}
        )
        assert log.metadata["turns"] == 2
        assert log.metadata["speakers_detected"] == 2
        assert log.metadata["duration_seconds"] == 4.0

    def test_not_an_audioref_is_failure(self, fake_diarization):
        result = services.diarize("https://x/a.mp3")
        assert result == {"status": "failure", "reason": "audio must be an AudioRef"}

    def test_unknown_provider_is_failure(self, fake_diarization):
        result = services.diarize(AudioRef(url="https://x/a.mp3"), provider="ghost")
        assert result["status"] == "failure"
        assert "Unknown diarization provider 'ghost'" in result["reason"]

    def test_fatal_error_degrades_to_failure_and_error_row(self, fake_diarization):
        result = services.diarize(
            AudioRef(url="https://x/a.mp3"), provider="fatal-diar"
        )
        assert result == {"status": "failure", "reason": "audio is not decodable"}
        log = PromptLog.objects.get()
        assert log.status == "error"
        assert log.model == "fatal-diar"

    def test_provider_options_forwarded(self, fake_diarization):
        services.diarize(
            AudioRef(url="https://x/a.mp3"),
            provider_options={"min_speakers": 2},
            timeout_seconds=30,
        )
        call = fake_diarization.calls[0]
        assert call["provider_options"] == {"min_speakers": 2}
        assert call["timeout_seconds"] == 30


# ─── comm function ─────────────────────────────────────────────────────


@pytest.mark.django_db
class TestLlmDiarizeFunction:
    def test_registered(self):
        assert "llm.diarize" in function_registry.names()

    def test_happy_path(self, fake_diarization):
        result = call(
            "llm.diarize",
            {"audio_url": "https://minio.test/rec.mp3", "num_speakers": 2},
        )
        assert result["status"] == "ok"
        assert result["provider_used"] == "fake-diar"
        assert len(result["diarization"]["turns"]) == 2
        assert fake_diarization.calls[0]["num_speakers"] == 2

    def test_failure_is_a_status_dict_not_an_exception(self, fake_diarization):
        result = call(
            "llm.diarize",
            {"audio_url": "https://x/a.mp3", "provider": "fatal-diar"},
        )
        assert result == {"status": "failure", "reason": "audio is not decodable"}

    def test_schema_rejects_missing_audio_url(self, fake_diarization):
        with pytest.raises(SchemaValidationError):
            call("llm.diarize", {"num_speakers": 2})

    def test_schema_rejects_zero_num_speakers(self, fake_diarization):
        with pytest.raises(SchemaValidationError):
            call("llm.diarize", {"audio_url": "https://x/a.mp3", "num_speakers": 0})

    def test_schema_rejects_extra_keys(self, fake_diarization):
        with pytest.raises(SchemaValidationError):
            call("llm.diarize", {"audio_url": "https://x/a.mp3", "beep": 1})


# ─── HTTP endpoint ─────────────────────────────────────────────────────


@pytest.mark.django_db
class TestDiarizeEndpoint:
    def _post(self, client, body=None, **kwargs):
        body = body or {"audio_url": "https://minio.test/rec.mp3"}
        return client.post(DIARIZE_URL, body, format="json", **kwargs)

    def test_anonymous_rejected(self, api_client, fake_diarization):
        assert self._post(api_client).status_code in (401, 403)
        assert fake_diarization.calls == []

    def test_plain_user_rejected(self, user, fake_diarization):
        from rest_framework.test import APIClient

        client = APIClient()
        client.force_authenticate(user=user)
        assert self._post(client).status_code == 403

    def test_service_key_happy_path(self, api_client, fake_diarization):
        resp = self._post(
            api_client,
            {
                "audio_url": "https://minio.test/rec.mp3",
                "num_speakers": 2,
                "provider": "fake-diar",
                "timeout_seconds": 120,
                "provider_options": {"exclusive": True},
            },
            HTTP_X_API_KEY="test-service-key",
        )
        assert resp.status_code == 200, resp.content
        data = resp.json()
        assert data["status"] == "ok"
        assert data["provider_used"] == "fake-diar"
        assert data["diarization"]["speakers_detected"] == [
            "SPEAKER_00", "SPEAKER_01",
        ]
        call = fake_diarization.calls[0]
        assert call["audio"].url == "https://minio.test/rec.mp3"
        assert call["num_speakers"] == 2
        assert call["timeout_seconds"] == 120

    def test_staff_user_accepted_and_logged(
        self, staff_client, staff_user, fake_diarization
    ):
        resp = self._post(staff_client)
        assert resp.status_code == 200
        log = PromptLog.objects.get()
        assert log.source == "diarize"
        assert log.user_id == str(staff_user.pk)

    def test_diarization_failure_is_http_200(self, api_client, fake_diarization):
        resp = self._post(
            api_client,
            {"audio_url": "https://x/a.mp3", "provider": "fatal-diar"},
            HTTP_X_API_KEY="test-service-key",
        )
        assert resp.status_code == 200
        assert resp.json() == {
            "status": "failure",
            "reason": "audio is not decodable",
        }

    def test_missing_audio_url_is_400(self, api_client, fake_diarization):
        resp = self._post(
            api_client, {"num_speakers": 2}, HTTP_X_API_KEY="test-service-key"
        )
        assert resp.status_code == 400
        assert fake_diarization.calls == []

    def test_zero_num_speakers_is_400(self, api_client, fake_diarization):
        resp = self._post(
            api_client,
            {"audio_url": "https://x/a.mp3", "num_speakers": 0},
            HTTP_X_API_KEY="test-service-key",
        )
        assert resp.status_code == 400
        assert fake_diarization.calls == []

    def test_non_positive_timeout_is_400(self, api_client, fake_diarization):
        resp = self._post(
            api_client,
            {"audio_url": "https://x/a.mp3", "timeout_seconds": 0},
            HTTP_X_API_KEY="test-service-key",
        )
        assert resp.status_code == 400
        assert fake_diarization.calls == []
