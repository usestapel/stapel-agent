"""
Agent domain: PromptLog — one row per LLM call (and the cache-by-prompt
store: a repeated identical prompt within CACHE_TTL is served from the
latest ``success`` row instead of calling the provider again).

Carries the full token ledger from system-design 7.16: input/output/
thinking/cache-read/cache-write tokens plus duration, so per-user and
per-source cost accounting needs no other table.
"""

import uuid

from django.db import models


class PromptSource(models.TextChoices):
    LLM_FACADE = "llm_facade", "LLM Facade"
    TRANSLATE = "translate", "Translate"
    OTHER = "other", "Other"


class PromptStatus(models.TextChoices):
    SUCCESS = "success", "Success"
    FAILURE = "failure", "Failure"
    TIMEOUT = "timeout", "Timeout"
    ERROR = "error", "Error"


class PromptLog(models.Model):
    """Immutable log of one LLM completion attempt."""

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
    user_id = models.CharField(max_length=64, null=True, blank=True, db_index=True)
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
        ]

    def __str__(self):
        return f"{self.source}/{self.model_size} [{self.status}] {self.model}"
