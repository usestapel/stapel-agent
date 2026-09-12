"""Retention job for the LLM prompt ledger — see stapel_agent.retention."""
from django.core.management.base import BaseCommand

from ...retention import purge_checkpoints, purge_prompt_logs


class Command(BaseCommand):
    help = (
        "Scrub prompt/system-prompt/response text from PromptLog rows older "
        "than STAPEL_AGENT['PROMPT_LOG_RETENTION_DAYS']. Token counters and "
        "the rows themselves are kept for accounting. Expired provider "
        "checkpoints (stapel_agent.checkpoint) are DELETED in the same run "
        "— they are content with no accounting half to keep."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=None,
            help="Override the configured retention window for this run.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report how many rows would be scrubbed and change nothing.",
        )

    def handle(self, *args, **options):
        count = purge_prompt_logs(
            older_than_days=options["days"], dry_run=options["dry_run"]
        )
        verb = "would scrub" if options["dry_run"] else "scrubbed"
        self.stdout.write(f"purge_prompt_logs: {verb} {count} row(s)")

        checkpoints = purge_checkpoints(dry_run=options["dry_run"])
        verb = "would delete" if options["dry_run"] else "deleted"
        self.stdout.write(f"purge_checkpoints: {verb} {checkpoints} row(s)")
