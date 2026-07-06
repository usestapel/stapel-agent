from django.contrib import admin

from stapel_core.django.admin.base import StapelModelAdmin

from .models import PromptLog


@admin.register(PromptLog)
class PromptLogAdmin(StapelModelAdmin):
    """Read-only: PromptLog is an ``@access.ops`` immutable ledger — rows
    are written by the service layer only (editing one would corrupt token
    accounting and could poison the prompt cache). ``StapelModelAdmin``
    reads the ``ops`` declaration and forbids add/change/delete for
    everyone, including the superuser."""

    list_display = [
        "created_at",
        "source",
        "model",
        "model_size",
        "status",
        "input_tokens",
        "output_tokens",
        "duration_ms",
        "user_id",
    ]
    list_filter = ["source", "status", "model_size"]
    search_fields = ["prompt", "user_id", "model"]
    date_hierarchy = "created_at"
    ordering = ["-created_at"]
