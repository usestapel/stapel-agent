"""Erasure over comm: the prompt ledger answers a subject request.

The finding this closes (ironmemo, 2026-08-21): stapel-agent was declared
a GDPR data owner and shipped no ``actions.py`` at all, so the only
erasure path was the in-process provider — which a service deployment
never runs. The erasure request went out, nothing consumed it, and the
gap was invisible until the request timed out thirty days later.

So the tests below assert three things that have to hold together: the
rows really go, the receipt says how many went, and the liveness answer
comes from the same module — an ``alive`` emitted from anywhere else
would prove only that a container is running.
"""
import types
from decimal import Decimal

import pytest
from unittest.mock import patch

from stapel_agent.actions import (
    handle_erasure_requested,
    handle_owner_probe,
    handle_user_deleted,
)
from stapel_agent.gdpr import (
    OWNER,
    PSEUDONYM_PREFIX,
    SUBJECT_TYPES,
    erase_subject,
    pseudonymize,
)
from stapel_agent.models import PromptLog, PromptSource, PromptStatus


def _row(**kwargs):
    defaults = dict(
        source=PromptSource.LLM_FACADE,
        model="m",
        model_size="small",
        prompt="secret prompt",
        system_prompt="secret system",
        response="secret response",
        status=PromptStatus.SUCCESS,
        input_tokens=7,
        user_id="u-1",
        workspace_id="ws-1",
    )
    defaults.update(kwargs)
    return PromptLog.objects.create(**defaults)


def _event(**payload):
    return types.SimpleNamespace(
        payload=payload, event_id="evt-1", service="gdpr",
    )


