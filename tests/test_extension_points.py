"""Tests for the fork-free extension points themselves: provider-registry
merge semantics, runtime registration, system checks, cache-policy seam."""
import pytest

from stapel_agent import services
from stapel_agent.checks import check_providers
from stapel_agent.providers import (
    BUILTIN_PROVIDERS,
    _reset_runtime_providers,
    register_provider,
    registered_providers,
)
from stapel_agent.providers.base import ProviderError, ProviderResult
from stapel_agent.tests.fakes import (
    CustomProvider,
    FakeProvider,
    NotAProvider,
    RecordingCachePolicy,
)

FAKE_PATH = "stapel_agent.tests.fakes.FakeProvider"
CUSTOM_PATH = "stapel_agent.tests.fakes.CustomProvider"


@pytest.fixture(autouse=True)
def clean_runtime_registry():
    _reset_runtime_providers()
    yield
    _reset_runtime_providers()


class TestSettingsMerge:
    def test_settings_entries_merge_over_builtins(self, settings):
        settings.STAPEL_AGENT = {"PROVIDERS": {"custom": CUSTOM_PATH}}
        effective = registered_providers()
        # the custom entry is added ...
        assert effective["custom"] == CUSTOM_PATH
        # ... WITHOUT restating the built-ins — they are all still there
        for name, path in BUILTIN_PROVIDERS.items():
            assert effective[name] == path

    def test_builtins_still_resolvable_alongside_custom(self, settings):
        settings.STAPEL_AGENT = {"PROVIDERS": {"custom": CUSTOM_PATH}}
        from stapel_agent.providers.anthropic import AnthropicProvider

        assert isinstance(services.get_provider("custom"), CustomProvider)
        assert isinstance(services.get_provider("anthropic"), AnthropicProvider)

    def test_settings_entry_overrides_builtin(self, settings):
        settings.STAPEL_AGENT = {"PROVIDERS": {"anthropic": FAKE_PATH}}
        assert isinstance(services.get_provider("anthropic"), FakeProvider)

    def test_none_removes_a_builtin(self, settings):
        settings.STAPEL_AGENT = {"PROVIDERS": {"claude-code": None}}
        assert "claude-code" not in registered_providers()
        with pytest.raises(ProviderError, match="claude-code"):
            services.get_provider("claude-code")

    def test_empty_string_removes_too(self, settings):
        settings.STAPEL_AGENT = {"PROVIDERS": {"claude-code": ""}}
        assert "claude-code" not in registered_providers()

    @pytest.mark.django_db
    def test_removed_provider_degrades_to_failure_response(
        self, settings, api_client
    ):
        settings.STAPEL_AGENT = {"PROVIDERS": {"claude-code": None}}
        resp = api_client.post(
            "/agent/api/v1/llm/complete",
            {"prompt": "x", "model": "small", "provider": "claude-code"},
            format="json",
            HTTP_X_API_KEY="test-service-key",
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "failure"
        assert "claude-code" in data["reason"]


class TestRegisterProvider:
    def test_register_class(self):
        register_provider("custom", CustomProvider)
        assert registered_providers()["custom"] is CustomProvider
        assert isinstance(services.get_provider("custom"), CustomProvider)

    def test_register_dotted_path(self):
        register_provider("custom", CUSTOM_PATH)
        assert registered_providers()["custom"] == CUSTOM_PATH
        assert isinstance(services.get_provider("custom"), CustomProvider)

    def test_runtime_beats_settings_merge(self, settings):
        settings.STAPEL_AGENT = {"PROVIDERS": {"custom": FAKE_PATH}}
        register_provider("custom", CustomProvider)
        assert registered_providers()["custom"] is CustomProvider

    def test_register_none_masks_a_builtin(self):
        register_provider("claude-code", None)
        assert "claude-code" not in registered_providers()

    def test_reregistering_overrides(self):
        register_provider("custom", FakeProvider)
        register_provider("custom", CustomProvider)
        assert registered_providers()["custom"] is CustomProvider

    def test_rejects_non_provider(self):
        with pytest.raises(TypeError, match="LlmProvider subclass"):
            register_provider("bad", NotAProvider)

    def test_rejects_instances(self):
        with pytest.raises(TypeError):
            register_provider("bad", CustomProvider())

    @pytest.mark.django_db
    def test_runtime_provider_usable_end_to_end(self):
        register_provider("custom", CustomProvider)
        CustomProvider.reset()
        result = services.complete("hi", "small", provider="custom", source="other")
        assert result["status"] == "ok"
        assert CustomProvider.calls[0]["prompt"] == "hi"


class TestSystemChecks:
    def test_clean_default_config(self, settings):
        # "Clean" now requires a CONFIGURED default. This test used to
        # assert [] against stock settings — i.e. against a config that
        # could not serve a single call (anthropic, no key), which is
        # exactly the blindness W009 ends.
        # openai-compat rather than anthropic-with-a-key: the anthropic
        # backend also needs an optional package, and a library suite must
        # not depend on whether that extra is installed in the venv running
        # it.
        settings.STAPEL_AGENT = {
            "DEFAULT_PROVIDER": "openai-compat",
            "OPENAI_COMPAT_BASE_URL": "http://vllm:8000/v1",
        }
        assert check_providers(None) == []

    def test_default_provider_without_credentials_warns(self, settings):
        """Registered is not usable — the ironmemo stand, 2026-07-26.

        DEFAULT_PROVIDER='anthropic' with an empty key passed every check
        while every llm.summarize call raised ProviderError, invisibly:
        stapel-recordings' summarize step is best-effort, so recordings
        completed with empty summaries and nothing reported why.
        """
        settings.STAPEL_AGENT = {"ANTHROPIC_API_KEY": ""}
        issues = check_providers(None)
        assert [i.id for i in issues] == ["stapel_agent.W009"]
        assert "ANTHROPIC_API_KEY is empty" in issues[0].msg
        # The hint must name the silent consequence, not just the misconfig.
        assert "empty summaries" in issues[0].hint

    def test_openai_compat_without_a_base_url_warns(self, settings):
        settings.STAPEL_AGENT = {
            "DEFAULT_PROVIDER": "openai-compat",
            "OPENAI_COMPAT_BASE_URL": "",
        }
        assert [i.id for i in check_providers(None)] == ["stapel_agent.W009"]

    def test_openai_compat_needs_no_api_key(self, settings):
        """A self-hosted endpoint (vLLM/Ollama/TEI) legitimately has none."""
        settings.STAPEL_AGENT = {
            "DEFAULT_PROVIDER": "openai-compat",
            "OPENAI_COMPAT_BASE_URL": "http://vllm:8000/v1",
            "OPENAI_COMPAT_API_KEY": "",
        }
        assert check_providers(None) == []

    def test_a_non_default_providers_credentials_are_not_checked(self, settings):
        """Only the default is probed — an unconfigured alternative is not
        a defect, it is simply not in use."""
        settings.STAPEL_AGENT = {
            "DEFAULT_PROVIDER": "openai-compat",
            "OPENAI_COMPAT_BASE_URL": "http://vllm:8000/v1",
            "ANTHROPIC_API_KEY": "",  # registered, unconfigured, unused
        }
        assert check_providers(None) == []

    def test_bad_default_provider_is_error(self, settings):
        settings.STAPEL_AGENT = {"DEFAULT_PROVIDER": "ghost"}
        issues = check_providers(None)
        assert [i.id for i in issues] == ["stapel_agent.E001"]
        assert "ghost" in issues[0].msg

    def test_removing_the_default_provider_is_error(self, settings):
        settings.STAPEL_AGENT = {"PROVIDERS": {"anthropic": None}}
        issues = check_providers(None)
        assert "stapel_agent.E001" in [i.id for i in issues]

    def test_unimportable_dotted_path_is_warning(self, settings):
        settings.STAPEL_AGENT = {
            # A usable default, so this test measures W001 and not the
            # W009 "default provider unusable" warning added alongside it.
            "DEFAULT_PROVIDER": "openai-compat",
            "OPENAI_COMPAT_BASE_URL": "http://vllm:8000/v1",
            "PROVIDERS": {"broken": "no.such.module.Cls"},
        }
        issues = check_providers(None)
        assert [i.id for i in issues] == ["stapel_agent.W001"]
        assert "broken" in issues[0].msg

    def test_non_provider_class_is_warning(self, settings):
        settings.STAPEL_AGENT = {
            "DEFAULT_PROVIDER": "openai-compat",
            "OPENAI_COMPAT_BASE_URL": "http://vllm:8000/v1",
            "PROVIDERS": {"bad": "stapel_agent.tests.fakes.NotAProvider"},
        }
        issues = check_providers(None)
        assert [i.id for i in issues] == ["stapel_agent.W002"]
        assert "bad" in issues[0].msg

    def test_runtime_registered_class_passes(self, settings):
        settings.STAPEL_AGENT = {
            "DEFAULT_PROVIDER": "openai-compat",
            "OPENAI_COMPAT_BASE_URL": "http://vllm:8000/v1",
            }
        register_provider("custom", CustomProvider)
        assert check_providers(None) == []

    def test_registered_with_django(self):
        from django.core.checks.registry import registry

        assert check_providers in registry.registered_checks


@pytest.mark.django_db
class TestCachePolicySeam:
    @pytest.fixture
    def custom_cache(self, settings):
        settings.STAPEL_AGENT = {
            "PROVIDERS": {"fake": FAKE_PATH},
            "DEFAULT_PROVIDER": "fake",
            "CACHE_POLICY": "stapel_agent.tests.fakes.RecordingCachePolicy",
        }
        FakeProvider.reset()
        RecordingCachePolicy.reset()
        yield RecordingCachePolicy
        RecordingCachePolicy.reset()
        FakeProvider.reset()

    def test_custom_policy_is_used_for_lookup_and_store(self, custom_cache):
        services.complete("p", "small", source="llm_facade")
        # llm_facade is cached because the custom policy says so —
        # CACHE_LOOKUP no longer applies once the policy is swapped. The
        # key now carries provider + resolved model + size.
        assert custom_cache.lookups == [
            ("p", None, "llm_facade", "fake", "claude-haiku-4-5-20251001", "small")
        ]
        assert custom_cache.stores == [
            (
                "p",
                None,
                "llm_facade",
                '{"answer": 42}',
                "fake",
                "claude-haiku-4-5-20251001",
                "small",
            )
        ]

    def test_custom_policy_hit_skips_provider(self, custom_cache):
        custom_cache.entries[("p", None, "llm_facade")] = '{"cached": true}'
        result = services.complete("p", "small", source="llm_facade")
        assert result["status"] == "ok"
        assert result["result"] == '{"cached": true}'
        assert FakeProvider.calls == []

    def test_custom_policy_should_cache_false_bypasses(self, custom_cache):
        custom_cache.cache_all = False
        services.complete("p", "small", source="llm_facade")
        services.complete("p", "small", source="llm_facade")
        assert custom_cache.lookups == []
        assert len(FakeProvider.calls) == 2

    def test_custom_policy_serves_translate(self, custom_cache):
        FakeProvider.result = ProviderResult(text='{"k": "Hallo"}')
        first = services.translate("en", "de", {"k": "Hello"})
        second = services.translate("en", "de", {"k": "Hello"})
        assert first == second == {"status": "ok", "result": {"k": "Hallo"}}
        # second call answered by the recording policy, not the provider
        assert len(FakeProvider.calls) == 1
        assert len(custom_cache.stores) == 1

    def test_default_policy_is_promptlog(self):
        from stapel_agent.cache import PromptLogCachePolicy
        from stapel_agent.services import _cache_policy

        assert isinstance(_cache_policy(), PromptLogCachePolicy)
