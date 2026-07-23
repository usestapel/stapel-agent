"""The generic vocabulary-biasing seam — keyterms + provider_options.

Covers: keyterm wiring on the pre-existing adapters (ElevenLabs Scribe
``keyterms``, AssemblyAI ``keyterms_prompt``), the passthrough seam,
schema/service/HTTP threading, ``biasing`` round-trips, the
non-supporting-adapter contract (applied=False, never a failure) and the
privacy invariant: the biasing block carries COUNTS ONLY — term strings
(customer data) never appear in it.
"""
import json

import pytest

from stapel_agent import services
from stapel_agent.stt.base import (
    AudioRef,
    NormalizedTranscript,
    NormalizedWord,
    SttProvider,
    transcript_from_dict,
    unsupported_biasing,
)
from stapel_agent.stt.providers.assemblyai import AssemblyAIProvider
from stapel_agent.stt.providers.elevenlabs import ElevenLabsProvider
from stapel_agent.stt.providers.whisper_http import WhisperHttpProvider
from stapel_agent.tests.test_stt_providers import (
    ASSEMBLY_DONE,
    ELEVENLABS_BODY,
    WHISPER_BODY,
    FakeResponse,
    mock_post,
)

TRANSCRIBE_URL = "/agent/api/v1/llm/transcribe"


# ─── ElevenLabs Scribe keyterms wiring ─────────────────────────────────


class TestElevenLabsKeyterms:
    @pytest.fixture
    def configured(self, settings, monkeypatch):
        settings.STAPEL_AGENT = {"ELEVENLABS_API_KEY": "xi-test"}

        class DownloadResp:
            content = b"mp3-bytes"

            def raise_for_status(self):
                pass

        monkeypatch.setattr("requests.get", lambda *a, **kw: DownloadResp())
        return settings

    def _run(self, monkeypatch, **kwargs):
        captured = []
        mock_post(monkeypatch, "elevenlabs", [FakeResponse(ELEVENLABS_BODY)], captured)
        transcript = ElevenLabsProvider().transcribe(
            audio=AudioRef(url="https://cdn.test/a.mp3"), **kwargs
        )
        return transcript, captured

    def test_keyterms_sent_as_multipart_list(self, configured, monkeypatch):
        transcript, captured = self._run(
            monkeypatch, keyterms=["IronMemo", "stapel core"]
        )
        # A list value → repeated `keyterms` form fields under requests.
        assert captured[0]["data"]["keyterms"] == ["IronMemo", "stapel core"]
        assert transcript.biasing == {
            "applied": True,
            "terms_sent": 2,
            "terms_truncated": 0,
        }

    def test_documented_limits_truncate_not_error(self, configured, monkeypatch):
        transcript, captured = self._run(
            monkeypatch,
            keyterms=[
                "ok",
                "x" * 50,  # >= 50 chars
                "one two three four five six",  # > 5 words
                "bad<term>",  # prohibited chars
            ],
        )
        assert captured[0]["data"]["keyterms"] == ["ok"]
        assert transcript.biasing == {
            "applied": True,
            "terms_sent": 1,
            "terms_truncated": 3,
        }

    def test_no_keyterms_means_no_field_and_no_biasing(self, configured, monkeypatch):
        transcript, captured = self._run(monkeypatch)
        assert "keyterms" not in captured[0]["data"]
        assert transcript.biasing is None

    def test_provider_options_win_over_adapter_params(self, configured, monkeypatch):
        _, captured = self._run(
            monkeypatch,
            provider_options={"model_id": "scribe_v1", "seed": "42"},
        )
        assert captured[0]["data"]["model_id"] == "scribe_v1"
        assert captured[0]["data"]["seed"] == "42"


# ─── AssemblyAI keyterms_prompt wiring ─────────────────────────────────


