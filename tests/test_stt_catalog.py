"""G7 — the ``llm.stt_catalog`` surface: the STT service-layer catalogue
and its comm verb. Mirrors the committed contract in
schemas/functions/llm.stt_catalog.json."""
import pytest
from stapel_core.comm import call, function_registry
from stapel_core.comm.exceptions import SchemaValidationError

from stapel_agent import services
from stapel_agent.stt import _reset_runtime_stt_providers, register_stt_provider


@pytest.fixture(autouse=True)
def clean_runtime_registry():
    _reset_runtime_stt_providers()
    yield
    _reset_runtime_stt_providers()


def _by_name(catalog):
    return {p["name"]: p for p in catalog["providers"]}


class TestSttCatalogService:
    def test_lists_the_builtins_with_routing_config(self, settings):
        settings.STAPEL_AGENT = {
            "DEFAULT_STT_PROVIDER": "elevenlabs",
            "STT_FALLBACK_CHAIN": ["assemblyai"],
            "STT_LANGUAGE_ROUTES": {"ru": ["whisper-http"]},
        }
        catalog = services.stt_catalog()
        assert catalog["status"] == "ok"
        names = _by_name(catalog)
        # all three built-ins are addressable
        assert {"whisper-http", "elevenlabs", "assemblyai"} <= set(names)
        # routing config is echoed back
        assert catalog["default_provider"] == "elevenlabs"
        assert catalog["fallback_chain"] == ["assemblyai"]
        assert catalog["language_routes"] == {"ru": ["whisper-http"]}

    def test_entry_shape_and_effective_model_from_settings(self, settings):
        settings.STAPEL_AGENT = {"ELEVENLABS_STT_MODEL": "scribe_v3"}
        entry = _by_name(services.stt_catalog())["elevenlabs"]
        assert entry["available"] is True
        assert entry["model"] == "scribe_v3"  # configured default, no pin
        assert entry["pinned_model"] is False
        assert entry["supports_diarization"] is True
        assert entry["cost_per_hour"] == 0.40

    def test_pinned_registration_surfaces_pin(self, settings):
        from stapel_agent.tests.fakes import PinnedSttProvider

        register_stt_provider("pinned-stt", PinnedSttProvider)
        entry = _by_name(services.stt_catalog())["pinned-stt"]
        assert entry["available"] is True
        assert entry["model"] == "pinned-model-x"  # the pin, not the default
        assert entry["pinned_model"] is True
        assert entry["supported_languages"] == ["en", "ru"]  # sorted
        assert entry["cost_per_hour"] == 0.10

    def test_any_languages_is_null(self, settings):
        # supported_languages None ("any") serializes as null, not []
        assert _by_name(services.stt_catalog())["whisper-http"][
            "supported_languages"
        ] is None

    def test_unresolvable_entry_is_listed_unavailable(self, settings):
        settings.STAPEL_AGENT = {"STT_PROVIDERS": {"broken": "no.such.module.Cls"}}
        entry = _by_name(services.stt_catalog())["broken"]
        assert entry["available"] is False
        assert "error" in entry
        # a bad entry must not sink the whole catalogue
        assert _by_name(services.stt_catalog())["whisper-http"]["available"] is True

    def test_none_masked_builtin_absent(self, settings):
        settings.STAPEL_AGENT = {"STT_PROVIDERS": {"assemblyai": None}}
        assert "assemblyai" not in _by_name(services.stt_catalog())


@pytest.mark.django_db
class TestSttCatalogVerb:
    def test_registered(self):
        assert "llm.stt_catalog" in function_registry.names()

    def test_happy_path_empty_payload(self):
        result = call("llm.stt_catalog", {})
        assert result["status"] == "ok"
        assert any(p["name"] == "whisper-http" for p in result["providers"])

    def test_writes_no_promptlog_row(self):
        from stapel_agent.models import PromptLog

        before = PromptLog.objects.count()
        call("llm.stt_catalog", {})
        assert PromptLog.objects.count() == before

    def test_schema_rejects_extra_keys(self):
        with pytest.raises(SchemaValidationError):
            call("llm.stt_catalog", {"provider": "whisper-http"})
