"""
Agent domain: PromptLog — one row per LLM call (and the cache-by-prompt
store: a repeated identical prompt within CACHE_TTL is served from the
latest ``success`` row instead of calling the provider again).

Carries the full token ledger from system-design 7.16: input/output/
thinking/cache-read/cache-write tokens plus duration, so per-user and
per-source cost accounting needs no other table.

THE THREE COLUMNS THAT MAKE IT A METER
--------------------------------------
The token columns were here from the start and the table still could not
answer "what did user X cost us in March", for three separate reasons —
all closed in 0.12.0:

- **Who.** ``user_id`` existed but the comm functions had no way to carry
  one, and product traffic arrives over comm. Every row from real traffic
  read ``user_id = NULL``. ``workspace_id`` did not exist at all, so a
  team wallet had nothing to sum over.
- **How much.** ``pricing.cost_fields()`` computed ``{cost_usd,
  cost_basis}``, attached it to the response, and dropped it. Now it is
  stored, **as computed at call time** — see the note on ``cost_usd``.
- **How much audio.** A transcribe row recorded ``url:<host>`` and a
  wall-clock ``duration_ms``. STT bills per audio hour, so the largest
  spend path in a recordings product was structurally unauditable.
  ``audio_duration_ms`` is that billable quantity.
"""

import uuid

from django.db import models

from stapel_core.access import access


class PromptSource(models.TextChoices):
    LLM_FACADE = "llm_facade", "LLM Facade"
    TRANSLATE = "translate", "Translate"
    TRANSCRIBE = "transcribe", "Transcribe"
    DIARIZE = "diarize", "Diarize"
    SUMMARIZE = "summarize", "Summarize"
    EMBED = "embed", "Embed"
    RERANK = "rerank", "Rerank"
    GENERATE_IMAGE = "generate_image", "Generate Image"
    OTHER = "other", "Other"


class PromptStatus(models.TextChoices):
    SUCCESS = "success", "Success"
    FAILURE = "failure", "Failure"
    TIMEOUT = "timeout", "Timeout"
    ERROR = "error", "Error"


class CostBasis(models.TextChoices):
    """Which number ``cost_usd`` is — the whole point of storing it.

    The values are ``pricing.cost_fields()``'s verbatim, because a second
    vocabulary for one fact is a vocabulary that drifts. ``UNPRICED``
    exists so a row with no rate card stays visibly unknown instead of
    summing into a total as free.
    """

    PROVIDER_TICKS = "provider_ticks", "Reported by the provider"
    PRICING_ESTIMATE = "pricing_estimate", "Estimated from the rate card"
    UNPRICED = "unpriced", "No rate card — cost unknown"


@access.ops
class PromptLog(models.Model):
    """Immutable log of one LLM completion attempt.

    ``@access.ops`` (admin-suite AS-5): a delivery/audit-log-shaped ledger
    written exclusively by the ``services.py`` completion pipeline — nobody
    is expected to add/change/delete a row through the admin (enforced
    below by ``StapelModelAdmin`` reading this declaration).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source = models.CharField(
        max_length=32, choices=PromptSource.choices, db_index=True
    )
    model = models.CharField(max_length=128)
    model_size = models.CharField(max_length=16)
    prompt = models.TextField()
    system_prompt = models.TextField(null=True, blank=True)
    response = models.TextField(null=True, blank=True)
    status = models.CharField(
        max_length=16, choices=PromptStatus.choices, db_index=True
    )
    error_message = models.TextField(null=True, blank=True)
    input_tokens = models.IntegerField(null=True, blank=True)
    output_tokens = models.IntegerField(null=True, blank=True)
    thinking_tokens = models.IntegerField(null=True, blank=True)
    cache_read_tokens = models.IntegerField(null=True, blank=True)
    cache_write_tokens = models.IntegerField(null=True, blank=True)
    duration_ms = models.IntegerField(null=True, blank=True)
    #: Billable audio length for the audio surfaces (transcribe,
    #: diarize) — the quantity an STT invoice is computed from. NOT
    #: ``duration_ms``, which is how long the call took us: a 60-minute
    #: recording transcribed in 14 seconds is 3_600_000 here and 14_000
    #: there, and billing wants the first. ``None`` when the provider
    #: reported no duration (the row is then not reconstructable, and
    #: ``transcribe`` says so in the log).
    audio_duration_ms = models.IntegerField(null=True, blank=True)
    #: USD for this one call, **as computed when the call was made**.
    #: Never recomputed: re-pricing a year-old row against today's rate
    #: card quietly restates history, and the ledger's job is to remember
    #: what was actually charged. ``cost_basis`` says what kind of number
    #: this is; 0.0 with ``cost_basis="unpriced"`` means unknown, not free.
    cost_usd = models.DecimalField(
        max_digits=16, decimal_places=8, null=True, blank=True
    )
    cost_basis = models.CharField(
        max_length=16, choices=CostBasis.choices, null=True, blank=True
    )
    user_id = models.CharField(max_length=64, null=True, blank=True, db_index=True)
    #: The tenant the call was made for. Separate from ``user_id`` because
    #: a team wallet and a person's usage are different questions. Erasure
    #: pseudonymizes both columns (``gdpr.erase_subject``): the counters
    #: and the cost survive, the person and the tenant stop being nameable.
    workspace_id = models.CharField(
        max_length=64, null=True, blank=True, db_index=True
    )
    metadata = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "agent_prompt_log"
        ordering = ["-created_at"]
        indexes = [
            # Short explicit name — auto-generated names exceed the 30-char
            # limit some backends enforce (models.E034).
            models.Index(
                fields=["source", "-created_at"], name="agent_source_created_idx"
            ),
            # The meter's own query: one tenant's spend over a period.
            models.Index(
                fields=["workspace_id", "-created_at"],
                name="agent_ws_created_idx",
            ),
        ]

    def __str__(self):
        return f"{self.source}/{self.model_size} [{self.status}] {self.model}"
