from django.contrib import admin

from .models import PromptLog


@admin.register(PromptLog)
class PromptLogAdmin(admin.ModelAdmin):
    """Read-only: PromptLog is an immutable ledger — rows are written by
    the service layer only (editing one would corrupt token accounting
    and could poison the prompt cache)."""

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

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
