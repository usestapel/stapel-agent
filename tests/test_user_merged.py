"""A merge re-parents the prompt ledger; it does not erase it.

The gap these tests close (stapel-core 0.52.1, ``stapel_core.lifecycle.E001``):
this module subscribed ``user.deleted`` and nothing else, so a guest whose
account was folded into an existing one left every prompt log row pointing
at an id that can no longer sign in. Nothing raised and nothing retried —
the rows were simply orphaned, which is why the check that reports the
silence is worth as much as the handler that ends it.

So the tests below assert the four things that have to hold together: the
rows move, a redelivery moves nothing further, an unusable payload is
dropped rather than raised on, and an event about users this deployment
holds nothing for is a quiet no-op.
"""
import types

import pytest

from stapel_agent.actions import handle_user_merged
from stapel_agent.models import PromptLog, PromptSource, PromptStatus

GUEST = "11111111-1111-1111-1111-111111111111"
SURVIVOR = "22222222-2222-2222-2222-222222222222"


def _row(**kwargs):
    defaults = dict(
        source=PromptSource.LLM_FACADE,
        model="m",
        model_size="small",
        prompt="what the guest asked",
        status=PromptStatus.SUCCESS,
        input_tokens=7,
        output_tokens=3,
        user_id=GUEST,
        workspace_id="ws-1",
    )
    defaults.update(kwargs)
    return PromptLog.objects.create(**defaults)


def _event(**payload):
    return types.SimpleNamespace(
        payload=payload, event_id="evt-1", service="auth",
    )


@pytest.mark.django_db
class TestUserMerged:
    def test_rows_move_to_the_survivor(self):
        guest_row = _row()
        survivor_row = _row(user_id=SURVIVOR, prompt="what the survivor asked")

        handle_user_merged(_event(from_user_id=GUEST, into_user_id=SURVIVOR))

        guest_row.refresh_from_db()
        survivor_row.refresh_from_db()
        assert guest_row.user_id == SURVIVOR
        assert survivor_row.user_id == SURVIVOR
        assert PromptLog.objects.filter(user_id=GUEST).count() == 0
        assert PromptLog.objects.filter(user_id=SURVIVOR).count() == 2

    def test_content_and_tenant_are_untouched(self):
        """A merge joins two people, not two tenants — and bills nothing."""
        row = _row()

        handle_user_merged(_event(from_user_id=GUEST, into_user_id=SURVIVOR))

        row.refresh_from_db()
        assert row.prompt == "what the guest asked"
        assert row.workspace_id == "ws-1"
        assert row.input_tokens == 7
        assert row.output_tokens == 3

    def test_redelivery_changes_nothing(self):
        _row()
        event = _event(from_user_id=GUEST, into_user_id=SURVIVOR)

        handle_user_merged(event)
        handle_user_merged(event)

        assert PromptLog.objects.filter(user_id=SURVIVOR).count() == 1
        assert PromptLog.objects.filter(user_id=GUEST).count() == 0

    def test_unknown_users_do_nothing(self):
        """This deployment holds no rows for either id — a quiet no-op."""
        mine = _row(user_id="someone-else")

        handle_user_merged(_event(from_user_id=GUEST, into_user_id=SURVIVOR))

        mine.refresh_from_db()
        assert mine.user_id == "someone-else"
        assert PromptLog.objects.count() == 1

    @pytest.mark.parametrize(
        "payload",
        [
            {"from_user_id": "not-a-uuid", "into_user_id": "also-not-a-uuid"},
            {"from_user_id": GUEST},
            {"into_user_id": SURVIVOR},
            {},
            {"from_user_id": "", "into_user_id": SURVIVOR},
            {"from_user_id": GUEST, "into_user_id": GUEST},
        ],
    )
    def test_malformed_payload_does_not_raise(self, payload):
        """A raise here means the bus redelivers a poison message forever."""
        row = _row()

        handle_user_merged(_event(**payload))

        row.refresh_from_db()
        assert row.user_id == GUEST


def test_lifecycle_pair_check_is_green():
    """The regression gate: this app answers both halves of the life cycle.

    ``stapel_core.lifecycle.E001`` reports an app that handles
    ``user.deleted`` and registers no ``user.merged`` handler. It is what
    would have caught the silence in the first place, so it stays wired to
    the suite rather than to a one-time audit.
    """
    from stapel_core.comm.lifecycle_checks import check_lifecycle_pairs

    assert check_lifecycle_pairs() == []
