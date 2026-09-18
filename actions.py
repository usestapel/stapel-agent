"""Action subscriptions of the agent module — the prompt ledger's erasure seam.

The `PromptLog` table holds prompts, system prompts and full model
responses, so it is a data owner in the stapel-gdpr sense: an erasure
request for a subject this module holds rows about must reach these rows
and come back with a receipt. Until 0.14.0 this package shipped no
subscriber at all — it participated only through the in-process
`GDPRProvider`, which a microservice deployment never runs, and the
client audit found exactly that: an owner declared in `DATA_OWNERS`
whose consumer nothing had ever deployed, discoverable only by waiting
thirty days for the request to time out.

Since 0.27.0 the three erasure-protocol handlers are not written here.
``apps.ready()`` calls ``stapel_core.gdpr.register_gdpr_owner("agent",
SUBJECT_TYPES, erase_subject)``, and core subscribes
``gdpr.erasure.requested``, ``gdpr.owner.probe`` and the deprecated
``user.deleted`` from one module — the same handlers this file used to
carry, down to the deterministic receipt id and the receipt inside the
erase's transaction. The probe is still answered by the subscriber that
erases, which is the whole evidence an ``alive`` carries. What stays ours
is :func:`stapel_agent.gdpr.erase_subject`.

What is left here is the other half of the account life cycle:

* ``user.merged`` — the other half of an account's life cycle. A merge
  RE-PARENTS rows onto the surviving account rather than erasing them,
  and an owner that subscribed only to the deletion half has a silent,
  wrong answer for the other (``stapel_core.lifecycle.E001``).

Handlers are idempotent — delivery is at-least-once (outbox retries,
broker redelivery) — and a redelivery reports the ``0`` it removed
rather than pretending it did the work twice.
"""
import logging

from stapel_core.comm import on_action

logger = logging.getLogger(__name__)


@on_action("user.merged")
def handle_user_merged(event):
    """Re-parent this module's prompt ledger onto the surviving account.

    ``user.merged`` (stapel-auth 0.30.0) is the opposite of
    ``user.deleted``: a guest account was folded into an account that
    already existed, ``from_user_id`` is gone from auth, and every row that named
    it belongs to ``into_user_id`` now. Nothing is erased. An app that
    answered only the deletion half would leave the guest's prompt log
    pointing at an id that can no longer sign in — invisible to the person
    who wrote it, and outside the reach of any erasure they could later
    request, because no erasure is ever requested for an id nobody holds.

    **Merge policy: re-point, keep everything.** A ``PromptLog`` row is a
    metering record as much as a content record — it carries the tokens and
    the cost the deployment already paid for — so the survivor inherits the
    guest's rows whole. Nothing is summed, rewritten or dropped; only
    ``user_id`` moves. ``workspace_id`` is deliberately untouched: a merge
    joins two people, not two tenants.

    Idempotent: the re-point is a filter on ``from_user_id``, which matches
    nothing once it has run, so a redelivery reports 0 rows.

    A payload whose ids are unusable is logged and dropped rather than
    raised on — a raise makes the bus redeliver a message that can never
    succeed. An id the payload *does* carry that this deployment simply has
    no rows for is not an error at all: it re-points 0 rows and says so.
    """
    from django.core.exceptions import ValidationError
    from django.db import transaction

    from .models import PromptLog

    payload = event.payload or {}
    from_user_id = payload.get("from_user_id")
    into_user_id = payload.get("into_user_id")
    if not from_user_id or not into_user_id:
        logger.error(
            "user.merged event without both account ids: %s",
            getattr(event, "event_id", "?"),
        )
        return
    if str(from_user_id) == str(into_user_id):
        logger.error(
            "user.merged names one account twice (%s): %s",
            from_user_id, getattr(event, "event_id", "?"),
        )
        return

    try:
        with transaction.atomic():
            moved = PromptLog.objects.filter(user_id=str(from_user_id)).update(
                user_id=str(into_user_id)
            )
    except (TypeError, ValueError, ValidationError):
        # ValidationError is in the list on purpose: a UUID column rejects a
        # malformed key with ValidationError, which is NOT a ValueError, and
        # a handler that caught only the latter would raise into the bus.
        logger.error(
            "user.merged with unusable ids (from=%r into=%r): %s",
            from_user_id, into_user_id, getattr(event, "event_id", "?"),
        )
        return
    logger.info(
        "agent re-parented %s prompt log row(s) from merged user %s to %s",
        moved, from_user_id, into_user_id,
    )


__all__ = [
    "handle_user_merged",
]