@pytest.mark.django_db
class TestEraseSubject:
    """Erasure removes what a person wrote; the bill stays, without them.

    Deleting the rows would silently restate closed reporting periods —
    "what did March cost" is not a question about whether the account
    still exists. So: content scrubbed, ids pseudonymized, economics
    untouched.
    """

    def test_the_content_goes_and_the_bill_stays(self):
        row = _row(
            user_id="u-1", input_tokens=7, output_tokens=11,
            cost_usd=Decimal("0.00012345"), cost_basis="provider_ticks",
            audio_duration_ms=3_600_000,
        )
        assert erase_subject("account", "u-1") == {"prompt_logs": 1}
        row.refresh_from_db()
        assert (row.prompt, row.system_prompt, row.response) == ("", None, None)
        assert row.error_message is None
        assert row.input_tokens == 7 and row.output_tokens == 11
        assert row.cost_usd == Decimal("0.00012345")
        assert row.cost_basis == "provider_ticks"
        assert row.audio_duration_ms == 3_600_000
        assert row.model == "m" and row.created_at is not None

    def test_both_ids_become_stable_pseudonyms(self):
        row = _row(user_id="u-1", workspace_id="ws-1")
        erase_subject("account", "u-1")
        row.refresh_from_db()
        assert row.user_id == pseudonymize("u-1")
        assert row.workspace_id == pseudonymize("ws-1")
        assert row.user_id.startswith(PSEUDONYM_PREFIX)
        assert "u-1" not in row.user_id

    def test_the_pseudonym_is_keyed_not_a_plain_digest(self, settings):
        """A bare sha256 of a user id is a rainbow table away from being
        the id again; the fleet's scheme is HMAC under SECRET_KEY."""
        import hashlib
        import hmac

        plain = hashlib.sha256(b"u-1").hexdigest()[:32]
        assert pseudonymize("u-1") != f"{PSEUDONYM_PREFIX}{plain}"
        expected = hmac.new(
            str(settings.SECRET_KEY).encode(), b"u-1", hashlib.sha256
        ).hexdigest()[:32]
        assert pseudonymize("u-1") == f"{PSEUDONYM_PREFIX}{expected}"

    def test_pseudonymizing_a_pseudonym_changes_nothing(self):
        """Otherwise a redelivered erasure would mint a second pseudonym
        for one subject and split its history in two."""
        once = pseudonymize("u-1")
        assert pseudonymize(once) == once

    def test_one_subjects_rows_stay_one_subject(self):
        """Stability is what keeps per-subject aggregates addable after an
        erasure: three rows, one pseudonym, the same three-row total."""
        for _ in range(3):
            _row(user_id="u-1", input_tokens=5)
        erase_subject("account", "u-1")
        pseudonyms = set(PromptLog.objects.values_list("user_id", flat=True))
        assert pseudonyms == {pseudonymize("u-1")}
        assert sum(PromptLog.objects.values_list("input_tokens", flat=True)) == 15

    def test_account_touches_the_rows_of_that_person_only(self):
        _row(user_id="u-1")
        _row(user_id="u-2")
        assert erase_subject("account", "u-1") == {"prompt_logs": 1}
        survivor = PromptLog.objects.get(user_id="u-2")
        assert survivor.prompt == "secret prompt"

    def test_an_account_is_erased_across_every_workspace(self):
        """A workspace_id on an account request is a partition hint for
        owners that need one; narrowing by it would leave the subject's
        rows in every other tenant."""
        _row(user_id="u-1", workspace_id="ws-1")
        _row(user_id="u-1", workspace_id="ws-2")
        assert erase_subject("account", "u-1", workspace_id="ws-1") == {
            "prompt_logs": 2
        }
        assert PromptLog.objects.filter(user_id="u-1").count() == 0
        assert PromptLog.objects.filter(prompt="").count() == 2

    def test_workspace_takes_the_tenants_rows_including_unattributed_ones(self):
        _row(user_id="u-1", workspace_id="ws-1")
        _row(user_id=None, workspace_id="ws-1")
        other = _row(user_id="u-1", workspace_id="ws-2")
        assert erase_subject("workspace", "ws-1") == {"prompt_logs": 2}
        other.refresh_from_db()
        assert other.workspace_id == "ws-2"
        assert other.prompt == "secret prompt"
        assert PromptLog.objects.filter(
            workspace_id=pseudonymize("ws-1")
        ).count() == 2

    def test_metadata_keeps_the_accounting_keys_and_drops_the_rest(self):
        """`audio` carries AudioRef.describe() — the URL of the person's
        own recording — and a caller's annotation is content too. The
        allowlist is what a bill is computed and justified from."""
        row = _row(user_id="u-1", metadata={
            "provider": "whisper-http",
            "priced_by": "deepgram:nova-3",
            "language": "ru",
            "audio": "url:files.example/meeting-with-alice.wav",
            "customer_note": "Alice asked about her divorce",
        })
        erase_subject("account", "u-1")
        row.refresh_from_db()
        assert row.metadata == {
            "provider": "whisper-http",
            "priced_by": "deepgram:nova-3",
            "language": "ru",
        }

    def test_a_row_with_no_metadata_survives_the_strip(self):
        row = _row(user_id="u-1", metadata=None)
        assert erase_subject("account", "u-1") == {"prompt_logs": 1}
        row.refresh_from_db()
        assert row.metadata is None

    def test_a_subject_type_this_module_does_not_own_is_not_erased(self):
        """`meeting` is in the spec's table for this lib on the assumption
        that the 0.12.0 metering columns carry a meeting correlation. They
        do not — see gdpr.SUBJECT_TYPES — so the answer is None (no work,
        no receipt), never a receipt for work nobody could have done."""
        _row()
        assert erase_subject("meeting", "m-1") is None
        assert erase_subject("recording", "r-1") is None
        assert PromptLog.objects.get().prompt == "secret prompt"

    def test_an_empty_subject_key_erases_nothing(self):
        _row()
        assert erase_subject("account", "") is None
        assert erase_subject("account", None) is None
        assert PromptLog.objects.get().prompt == "secret prompt"

    def test_a_second_run_reports_the_zero_it_touched(self):
        """The id is a pseudonym after the first run, so the subject key
        matches nothing — idempotent without a tombstone to remember."""
        _row(user_id="u-1")
        erase_subject("account", "u-1")
        assert erase_subject("account", "u-1") == {"prompt_logs": 0}
        assert PromptLog.objects.count() == 1

    def test_the_claimed_types_are_the_ones_that_work(self):
        for subject_type in SUBJECT_TYPES:
            assert erase_subject(subject_type, "nothing-matches-this") == {
                "prompt_logs": 0
            }


