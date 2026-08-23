"""GDPR provider for the prompt ledger.

The PromptLog table holds prompts, system prompts and full model
responses — customer content by any reading of it — and until the
2026-08-11 audit (AGENT-02) it appeared in no export and no erasure
path. It does now: this provider is registered in ``apps.ready()``
alongside every other package's, so a subject request that reaches the
GDPR orchestrator reaches these rows too.

Erasure **deletes** the rows (0.14.0; through 0.13.x it scrubbed the text
and kept the counters). The ledger argument that motivated the scrub is
real, but it is the argument for ``anonymize()``, which still keeps the
numbers and the tenant while dropping the person. An erasure request is a
request to be gone, and one erasure cannot mean two things depending on
which path reached this module — the in-process provider below and the
``gdpr.erasure.requested`` subscriber in :mod:`stapel_agent.actions` run
the same :func:`erase_subject`.
"""
from __future__ import annotations

from stapel_core.gdpr import GDPRProvider


def _jsonable(key: str, value):
    """One row value, in a shape a subject-access export can serialise.

    UUIDs and timestamps stringify; ``cost_usd`` is a ``Decimal``, which
    ``json.dumps`` refuses outright — a column added for accounting must
    not be the reason an erasure export raises.
    """
    from decimal import Decimal

    if key in ("created_at", "id"):
        return value.isoformat() if hasattr(value, "isoformat") else str(value)
    if isinstance(value, Decimal):
        return float(value)
    return value


class AgentGDPRProvider(GDPRProvider):
    """Export / erase one user's rows in the LLM prompt ledger."""

    section = "agent"

    def _rows(self, user_id: int):
        from .models import PromptLog

        # PromptLog.user_id is a CharField (the ledger accepts any host's
        # id shape), so the numeric subject id is compared as text.
        return PromptLog.objects.filter(user_id=str(user_id))

    def export(self, user_id: int) -> dict:
        rows = list(
            self._rows(user_id).values(
                "id",
                "source",
                "model",
                "model_size",
                "prompt",
                "system_prompt",
                "response",
                "status",
                "error_message",
                "input_tokens",
                "output_tokens",
                "thinking_tokens",
                "duration_ms",
                # The metering columns are part of the subject's record too:
                # a person asking what we hold on them is owed the numbers
                # we bill their account on, not only the text.
                "audio_duration_ms",
                "cost_usd",
                "cost_basis",
                "created_at",
            )
        )
        return {"prompts": [{k: _jsonable(k, v) for k, v in row.items()} for row in rows]}

    def delete(self, user_id: int) -> None:
        """Erase the subject's rows — the same operation the comm path runs.

        Since 0.14.0 this DELETES rather than scrubs. Until 0.13.x it
        emptied the text and unlinked the subject, keeping the counters
        for the cost ledger; the deletion-lifecycle spec settles that
        trade the other way, and there cannot be two answers to one
        erasure. A host that wants the numbers without the person calls
        :meth:`anonymize`, which still does exactly what ``delete`` used
        to do — but an erasure request removes the rows.
        """
        erase_subject("account", user_id)

    def anonymize(self, user_id: int) -> None:
        """Drop the person, keep the accounting row.

        Scrub the text and unlink the subject id; the tenant
        (``workspace_id``), the token counters and the cost stay. This is
        the operation the ledger argument justifies, and it is no longer
        a synonym for ``delete``.
        """
        from .retention import scrub_queryset

        rows = self._rows(user_id)
        scrub_queryset(rows)
        # Second statement on purpose: the scrub must have committed
        # before the rows stop being findable by subject id.
        rows.update(user_id=None)


#: The name this module answers to in ``STAPEL_GDPR["DATA_OWNERS"]`` — the
#: same string as :attr:`AgentGDPRProvider.section`, because an owner with
#: two names is an owner whose receipts land on nobody's part.
OWNER = AgentGDPRProvider.section

#: Subject types this module can actually erase, and therefore the only
#: ones it claims in ``DATA_OWNERS`` and answers ``gdpr.owner.alive`` with.
#:
#: **"meeting" is deliberately absent.** The deletion-lifecycle spec lists
#: it for this library on the assumption that the 0.12.0 metering columns
#: carry a meeting/recording correlation. They do not: 0.12.0 added
#: ``user_id``, ``workspace_id``, ``cost_usd``, ``cost_basis`` and
#: ``audio_duration_ms``, and no ``llm.*`` payload accepts an entity id at
#: all (the schemas are ``additionalProperties: false``). ``metadata`` is
#: written by this package, not by the caller, and holds provider details.
#: So a meeting erasure has no key to match on here, and claiming the
#: subject type would produce a receipt for work nobody could have done —
#: exactly the self-certification this protocol exists to prevent. Giving
#: this module a real meeting key means a new nullable column plus a new
#: optional field on every ``llm.*`` schema, which is its own release.
SUBJECT_TYPES = ("account", "workspace")


def erase_subject(subject_type: str, subject_key, *, workspace_id=None) -> dict | None:
    """Delete every prompt-ledger row this module holds about one subject.

    Returns ``{"prompt_logs": n}`` — what was actually removed, which is
    what the receipt carries — or ``None`` when *subject_type* is not one
    this module claims (the caller then owes no receipt: gdpr never
    created a part for it).

    Idempotent by construction: a redelivery finds nothing to delete and
    reports ``0``. Deleting, not scrubbing: an erasure request is not a
    retention window (see :mod:`stapel_agent.retention` for that one).
    """
    from .models import PromptLog

    key = str(subject_key) if subject_key is not None else ""
    if not key:
        return None
    if subject_type == "account":
        # Every row of that person, in every workspace. A ``workspace_id``
        # on an account request is a partition hint for owners that need
        # one; narrowing by it here would leave the subject's rows in
        # every other tenant.
        rows = PromptLog.objects.filter(user_id=key)
    elif subject_type == "workspace":
        rows = PromptLog.objects.filter(workspace_id=key)
    else:
        return None
    removed, _by_model = rows.delete()
    return {"prompt_logs": int(removed)}


__all__ = ["OWNER", "SUBJECT_TYPES", "AgentGDPRProvider", "erase_subject"]
