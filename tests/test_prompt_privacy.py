"""AGENT-02: the prompt ledger is customer content, and the cache is keyed
by whose content it is.

Two halves of one finding:

* the cache used to be keyed on prompt text alone, so two tenants sending
  the same sensitive prompt shared one stored response;
* PromptLog stored prompt, system prompt and full response in plaintext
  with no retention job and no subject-request path.
"""
import pytest
from django.utils import timezone

from stapel_agent import services
from stapel_agent.gdpr import AgentGDPRProvider
from stapel_agent.models import PromptLog, PromptSource, PromptStatus
from stapel_agent.providers.base import ProviderResult
from stapel_agent.retention import purge_prompt_logs


@pytest.fixture
def cached_facade(settings):
    from stapel_agent.tests.fakes import FakeProvider

    settings.STAPEL_AGENT = {
        "PROVIDERS": {"fake": "stapel_agent.tests.fakes.FakeProvider"},
        "DEFAULT_PROVIDER": "fake",
        "CACHE_LOOKUP": {"llm_facade": True, "translate": True},
    }
    FakeProvider.reset()
    yield FakeProvider
    FakeProvider.reset()


@pytest.mark.django_db
class TestCacheIsTenantScoped:
    def test_another_tenants_answer_is_never_served(self, cached_facade):
        cached_facade.result = ProviderResult(text="tenant-a-secret-answer")
        services.complete(
            "what is our runway?", "small", source=PromptSource.LLM_FACADE,
            user_id="tenant-a",
        )
        cached_facade.result = ProviderResult(text="tenant-b-own-answer")
        second = services.complete(
            "what is our runway?", "small", source=PromptSource.LLM_FACADE,
            user_id="tenant-b",
        )
        assert second["result"] == "tenant-b-own-answer"
        assert len(cached_facade.calls) == 2

    def test_same_tenant_still_hits(self, cached_facade):
        services.complete(
            "p", "small", source=PromptSource.LLM_FACADE, user_id="tenant-a"
        )
        services.complete(
            "p", "small", source=PromptSource.LLM_FACADE, user_id="tenant-a"
        )
        assert len(cached_facade.calls) == 1

    def test_unscoped_call_does_not_use_the_cache_at_all(self, cached_facade):
        """Fail closed: no scope means no sharing, in either direction."""
        services.complete("p", "small", source=PromptSource.LLM_FACADE)
        services.complete("p", "small", source=PromptSource.LLM_FACADE)
        assert len(cached_facade.calls) == 2
        assert PromptLog.objects.count() == 2

    def test_a_scoped_call_cannot_read_an_unscoped_row(self, cached_facade):
        services.complete("p", "small", source=PromptSource.LLM_FACADE)
        services.complete(
            "p", "small", source=PromptSource.LLM_FACADE, user_id="tenant-a"
        )
        assert len(cached_facade.calls) == 2

    def test_host_can_opt_a_source_into_the_shared_cache(
        self, cached_facade, settings
    ):
        """The escape hatch for content the host declares non-personal —
        opt-in per source, never the default."""
        settings.STAPEL_AGENT = {
            **settings.STAPEL_AGENT,
            "CACHE_ALLOW_UNSCOPED": ["llm_facade"],
        }
        services.complete("p", "small", source=PromptSource.LLM_FACADE)
        services.complete("p", "small", source=PromptSource.LLM_FACADE)
        assert len(cached_facade.calls) == 1

    def test_translate_is_scoped_too(self, cached_facade):
        cached_facade.result = ProviderResult(text='{"k": "A"}')
        services.translate("en", "de", {"k": "Hello"}, user_id="tenant-a")
        cached_facade.result = ProviderResult(text='{"k": "B"}')
        result = services.translate("en", "de", {"k": "Hello"}, user_id="tenant-b")
        assert result == {"status": "ok", "result": {"k": "B"}}

    def test_policy_without_scope_support_is_refused(self, settings, caplog):
        """A host policy predating the scoped signature is switched off
        rather than trusted — it has no way to be correct."""
        from stapel_agent.tests.fakes import FakeProvider, LegacyCachePolicy

        settings.STAPEL_AGENT = {
            "PROVIDERS": {"fake": "stapel_agent.tests.fakes.FakeProvider"},
            "DEFAULT_PROVIDER": "fake",
            "CACHE_POLICY": "stapel_agent.tests.fakes.LegacyCachePolicy",
        }
        FakeProvider.reset()
        LegacyCachePolicy.reset()
        result = services.complete(
            "p", "small", source=PromptSource.LLM_FACADE, user_id="tenant-a"
        )
        assert LegacyCachePolicy.lookups == []
        assert result["result"] == '{"answer": 42}'
        assert "predates tenant-scoped keys" in caplog.text