@pytest.mark.django_db
class TestErasureRequested:
    def test_the_content_goes_and_the_receipt_says_how_many(self):
        _row(user_id="u-1")
        _row(user_id="u-1")
        _row(user_id="u-2")
        with patch("stapel_core.comm.emit") as m_emit:
            handle_erasure_requested(_event(
                request_id=5, correlation_id="corr-1",
                subject_type="account", subject_key="u-1",
            ))
        assert PromptLog.objects.filter(user_id="u-1").count() == 0
        assert PromptLog.objects.filter(prompt="").count() == 2
        assert PromptLog.objects.get(user_id="u-2").prompt == "secret prompt"
        name, payload = m_emit.call_args.args
        assert name == "gdpr.section.erased"
        assert payload["owner"] == OWNER == "agent"
        assert payload["correlation_id"] == "corr-1"
        assert (payload["subject_type"], payload["subject_key"]) == (
            "account", "u-1",
        )
        assert payload["counts"] == {"prompt_logs": 2}
        assert payload["receipt_id"]

    def test_a_workspace_erasure_receipts_the_workspace_pair(self):
        _row(workspace_id="ws-9")
        with patch("stapel_core.comm.emit") as m_emit:
            handle_erasure_requested(_event(
                request_id=6, correlation_id="corr-2",
                subject_type="workspace", subject_key="ws-9",
            ))
        payload = m_emit.call_args.args[1]
        assert (payload["subject_type"], payload["subject_key"]) == (
            "workspace", "ws-9",
        )
        assert payload["counts"] == {"prompt_logs": 1}

    def test_redelivery_touches_nothing_and_says_so(self):
        """At-least-once delivery: the second receipt reports 0 rather
        than claiming the work twice — and carries the SAME receipt_id, so
        an audit following it back lands on one erasure."""
        _row(user_id="u-1")
        event = _event(
            request_id=7, correlation_id="corr-3",
            subject_type="account", subject_key="u-1",
        )
        with patch("stapel_core.comm.emit") as m_emit:
            handle_erasure_requested(event)
            handle_erasure_requested(event)
        first, second = [c.args[1] for c in m_emit.call_args_list]
        assert first["counts"] == {"prompt_logs": 1}
        assert second["counts"] == {"prompt_logs": 0}
        assert first["receipt_id"] == second["receipt_id"]

    def test_an_unclaimed_subject_is_ignored_without_a_receipt(self):
        """gdpr creates a part only for owners that claim the type, so a
        receipt here would be answering for somebody else."""
        _row()
        with patch("stapel_core.comm.emit") as m_emit:
            handle_erasure_requested(_event(
                request_id=8, correlation_id="corr-4",
                subject_type="meeting", subject_key="m-1",
            ))
        m_emit.assert_not_called()
        assert PromptLog.objects.get().prompt == "secret prompt"

    @pytest.mark.parametrize("payload", [
        {"correlation_id": "c", "subject_type": "account"},          # no key
        {"correlation_id": "c", "subject_key": "u-1"},               # no type
        {"subject_type": "account", "subject_key": "u-1"},           # no corr
    ])
    def test_a_malformed_request_erases_nothing(self, payload):
        _row()
        with patch("stapel_core.comm.emit") as m_emit:
            handle_erasure_requested(_event(**payload))
        m_emit.assert_not_called()
        assert PromptLog.objects.get().prompt == "secret prompt"

    def test_the_receipt_validates_against_the_committed_schema(self):
        """No mock: the emit goes through comm with VALIDATE_SCHEMAS on,
        so schemas/emits/gdpr.section.erased.json is what accepts it."""
        from stapel_core.comm import subscribe_action

        seen = []
        subscribe_action("gdpr.section.erased", lambda e: seen.append(e.payload))
        _row(user_id="u-7")
        handle_erasure_requested(_event(
            request_id=9, correlation_id="corr-5",
            subject_type="account", subject_key="u-7",
        ))
        assert [p["counts"] for p in seen] == [{"prompt_logs": 1}]