class TestAssemblyAIKeyterms:
    @pytest.fixture
    def configured(self, settings, monkeypatch):
        settings.STAPEL_AGENT = {"ASSEMBLYAI_API_KEY": "aai-test"}
        monkeypatch.setattr("time.sleep", lambda s: None)
        return settings

    def _run(self, monkeypatch, **kwargs):
        posted, polled = [], []
        mock_post(
            monkeypatch, "assemblyai", [FakeResponse({"id": "tr_1"})], posted
        )

        def fake_get(url, headers=None, timeout=None):
            polled.append(url)
            return FakeResponse(ASSEMBLY_DONE)

        monkeypatch.setattr(
            "stapel_agent.stt.providers.assemblyai.requests.get", fake_get
        )
        transcript = AssemblyAIProvider().transcribe(
            audio=AudioRef(url="https://cdn.test/a.mp3"), **kwargs
        )
        return transcript, posted

    def test_keyterms_sent_as_keyterms_prompt(self, configured, monkeypatch):
        transcript, posted = self._run(monkeypatch, keyterms=["IronMemo", "SSH"])
        body = posted[0]["json"]
        assert body["keyterms_prompt"] == ["IronMemo", "SSH"]
        # the legacy pair is gone from current docs — must never be sent
        assert "word_boost" not in body
        assert "boost_param" not in body
        assert transcript.biasing == {
            "applied": True,
            "terms_sent": 2,
            "terms_truncated": 0,
        }

    def test_phrase_word_limit_truncates(self, configured, monkeypatch):
        transcript, posted = self._run(
            monkeypatch,
            keyterms=["ok", "one two three four five six seven"],  # 7 words
        )
        assert posted[0]["json"]["keyterms_prompt"] == ["ok"]
        assert transcript.biasing == {
            "applied": True,
            "terms_sent": 1,
            "terms_truncated": 1,
        }

    def test_total_word_budget_truncates(self, configured, monkeypatch):
        # 500 two-word phrases = exactly the 1000-word budget; the next
        # phrase is over and must be truncated, not an error.
        phrases = [f"term {i}" for i in range(500)]
        transcript, posted = self._run(monkeypatch, keyterms=[*phrases, "overflow x"])
        assert posted[0]["json"]["keyterms_prompt"] == phrases
        assert transcript.biasing == {
            "applied": True,
            "terms_sent": 500,
            "terms_truncated": 1,
        }

    def test_provider_options_win_over_adapter_params(self, configured, monkeypatch):
        _, posted = self._run(
            monkeypatch,
            provider_options={
                "speech_model": "best",
                "custom_spelling": [{"from": ["iron memo"], "to": "IronMemo"}],
            },
        )
        body = posted[0]["json"]
        assert body["speech_model"] == "best"
        assert body["custom_spelling"] == [{"from": ["iron memo"], "to": "IronMemo"}]


# ─── Non-supporting adapter contract ───────────────────────────────────


class TestNonSupportingAdapter:
    @pytest.fixture
    def configured(self, settings):
        settings.STAPEL_AGENT = {"WHISPER_BASE_URL": "http://whisper:8000/v1"}
        return settings

    def test_whisper_reports_not_applied(self, configured, monkeypatch):
        captured = []
        mock_post(monkeypatch, "whisper_http", [FakeResponse(WHISPER_BODY)], captured)
        transcript = WhisperHttpProvider().transcribe(
            audio=AudioRef(data=b"x"), keyterms=["IronMemo", "SSH"]
        )
        assert transcript.biasing == {
            "applied": False,
            "terms_sent": 0,
            "terms_truncated": 2,
        }
        # the terms are NOT smuggled into the request either
        assert "IronMemo" not in json.dumps(captured[0]["data"])

    def test_whisper_provider_options_still_pass_through(
        self, configured, monkeypatch
    ):
        # NEVER silently drop provider_options — that's the point of the
        # seam even on adapters without keyterm support.
        captured = []
        mock_post(monkeypatch, "whisper_http", [FakeResponse(WHISPER_BODY)], captured)
        WhisperHttpProvider().transcribe(
            audio=AudioRef(data=b"x"),
            provider_options={"prompt": "IronMemo, SSH", "temperature": "0"},
        )
        assert captured[0]["data"]["prompt"] == "IronMemo, SSH"
        assert captured[0]["data"]["temperature"] == "0"

    def test_unsupported_biasing_helper(self):
        assert unsupported_biasing(None) is None
        assert unsupported_biasing([]) is None
        assert unsupported_biasing(["a", "b"]) == {
            "applied": False,
            "terms_sent": 0,
            "terms_truncated": 2,
        }