def _row(**kwargs):
    defaults = dict(
        source=PromptSource.LLM_FACADE,
        model="m",
        model_size="small",
        prompt="secret prompt",
        system_prompt="secret system",
        response="secret response",
        error_message="secret error",
        status=PromptStatus.SUCCESS,
        input_tokens=7,
        user_id="tenant-a",
    )
    defaults.update(kwargs)
    return PromptLog.objects.create(**defaults)


@pytest.mark.django_db
class TestRetention:
    def _age(self, row, days):
        PromptLog.objects.filter(pk=row.pk).update(
            created_at=timezone.now() - timezone.timedelta(days=days)
        )

    def test_old_rows_lose_their_text_and_keep_their_counters(self, settings):
        settings.STAPEL_AGENT = {"PROMPT_LOG_RETENTION_DAYS": 30}
        old = _row()
        self._age(old, 31)
        assert purge_prompt_logs() == 1
        old.refresh_from_db()
        assert old.prompt == ""
        assert old.system_prompt is None
        assert old.response is None
        assert old.error_message is None
        assert old.input_tokens == 7  # the ledger survives the scrub

    def test_rows_inside_the_window_are_untouched(self, settings):
        settings.STAPEL_AGENT = {"PROMPT_LOG_RETENTION_DAYS": 30}
        fresh = _row()
        self._age(fresh, 5)
        assert purge_prompt_logs() == 0
        fresh.refresh_from_db()
        assert fresh.prompt == "secret prompt"

    def test_dry_run_changes_nothing(self, settings):
        settings.STAPEL_AGENT = {"PROMPT_LOG_RETENTION_DAYS": 30}
        old = _row()
        self._age(old, 90)
        assert purge_prompt_logs(dry_run=True) == 1
        old.refresh_from_db()
        assert old.prompt == "secret prompt"

    def test_no_configured_window_is_a_no_op(self, settings):
        settings.STAPEL_AGENT = {"PROMPT_LOG_RETENTION_DAYS": None}
        old = _row()
        self._age(old, 3650)
        assert purge_prompt_logs() == 0
        old.refresh_from_db()
        assert old.prompt == "secret prompt"

    def test_management_command_runs_the_job(self, settings):
        from django.core.management import call_command

        settings.STAPEL_AGENT = {"PROMPT_LOG_RETENTION_DAYS": 1}
        old = _row()
        self._age(old, 10)
        call_command("purge_prompt_logs")
        old.refresh_from_db()
        assert old.prompt == ""


@pytest.mark.django_db
class TestGDPRProvider:
    def test_registered_with_the_registry(self):
        from stapel_core.gdpr import gdpr_registry

        assert "agent" in gdpr_registry.sections

    def test_export_returns_the_users_prompts_only(self):
        _row(prompt="mine", user_id="7")
        _row(prompt="someone else's", user_id="8")
        exported = AgentGDPRProvider().export(7)
        assert [p["prompt"] for p in exported["prompts"]] == ["mine"]

    def test_anonymize_matches_delete(self):
        """Nothing personal survives a scrub, so there is no separate
        retained-content case: the retained part is numbers."""
        row = _row(user_id="7")
        AgentGDPRProvider().anonymize(7)
        row.refresh_from_db()
        assert (row.prompt, row.response, row.user_id) == ("", None, None)
        assert row.input_tokens == 7

    def test_delete_scrubs_content_and_unlinks_the_subject(self):
        row = _row(user_id="7")
        other = _row(user_id="8")
        AgentGDPRProvider().delete(7)
        row.refresh_from_db()
        other.refresh_from_db()
        assert row.prompt == ""
        assert row.response is None
        assert row.user_id is None
        assert row.input_tokens == 7
        assert other.prompt == "secret prompt"  # untouched
