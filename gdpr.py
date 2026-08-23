"""GDPR provider for the prompt ledger.

The PromptLog table holds prompts, system prompts and full model
responses — customer content by any reading of it — and until the
2026-08-11 audit (AGENT-02) it appeared in no export and no erasure
path. It does now: this provider is registered in ``apps.ready()``
alongside every other package's, so a subject request that reaches the
GDPR orchestrator reaches these rows too.

**Erasure removes what a person wrote; the bill stays, without the
person.** One operation, :func:`erase_subject`, reached identically by
the in-process provider below and by the ``gdpr.erasure.requested``
subscriber in :mod:`stapel_agent.actions`:

* the content columns are scrubbed (prompt, system prompt, response,
  error) and ``metadata`` is cut down to the accounting keys this package
  writes — an audio ref, a caller's annotation or a provider's extra dict
  is content too;
* ``user_id`` and ``workspace_id`` are **pseudonymized** — a keyed HMAC,
  irreversible without the deployment's ``SECRET_KEY``, stable so the
  rows of one subject stay one subject;
* the economics stay untouched: ``cost_usd``, ``cost_basis``,
  ``audio_duration_ms``, the token counters, the model, the timestamps.

Deleting the rows would silently restate closed reporting periods, and
the question a cost query asks — what was spent in March — is not a
question about whether the account still exists. 0.14.0/0.14.1 deleted;
0.14.2 is the correction.
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
        """Erase the subject — the same operation the comm path runs.

        Scrub the content, pseudonymize the ids, keep the bill. See
        :func:`erase_subject`, which is where the operation lives.
        """
        erase_subject("account", user_id)

    #: ``anonymize`` IS ``delete`` here, not a weaker cousin: after the
    #: scrub the row holds numbers and a pseudonym, which is what an
    #: anonymisation is meant to produce. Two names for one operation, and
    #: the alias is named so nobody looks for a second implementation.
    anonymize = delete


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
#: all (the schemas are ``additionalProperties: false``), so nothing over
#: comm can even pass one; ``metadata`` is free-form and unindexed, which
#: is a place to put a value, not a key to erase by.
#: So a meeting erasure has no key to match on here, and claiming the
#: subject type would produce a receipt for work nobody could have done —
#: exactly the self-certification this protocol exists to prevent. Giving
#: this module a real meeting key means a new nullable column plus a new
#: optional field on every ``llm.*`` schema, which is its own release.
SUBJECT_TYPES = ("account", "workspace")

#: Marks a pseudonymized id, so a reader can tell one from a real host id
#: and a second erasure can leave it alone. Same spelling as
#: ``stapel_video.presence.pseudonymize_user``.
PSEUDONYM_PREFIX = "erased:"

#: The ``metadata`` keys this package writes for accounting and audit — the
#: dimensions a bill is computed from and the card that priced it. Erasure
#: keeps these and drops everything else, because everything else is either
#: a caller's annotation, a provider's extra dict or a reference to the
#: subject's own material (``audio`` carries ``AudioRef.describe()``, i.e.
#: the URL of their recording). An allowlist and not a denylist on purpose:
#: a key nobody anticipated must fall on the erasing side.
LEDGER_METADATA_KEYS = frozenset({
    "attempts",
    "batch_size",
    "diarization",
    "document_count",
    "fallback_used",
    "from",
    "images",
    "key_count",
    "language",
    "model",
    "n",
    "num_speakers",
    "priced_by",
    "provider",
    "size",
    "to",
    "top_n",
})


def pseudonymize(value: str) -> str:
    """Replace an id with a stable pseudonym — the fleet's one scheme.

    A keyed digest (HMAC-SHA256 under the deployment's ``SECRET_KEY``),
    mirroring ``stapel_video.presence.pseudonymize_user`` down to the
    prefix and the 32-hex truncation: stable, so one subject's rows stay
    one subject and per-subject aggregates keep their arithmetic, and not
    reversible without the key. Never a plain hash — a bare digest of a
    user id is a rainbow table away from being the id again.

    Idempotent: a value that is already a pseudonym is returned as-is, so
    a redelivered erasure cannot produce a second pseudonym for one
    subject and split its history in two.
    """
    import hashlib
    import hmac

    from django.conf import settings

    value = str(value)
    if value.startswith(PSEUDONYM_PREFIX):
        return value
    digest = hmac.new(
        str(settings.SECRET_KEY).encode(), value.encode(), hashlib.sha256
    ).hexdigest()[:32]
    return f"{PSEUDONYM_PREFIX}{digest}"


def _strip_metadata(rows) -> None:
    """Cut every row's ``metadata`` down to :data:`LEDGER_METADATA_KEYS`."""
    from .models import PromptLog

    changed = []
    for row in rows.exclude(metadata__isnull=True).only("id", "metadata").iterator():
        meta = row.metadata
        if not isinstance(meta, dict):
            row.metadata = None
            changed.append(row)
            continue
        kept = {k: v for k, v in meta.items() if k in LEDGER_METADATA_KEYS}
        if kept != meta:
            row.metadata = kept
            changed.append(row)
        if len(changed) >= 500:
            PromptLog.objects.bulk_update(changed, ["metadata"])
            changed = []
    if changed:
        PromptLog.objects.bulk_update(changed, ["metadata"])


def erase_subject(subject_type: str, subject_key, *, workspace_id=None) -> dict | None:
    """Erase one subject from the prompt ledger: content out, bill kept.

    Scrubs the content columns, cuts ``metadata`` to the accounting keys,
    and pseudonymizes ``user_id`` and ``workspace_id`` on every row
    touched. The economics columns are not read and not written.

    Returns ``{"prompt_logs": n}`` — rows **touched**, which is what the
    receipt carries — or ``None`` when *subject_type* is not one this
    module claims (the caller then owes no receipt: gdpr never created a
    part for it).

    Idempotent: the ids are gone from the subject's key after the first
    run, so a redelivery matches nothing and reports ``0``.

    Note the consequence of pseudonymizing BOTH ids: a row that carried an
    erased person's workspace can no longer be looked up by that workspace
    id either. Per-tenant totals still add up (the pseudonym is stable),
    but the link back to a living tenant is cut along with the link to the
    person — which is what "without the person" costs on a row that names
    both.
    """
    from .models import PromptLog
    from .retention import scrub_queryset

    key = str(subject_key) if subject_key is not None else ""
    if not key:
        return None
    if subject_type == "account":
        # Every row of that person, in every workspace. A ``workspace_id``
        # on an account request is a partition hint for owners that need
        # one; narrowing by it here would leave the subject's rows in
        # every other tenant.
        selected = PromptLog.objects.filter(user_id=key)
    elif subject_type == "workspace":
        selected = PromptLog.objects.filter(workspace_id=key)
    else:
        return None

    # Pin the row set by primary key first: the id columns are about to
    # change, and a queryset that filters on them would stop matching its
    # own rows halfway through the erasure.
    pks = list(selected.values_list("pk", flat=True))
    if not pks:
        return {"prompt_logs": 0}
    rows = PromptLog.objects.filter(pk__in=pks)

    scrub_queryset(rows)
    _strip_metadata(rows)
    for column in ("user_id", "workspace_id"):
        values = {v for v in rows.values_list(column, flat=True) if v}
        for value in values:
            pseudonym = pseudonymize(value)
            if pseudonym != value:
                rows.filter(**{column: value}).update(**{column: pseudonym})
    return {"prompt_logs": len(pks)}


__all__ = [
    "LEDGER_METADATA_KEYS",
    "OWNER",
    "PSEUDONYM_PREFIX",
    "SUBJECT_TYPES",
    "AgentGDPRProvider",
    "erase_subject",
    "pseudonymize",
]