# ─── Transcript round-trip ─────────────────────────────────────────────


class TestBiasingRoundTrip:
    def test_to_dict_from_dict_preserves_biasing(self):
        t = NormalizedTranscript(
            provider="deepgram",
            language="en",
            duration_seconds=1.0,
            words=[NormalizedWord(text="hi", start=0.0, end=0.4)],
            biasing={"applied": True, "terms_sent": 3, "terms_truncated": 1},
        )
        back = transcript_from_dict(t.to_dict())
        assert back == t
        assert back.biasing == {
            "applied": True,
            "terms_sent": 3,
            "terms_truncated": 1,
        }

    def test_from_dict_tolerates_missing_biasing(self):
        t = transcript_from_dict({"provider": "x"})
        assert t.biasing is None


# ─── Service threading ─────────────────────────────────────────────────


@pytest.mark.django_db
class TestServiceThreading:
    def test_keyterms_and_options_reach_the_adapter(self, fake_stt):
        result = services.transcribe(
            AudioRef(url="https://x/a.mp3"),
            keyterms=["IronMemo"],
            provider_options={"beta_flag": True},
        )
        assert result["status"] == "ok"
        call = fake_stt.calls[0]
        assert call["keyterms"] == ["IronMemo"]
        assert call["provider_options"] == {"beta_flag": True}
        # the fake has no keyterm support → the generic contract applies
        assert result["transcript"]["biasing"] == {
            "applied": False,
            "terms_sent": 0,
            "terms_truncated": 1,
        }

    def test_seam_kwargs_not_passed_to_legacy_adapters_when_unused(self, settings):
        """An adapter written against the pre-seam signature keeps
        working for calls that don't use the seam."""
        from stapel_agent.stt import (
            _reset_runtime_stt_providers,
            register_stt_provider,
        )

        class LegacySttProvider(SttProvider):
            name = "legacy-stt"

            def transcribe(
                self, *, audio, language=None, diarization=False, timeout_seconds=None
            ):
                return NormalizedTranscript(
                    provider=self.name, language=None, duration_seconds=None
                )

        settings.STAPEL_AGENT = {"DEFAULT_STT_PROVIDER": "legacy-stt"}
        register_stt_provider("legacy-stt", LegacySttProvider)
        try:
            result = services.transcribe(AudioRef(url="https://x/a.mp3"))
            assert result["status"] == "ok"
            assert result["transcript"]["biasing"] is None
        finally:
            _reset_runtime_stt_providers()


# ─── comm function schema ──────────────────────────────────────────────


@pytest.mark.django_db
class TestTranscribeFunctionSchema:
    def test_keyterms_and_provider_options_accepted_and_threaded(self, fake_stt):
        from stapel_core.comm import call

        result = call(
            "llm.transcribe",
            {
                "audio_url": "https://minio.test/rec.mp3",
                "keyterms": ["IronMemo", "stapel"],
                "provider_options": {"any_provider_key": {"nested": 1}},
            },
        )
        assert result["status"] == "ok"
        call_rec = fake_stt.calls[0]
        assert call_rec["keyterms"] == ["IronMemo", "stapel"]
        assert call_rec["provider_options"] == {"any_provider_key": {"nested": 1}}
        assert result["transcript"]["biasing"]["applied"] is False

    def test_schema_rejects_non_string_keyterms(self, fake_stt):
        from stapel_core.comm import call
        from stapel_core.comm.exceptions import SchemaValidationError

        with pytest.raises(SchemaValidationError):
            call(
                "llm.transcribe",
                {"audio_url": "https://x/a", "keyterms": [1, 2]},
            )

    def test_schema_rejects_non_object_provider_options(self, fake_stt):
        from stapel_core.comm import call
        from stapel_core.comm.exceptions import SchemaValidationError

        with pytest.raises(SchemaValidationError):
            call(
                "llm.transcribe",
                {"audio_url": "https://x/a", "provider_options": "model=x"},
            )

    def test_top_level_stays_closed(self, fake_stt):
        # additionalProperties: false at the top level — the free-form
        # zone is INSIDE provider_options only.
        from stapel_core.comm import call
        from stapel_core.comm.exceptions import SchemaValidationError

        with pytest.raises(SchemaValidationError):
            call(
                "llm.transcribe",
                {"audio_url": "https://x/a", "keyterm": ["typo-key"]},
            )


