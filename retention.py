"""PromptLog retention — scrub the text, keep the ledger.

A PromptLog row is two things at once: an accounting record (who spent
how many tokens on which model) and a copy of the conversation (prompt,
system prompt, full response, error text). The first has to survive for
cost and audit queries; the second is customer content and must not sit
in a table forever because nobody wrote the job that removes it — which
is exactly what the 2026-08-11 audit found (AGENT-02: "plaintext with no
discovered retention path").

So retention here scrubs rather than deletes: after
``PROMPT_LOG_RETENTION_DAYS`` the text columns are emptied and the row
keeps its counters. ``PROMPT_LOG_RETENTION_DAYS = None`` disables the
cut-off entirely — an explicit decision a host has to make, not a
default that quietly keeps everything.

The same scrub is the first half of an erasure — which then also cuts
``metadata`` to the accounting keys and pseudonymizes the id columns; see
:func:`stapel_agent.gdpr.erase_subject`. Retention stops at the scrub on
purpose: an old row still belongs to a live customer, so its ids stay.
"""
from __future__ import annotations

from .conf import agent_settings

#: Columns holding caller/model content. Everything else on the row is
#: metadata (ids, counters, timestamps, status) and survives a scrub.
CONTENT_FIELDS = ("prompt", "system_prompt", "response", "error_message")

#: What a scrubbed text column reads as afterwards — a tombstone, so a
#: reader can tell "removed by retention" from "was never recorded".
SCRUBBED = ""


def scrub_queryset(qs) -> int:
    """Empty the content columns of every row in *qs*. Returns the count.

    One UPDATE, no row iteration: retention runs over tables that are
    large by construction.
    """
    return qs.update(
        prompt=SCRUBBED,
        system_prompt=None,
        response=None,
        error_message=None,
    )


def purge_prompt_logs(*, older_than_days: int | None = None, dry_run: bool = False) -> int:
    """Scrub the text of PromptLog rows older than the retention window.

    *older_than_days* overrides ``PROMPT_LOG_RETENTION_DAYS`` for one run.
    Returns the number of rows affected (or that would be, when
    *dry_run*). A ``None`` window on both sides is a no-op returning 0 —
    the caller asked for no retention limit and gets none.
    """
    from datetime import timedelta

    from django.utils import timezone

    from .models import PromptLog

    days = (
        older_than_days
        if older_than_days is not None
        else agent_settings.PROMPT_LOG_RETENTION_DAYS
    )
    if days is None:
        return 0

    cutoff = timezone.now() - timedelta(days=int(days))
    # Already-scrubbed rows are excluded so a repeated run reports the
    # work it actually did, not the size of the archive.
    qs = PromptLog.objects.filter(created_at__lt=cutoff).exclude(
        prompt=SCRUBBED, system_prompt__isnull=True, response__isnull=True
    )
    if dry_run:
        return qs.count()
    return scrub_queryset(qs)


__all__ = ["CONTENT_FIELDS", "SCRUBBED", "purge_prompt_logs", "scrub_queryset"]