@pytest.mark.django_db
class TestOwnerProbe:
    def test_alive_names_the_owner_and_what_it_can_erase(self):
        with patch("stapel_core.comm.emit") as m_emit:
            handle_owner_probe(_event(correlation_id="corr-probe"))
        name, payload = m_emit.call_args.args
        assert name == "gdpr.owner.alive"
        assert payload == {
            "owner": "agent",
            "subject_types": ["account", "workspace"],
            "correlation_id": "corr-probe",
        }

    def test_meeting_is_not_claimed_in_the_answer(self):
        """The spec's table lists it; this module has no key to match on,
        and an owner that claims what it cannot do turns the health table
        into a false green."""
        with patch("stapel_core.comm.emit") as m_emit:
            handle_owner_probe(_event(correlation_id="c"))
        assert "meeting" not in m_emit.call_args.args[1]["subject_types"]

    def test_a_probe_without_a_correlation_id_still_answers(self):
        with patch("stapel_core.comm.emit") as m_emit:
            handle_owner_probe(_event())
        assert m_emit.call_args.args[1] == {
            "owner": "agent", "subject_types": ["account", "workspace"],
        }

    def test_the_answer_comes_from_the_module_that_erases(self):
        """Co-location IS the evidence: `alive` proves the erasure
        subscriber is consumed, not that a container is deployed. Two
        modules would make it prove nothing."""
        from stapel_core.comm.registry import action_registry

        erasers = action_registry.handlers("gdpr.erasure.requested")
        probes = action_registry.handlers("gdpr.owner.probe")
        assert handle_erasure_requested in erasers
        assert handle_owner_probe in probes
        assert handle_owner_probe.__module__ == handle_erasure_requested.__module__

    def test_the_alive_answer_validates_against_the_committed_schema(self):
        from stapel_core.comm import subscribe_action

        seen = []
        subscribe_action("gdpr.owner.alive", lambda e: seen.append(e.payload))
        handle_owner_probe(_event(correlation_id="corr-probe-2"))
        assert seen[-1]["owner"] == "agent"


