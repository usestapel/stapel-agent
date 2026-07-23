"""Model, admin and conf smoke tests."""
import pytest
from django.contrib import admin

from stapel_agent.conf import agent_settings
from stapel_agent.models import PromptLog, PromptSource, PromptStatus


@pytest.mark.django_db
class TestPromptLogModel:
    def test_str(self):
        log = PromptLog.objects.create(
            source=PromptSource.TRANSLATE,
            model="claude-sonnet-5",
            model_size="medium",
            prompt="p",
            status=PromptStatus.SUCCESS,
        )
        assert str(log) == "translate/medium [success] claude-sonnet-5"

    def test_ordering_is_newest_first(self):
        old = PromptLog.objects.create(
            source=PromptSource.OTHER, model="m", model_size="small",
            prompt="1", status=PromptStatus.SUCCESS,
        )
        new = PromptLog.objects.create(
            source=PromptSource.OTHER, model="m", model_size="small",
            prompt="2", status=PromptStatus.SUCCESS,
        )
        assert list(PromptLog.objects.all()) == [new, old]


class TestAdmin:
    def test_registered_read_only(self):
        model_admin = admin.site._registry[PromptLog]
        assert model_admin.has_add_permission(None) is False
        assert model_admin.has_change_permission(None) is False
        assert model_admin.has_delete_permission(None) is False


class TestSerializerSeam:
    def test_seam_getters(self):
        from stapel_agent.serializers import CompleteRequestSerializer
        from stapel_agent.views import LlmCompleteView

        view = LlmCompleteView()
        assert view.get_request_serializer_class() is CompleteRequestSerializer
        assert view.get_response_serializer_class() is None


class TestConfDefaults:
    def test_defaults(self):
        assert agent_settings.DEFAULT_PROVIDER == "anthropic"
        assert agent_settings.MODELS["small"] == "claude-haiku-4-5-20251001"
        assert agent_settings.MODELS["medium"] == "claude-sonnet-5"
        assert agent_settings.MODELS["large"] == "claude-opus-4-8"
        # PROVIDERS is an overlay (merged over the built-ins) — empty by
        # default; the effective registry is the built-ins.
        assert agent_settings.PROVIDERS == {}
        from stapel_agent.providers import BUILTIN_PROVIDERS, registered_providers

        assert registered_providers() == BUILTIN_PROVIDERS
        assert set(BUILTIN_PROVIDERS) == {
            "anthropic",
            "openai-compat",
            "claude-code",
        }
        assert agent_settings.CLI_BINARY == "claude"
        assert agent_settings.CLI_TIMEOUT == 120
        assert agent_settings.MAX_TOKENS == 4096
        assert agent_settings.CACHE_LOOKUP == {
            "llm_facade": False,
            "translate": True,
            "summarize": False,
        }
        assert agent_settings.CACHE_TTL == 604800
        from stapel_agent.cache import PromptLogCachePolicy

        assert agent_settings.CACHE_POLICY is PromptLogCachePolicy
        # STT defaults
        from stapel_agent.stt import BUILTIN_STT_PROVIDERS, registered_stt_providers

        assert agent_settings.STT_PROVIDERS == {}
        assert registered_stt_providers() == BUILTIN_STT_PROVIDERS
        assert set(BUILTIN_STT_PROVIDERS) == {
            "whisper-http",
            "elevenlabs",
            "assemblyai",
            "deepgram",
            "gladia",
            "soniox",
            "speechmatics",
            "xai-stt",
        }
        assert agent_settings.DEFAULT_STT_PROVIDER == "whisper-http"
        assert agent_settings.STT_FALLBACK_CHAIN == []
        assert agent_settings.STT_LANGUAGE_ROUTES == {}
        assert agent_settings.STT_TIMEOUT == 1800
        assert agent_settings.WHISPER_MODEL == "whisper-1"
        assert agent_settings.ASSEMBLYAI_MODEL == "universal"
        # Image-generation defaults
        from stapel_agent.images import (
            BUILTIN_IMAGE_PROVIDERS,
            registered_image_providers,
        )

        assert agent_settings.IMAGE_PROVIDERS == {}
        assert registered_image_providers() == BUILTIN_IMAGE_PROVIDERS
        assert set(BUILTIN_IMAGE_PROVIDERS) == {"openai-images"}
        assert agent_settings.DEFAULT_IMAGE_PROVIDER == "openai-images"
        assert agent_settings.IMAGES_BASE_URL == ""
        assert agent_settings.IMAGES_API_KEY == ""
        assert agent_settings.IMAGES_MODEL == ""

    def test_namespace_override(self, settings):
        settings.STAPEL_AGENT = {"DEFAULT_PROVIDER": "openai-compat"}
        assert agent_settings.DEFAULT_PROVIDER == "openai-compat"
        # untouched keys keep their defaults
        assert agent_settings.CLI_BINARY == "claude"

    def test_error_keys_registered(self):
        from stapel_agent.errors import AGENT_ERRORS, AgentErrorKeysView

        assert AgentErrorKeysView().get_service_errors() is AGENT_ERRORS
        assert "error.400.invalid_model_size" in AGENT_ERRORS

    def test_default_providers_resolve(self):
        from stapel_agent.providers.anthropic import AnthropicProvider
        from stapel_agent.services import get_provider

        assert isinstance(get_provider("anthropic"), AnthropicProvider)