# ─── HTTP surface ──────────────────────────────────────────────────────


@pytest.mark.django_db
class TestTranscribeHttpSurface:
    def test_keyterms_and_provider_options_threaded(self, api_client, fake_stt):
        resp = api_client.post(
            TRANSCRIBE_URL,
            {
                "audio_url": "https://minio.test/rec.mp3",
                "keyterms": ["IronMemo"],
                "provider_options": {"beta_flag": True},
            },
            format="json",
            HTTP_X_API_KEY="test-service-key",
        )
        assert resp.status_code == 200, resp.content
        call = fake_stt.calls[0]
        assert call["keyterms"] == ["IronMemo"]
        assert call["provider_options"] == {"beta_flag": True}
        assert resp.json()["transcript"]["biasing"] == {
            "applied": False,
            "terms_sent": 0,
            "terms_truncated": 1,
        }


# ─── Catalog surface ───────────────────────────────────────────────────


@pytest.mark.django_db
class TestCatalogSupportsKeyterms:
    def test_supports_keyterms_listed_per_provider(self, settings):
        settings.STAPEL_AGENT = {}
        entries = {p["name"]: p for p in services.stt_catalog()["providers"]}
        assert entries["deepgram"]["supports_keyterms"] is True
        assert entries["elevenlabs"]["supports_keyterms"] is True
        assert entries["assemblyai"]["supports_keyterms"] is True
        assert entries["xai-stt"]["supports_keyterms"] is True
        assert entries["whisper-http"]["supports_keyterms"] is False
        assert entries["gladia"]["supports_keyterms"] is False
        assert entries["soniox"]["supports_keyterms"] is False
        assert entries["speechmatics"]["supports_keyterms"] is False


# ─── Privacy invariant ─────────────────────────────────────────────────


class TestBiasingPrivacy:
    def test_biasing_block_never_contains_terms(self, settings, monkeypatch):
        """The biasing block is counts only — run every keyterm-wired
        adapter with distinctive terms and assert none leak into it."""
        from stapel_agent.stt.providers.deepgram import DeepgramProvider
        from stapel_agent.stt.providers.xai_stt import XaiSttProvider
        from stapel_agent.tests.test_stt_providers_ported import (
            DEEPGRAM_BODY,
            XAI_BODY,
            mock_http,
        )

        settings.STAPEL_AGENT = {
            "DEEPGRAM_API_KEY": "dg",
            "XAI_API_KEY": "xai",
        }
        terms = ["SECRET-CLIENT-TERM", "ACME Corp"]

        # requests is one shared module — patch per provider, sequentially.
        for provider, module, body in (
            (DeepgramProvider(), "deepgram", DEEPGRAM_BODY),
            (XaiSttProvider(), "xai_stt", XAI_BODY),
        ):
            mock_http(monkeypatch, module, post=[FakeResponse(body)])
            transcript = provider.transcribe(
                audio=AudioRef(data=b"x"), keyterms=list(terms)
            )
            blob = json.dumps(transcript.to_dict()["biasing"])
            for term in terms:
                assert term not in blob
            assert set(transcript.biasing) == {
                "applied",
                "terms_sent",
                "terms_truncated",
            }
            assert all(
                isinstance(v, (bool, int)) for v in transcript.biasing.values()
            )