@pytest.mark.django_db
class TestDeprecatedUserDeleted:
    """stapel-gdpr keeps emitting `user.deleted` for accounts until its
    0.6.0. It routes through the same erase call, so the handler can be
    deleted without deleting any erasure logic."""

    def test_it_erases_through_the_same_path(self):
        _row(user_id="u-1")
        _row(user_id="u-2")
        with patch("stapel_core.comm.emit"):
            handle_user_deleted(_event(user_id="u-1", correlation_id="corr-6"))
        assert PromptLog.objects.get(user_id=pseudonymize("u-1")).prompt == ""
        assert PromptLog.objects.get(user_id="u-2").prompt == "secret prompt"

    def test_the_receipt_carries_the_account_subject_pair(self):
        _row(user_id="u-1")
        with patch("stapel_core.comm.emit") as m_emit:
            handle_user_deleted(_event(user_id="u-1", correlation_id="corr-7"))
        payload = m_emit.call_args.args[1]
        assert payload["correlation_id"] == "corr-7"
        assert (payload["subject_type"], payload["subject_key"]) == (
            "account", "u-1",
        )
        assert payload["user_id"] == "u-1"
        assert payload["counts"] == {"prompt_logs": 1}

    def test_no_receipt_without_a_correlation_id(self):
        """Monolith path (the in-process provider ran): nobody is waiting
        on a part, so there is nothing to confirm."""
        _row(user_id="u-1")
        with patch("stapel_core.comm.emit") as m_emit:
            handle_user_deleted(_event(user_id="u-1"))
        m_emit.assert_not_called()
        assert PromptLog.objects.get().user_id == pseudonymize("u-1")

    def test_an_event_without_a_user_id_erases_nothing(self):
        _row()
        with patch("stapel_core.comm.emit") as m_emit:
            handle_user_deleted(_event(correlation_id="corr-8"))
        m_emit.assert_not_called()
        assert PromptLog.objects.get().prompt == "secret prompt"

    def test_both_events_for_one_account_are_safe_together(self):
        """0.5.0 emits `gdpr.erasure.requested` AND `user.deleted` for an
        account. Both land here; the second finds nothing."""
        _row(user_id="u-1")
        event = _event(
            request_id=10, correlation_id="corr-9",
            subject_type="account", subject_key="u-1",
        )
        with patch("stapel_core.comm.emit") as m_emit:
            handle_erasure_requested(event)
            handle_user_deleted(_event(user_id="u-1", correlation_id="corr-9"))
        counts = [c.args[1]["counts"] for c in m_emit.call_args_list]
        assert counts == [{"prompt_logs": 1}, {"prompt_logs": 0}]


class TestBeatSchedule:
    """The retention job in schedulable form — the half that was missing."""

    def test_the_task_name_is_stable(self):
        """A beat entry references the task by name, so the name is the
        contract — renaming the function must not silently unschedule
        every host's retention job."""
        from stapel_agent.tasks import PURGE_TASK_NAME

        assert PURGE_TASK_NAME == "stapel_agent.tasks.purge_prompt_logs"

    def test_the_factory_names_the_shipped_task(self):
        """The factory builds a `crontab`, so it needs celery — which this
        package does not depend on (the callable below does not)."""
        pytest.importorskip("celery")
        from stapel_agent.tasks import (
            PURGE_BEAT_KEY, PURGE_TASK_NAME, get_agent_beat_schedule,
        )

        schedule = get_agent_beat_schedule()
        assert schedule[PURGE_BEAT_KEY]["task"] == PURGE_TASK_NAME

    @pytest.mark.django_db
    def test_the_task_runs_the_retention_job(self, settings):
        from django.utils import timezone

        from stapel_agent.tasks import purge_prompt_logs

        settings.STAPEL_AGENT = {"PROMPT_LOG_RETENTION_DAYS": 30}
        old = _row()
        PromptLog.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timezone.timedelta(days=31)
        )
        assert purge_prompt_logs() == 1
        old.refresh_from_db()
        assert old.prompt == ""


