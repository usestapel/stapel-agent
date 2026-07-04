"""STT extension-point tests: registry merge semantics, runtime
registration, and the W003/W004 system checks — mirrors the LLM-registry
suite in test_extension_points.py."""
import pytest

from stapel_agent import services
from stapel_agent.checks import check_stt_providers
from stapel_agent.stt import (
    BUILTIN_STT_PROVIDERS,
    _reset_runtime_stt_providers,
    register_stt_provider,
    registered_stt_providers,
)
from stapel_agent.stt.base import TranscriptionError
from stapel_agent.stt.providers.whisper_http import WhisperHttpProvider
from stapel_agent.tests.fakes import (
    FakeSttProvider,
    NotAnSttProvider,
    SecondSttProvider,
)

FAKE_PATH = "stapel_agent.tests.fakes.FakeSttProvider"
SECOND_PATH = "stapel_agent.tests.fakes.SecondSttProvider"


@pytest.fixture(autouse=True)
def clean_runtime_registry():
    _reset_runtime_stt_providers()
    yield
    _reset_runtime_stt_providers()


class TestSettingsMerge:
    def test_settings_entries_merge_over_builtins(self, settings):
        settings.STAPEL_AGENT = {"STT_PROVIDERS": {"fake-stt": FAKE_PATH}}
        effective = registered_stt_providers()
        # the custom entry is added ...
        assert effective["fake-stt"] == FAKE_PATH
        # ... WITHOUT restating the built-ins — they are all still there
        for name, path in BUILTIN_STT_PROVIDERS.items():
            assert effective[name] == path

    def test_builtins_still_resolvable_alongside_custom(self, settings):
        settings.STAPEL_AGENT = {"STT_PROVIDERS": {"fake-stt": FAKE_PATH}}
        assert isinstance(services.get_stt_provider("fake-stt"), FakeSttProvider)
        assert isinstance(
            services.get_stt_provider("whisper-http"), WhisperHttpProvider
        )

    def test_settings_entry_overrides_builtin(self, settings):
        settings.STAPEL_AGENT = {"STT_PROVIDERS": {"elevenlabs": FAKE_PATH}}
        assert isinstance(services.get_stt_provider("elevenlabs"), FakeSttProvider)

    def test_none_removes_a_builtin(self, settings):
        settings.STAPEL_AGENT = {"STT_PROVIDERS": {"assemblyai": None}}
        assert "assemblyai" not in registered_stt_providers()
        with pytest.raises(TranscriptionError, match="assemblyai"):
            services.get_stt_provider("assemblyai")

    def test_empty_string_removes_too(self, settings):
        settings.STAPEL_AGENT = {"STT_PROVIDERS": {"assemblyai": ""}}
        assert "assemblyai" not in registered_stt_providers()


class TestRegisterSttProvider:
    def test_register_class(self):
        register_stt_provider("fake-stt", FakeSttProvider)
        assert registered_stt_providers()["fake-stt"] is FakeSttProvider
        assert isinstance(services.get_stt_provider("fake-stt"), FakeSttProvider)

    def test_register_dotted_path(self):
        register_stt_provider("fake-stt", FAKE_PATH)
        assert registered_stt_providers()["fake-stt"] == FAKE_PATH
        assert isinstance(services.get_stt_provider("fake-stt"), FakeSttProvider)

    def test_runtime_beats_settings_merge(self, settings):
        settings.STAPEL_AGENT = {"STT_PROVIDERS": {"gigaam": FAKE_PATH}}
        register_stt_provider("gigaam", SecondSttProvider)
        assert registered_stt_providers()["gigaam"] is SecondSttProvider

    def test_register_none_masks_a_builtin(self):
        register_stt_provider("elevenlabs", None)
        assert "elevenlabs" not in registered_stt_providers()

    def test_reregistering_overrides(self):
        register_stt_provider("gigaam", FakeSttProvider)
        register_stt_provider("gigaam", SecondSttProvider)
        assert registered_stt_providers()["gigaam"] is SecondSttProvider

    def test_rejects_non_provider(self):
        with pytest.raises(TypeError, match="SttProvider subclass"):
            register_stt_provider("bad", NotAnSttProvider)

    def test_rejects_instances(self):
        with pytest.raises(TypeError):
            register_stt_provider("bad", FakeSttProvider())

    @pytest.mark.django_db
    def test_runtime_provider_usable_end_to_end(self, settings):
        from stapel_agent.stt.base import AudioRef

        settings.STAPEL_AGENT = {"DEFAULT_STT_PROVIDER": "gigaam"}
        register_stt_provider("gigaam", FakeSttProvider)
        FakeSttProvider.reset()
        result = services.transcribe(AudioRef(url="https://x/a.mp3"))
        assert result["status"] == "ok"
        assert result["provider_used"] == "gigaam"
        FakeSttProvider.reset()


class TestSttSystemChecks:
    def test_clean_default_config(self):
        assert check_stt_providers(None) == []

    def test_unimportable_dotted_path_is_w003(self, settings):
        settings.STAPEL_AGENT = {"STT_PROVIDERS": {"broken": "no.such.module.Cls"}}
        issues = check_stt_providers(None)
        assert [i.id for i in issues] == ["stapel_agent.W003"]
        assert "broken" in issues[0].msg

    def test_non_stt_provider_class_is_w003(self, settings):
        settings.STAPEL_AGENT = {
            "STT_PROVIDERS": {"bad": "stapel_agent.tests.fakes.NotAnSttProvider"}
        }
        issues = check_stt_providers(None)
        assert [i.id for i in issues] == ["stapel_agent.W003"]
        assert "bad" in issues[0].msg

    def test_llm_provider_class_is_w003_here_too(self, settings):
        # An LlmProvider is not an SttProvider — wrong seam, flagged.
        settings.STAPEL_AGENT = {
            "STT_PROVIDERS": {"bad": "stapel_agent.tests.fakes.FakeProvider"}
        }
        assert [i.id for i in check_stt_providers(None)] == ["stapel_agent.W003"]

    def test_unknown_default_is_w004(self, settings):
        settings.STAPEL_AGENT = {"DEFAULT_STT_PROVIDER": "ghost"}
        issues = check_stt_providers(None)
        assert [i.id for i in issues] == ["stapel_agent.W004"]
        assert "ghost" in issues[0].msg

    def test_removing_the_default_is_w004(self, settings):
        settings.STAPEL_AGENT = {"STT_PROVIDERS": {"whisper-http": None}}
        assert "stapel_agent.W004" in [i.id for i in check_stt_providers(None)]

    def test_unknown_fallback_chain_entry_is_w004(self, settings):
        settings.STAPEL_AGENT = {"STT_FALLBACK_CHAIN": ["elevenlabs", "ghost"]}
        issues = check_stt_providers(None)
        assert [i.id for i in issues] == ["stapel_agent.W004"]
        assert "STT_FALLBACK_CHAIN" in issues[0].msg

    def test_unknown_language_route_entry_is_w004(self, settings):
        settings.STAPEL_AGENT = {
            "STT_LANGUAGE_ROUTES": {"ru": ["gigaam"], "en": ["whisper-http"]}
        }
        issues = check_stt_providers(None)
        assert [i.id for i in issues] == ["stapel_agent.W004"]
        assert "'ru'" in issues[0].msg and "gigaam" in issues[0].msg

    def test_runtime_registered_class_passes(self):
        register_stt_provider("gigaam", FakeSttProvider)
        assert check_stt_providers(None) == []

    def test_registered_with_django(self):
        from django.core.checks.registry import registry

        assert check_stt_providers in registry.registered_checks
