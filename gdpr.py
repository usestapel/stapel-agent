"""GDPR provider for the prompt ledger.

The PromptLog table holds prompts, system prompts and full model
responses — customer content by any reading of it — and until the
2026-08-11 audit (AGENT-02) it appeared in no export and no erasure
path. It does now: this provider is registered in ``apps.ready()``
alongside every other package's, so a subject request that reaches the
GDPR orchestrator reaches these rows too.

Erasure scrubs the text and keeps the counters. Deleting the rows
outright would destroy the token/cost ledger the finance side reads, and
that ledger holds no personal data once the text is gone — the row keeps
a model name, a status and numbers.
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
        """Erase the content, keep the accounting row (anonymised)."""
        from .retention import scrub_queryset

        rows = self._rows(user_id)
        scrub_queryset(rows)
        # Second statement on purpose: the scrub must have committed
        # before the rows stop being findable by subject id.
        rows.update(user_id=None)

    def anonymize(self, user_id: int) -> None:
        # Same operation as delete(): there is no retained-content case
        # here, because the retained part is numbers.
        self.delete(user_id)


__all__ = ["AgentGDPRProvider"]