class TestBeatScheduleIsRegistered:
    """W017: this process runs beat and this package is not in it."""

    def _ids(self, **kwargs):
        from stapel_agent.checks import check_agent_beat_schedule_is_registered

        return [
            issue.id
            for issue in check_agent_beat_schedule_is_registered(app_configs=None)
        ]

    def test_a_beat_schedule_without_the_entry_warns(self, settings):
        settings.STAPEL_AGENT = {}
        settings.CELERY_BEAT_SCHEDULE = {
            "other": {"task": "myapp.tasks.send_digest", "schedule": 60}
        }
        assert self._ids() == ["stapel_agent.W017"]

    def test_an_entry_for_the_purge_task_silences_it(self, settings):
        from stapel_agent.tasks import PURGE_TASK_NAME

        settings.STAPEL_AGENT = {}
        settings.CELERY_BEAT_SCHEDULE = {
            "other": {"task": "myapp.tasks.send_digest", "schedule": 60},
            "agent-prompt-log-retention": {
                "task": PURGE_TASK_NAME, "schedule": 86400,
            },
        }
        assert self._ids() == []

    def test_the_shipped_factory_silences_it(self, settings):
        """The same thing, through the entry a host actually writes —
        `crontab` needs celery, which is optional here."""
        pytest.importorskip("celery")
        from stapel_agent.tasks import get_agent_beat_schedule

        settings.STAPEL_AGENT = {}
        settings.CELERY_BEAT_SCHEDULE = {
            "other": {"task": "myapp.tasks.send_digest", "schedule": 60},
            **get_agent_beat_schedule(),
        }
        assert self._ids() == []

    def test_no_beat_schedule_at_all_is_W014s_finding(self, settings):
        """One gap, one warning: a process with no scheduler has no entry
        to be missing."""
        from stapel_agent.checks import check_prompt_log_retention_is_scheduled

        settings.STAPEL_AGENT = {}
        settings.CELERY_BEAT_SCHEDULE = {}
        assert self._ids() == []
        assert [
            i.id for i in check_prompt_log_retention_is_scheduled(app_configs=None)
        ] == ["stapel_agent.W014"]

    def test_the_two_checks_never_fire_together(self, settings):
        from stapel_agent.checks import check_prompt_log_retention_is_scheduled

        settings.STAPEL_AGENT = {}
        settings.CELERY_BEAT_SCHEDULE = {
            "other": {"task": "myapp.tasks.send_digest", "schedule": 60}
        }
        assert [
            i.id for i in check_prompt_log_retention_is_scheduled(app_configs=None)
        ] == []
        assert self._ids() == ["stapel_agent.W017"]

    def test_a_declared_external_scheduler_silences_it(self, settings):
        settings.STAPEL_AGENT = {"PROMPT_LOG_RETENTION_SCHEDULED": True}
        settings.CELERY_BEAT_SCHEDULE = {
            "other": {"task": "myapp.tasks.send_digest", "schedule": 60}
        }
        assert self._ids() == []

    def test_retention_switched_off_is_a_decision_not_a_gap(self, settings):
        settings.STAPEL_AGENT = {"PROMPT_LOG_RETENTION_DAYS": None}
        settings.CELERY_BEAT_SCHEDULE = {
            "other": {"task": "myapp.tasks.send_digest", "schedule": 60}
        }
        assert self._ids() == []

    def test_the_check_is_registered_with_django(self):
        from django.core.checks import registry

        from stapel_agent.checks import check_agent_beat_schedule_is_registered

        assert check_agent_beat_schedule_is_registered in registry.registry.get_checks()

    def test_the_id_belongs_to_this_check_alone(self):
        """W016 was the last free id; W017 must not end up shared the way
        W009 once was between two checks (fixed in 0.13.1)."""
        import ast
        from pathlib import Path

        import stapel_agent

        source = (Path(stapel_agent.__file__).parent / "checks.py").read_text()
        owners = {}
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.FunctionDef):
                continue
            for kw in (
                k for call in ast.walk(node)
                if isinstance(call, ast.Call) for k in call.keywords
            ):
                if kw.arg == "id" and isinstance(kw.value, ast.Constant):
                    owners.setdefault(kw.value.value, set()).add(node.name)
        assert owners["stapel_agent.W017"] == {
            "check_agent_beat_schedule_is_registered"
        }


class TestRetentionDefault:
    def test_the_shipped_window_is_thirty_days(self, settings):
        """0.14.0: 90 → 30. A library default that keeps content three
        times longer than the platform's own 30-day purge SLA is a default
        that quietly breaks it."""
        from stapel_agent.conf import agent_settings

        settings.STAPEL_AGENT = {}
        assert agent_settings.PROMPT_LOG_RETENTION_DAYS == 30

    def test_a_host_can_still_state_ninety(self, settings):
        from stapel_agent.conf import agent_settings

        settings.STAPEL_AGENT = {"PROMPT_LOG_RETENTION_DAYS": 90}
        assert agent_settings.PROMPT_LOG_RETENTION_DAYS == 90
