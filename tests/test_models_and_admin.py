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
        assert set(agent_settings.PROVIDERS) == {
            "anthropic",
            "openai-compat",
            "claude-code",
        }
        assert agent_settings.CLI_BINARY == "claude"
        assert agent_settings.CLI_TIMEOUT == 120
        assert agent_settings.MAX_TOKENS == 4096
        assert agent_settings.CACHE_LOOKUP == {"llm_facade": False, "translate": True}
        assert agent_settings.CACHE_TTL == 604800

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
