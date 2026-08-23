"""Scheduled work of stapel-agent — the retention purge in schedulable form.

`PROMPT_LOG_RETENTION_DAYS` and the `purge_prompt_logs` management command
have existed since the AGENT-02 audit; nothing shipped that a scheduler
could reference, so every host had to invent its own cron entry and the
ironmemo deployment ran with no beat at all — a retention policy that was
a number in a settings file. This module is the missing half:
:func:`get_agent_beat_schedule` is the entry a host splices into
`CELERY_BEAT_SCHEDULE`, and `checks.py` warns (``stapel_agent.W017``)
when a process that runs beat has no entry pointing here.

Celery is OPTIONAL and not a dependency of this package.
:func:`purge_prompt_logs` is a plain callable any scheduler (cron, systemd
timer, k8s CronJob) can invoke; when celery is installed it is
additionally registered as a shared task under the stable name below.

Wire it in::

    from stapel_agent.tasks import get_agent_beat_schedule

    CELERY_BEAT_SCHEDULE = {
        **get_agent_beat_schedule(),
        ...
    }
"""
import logging

logger = logging.getLogger(__name__)

#: The name a beat schedule must reference (stable across refactors).
PURGE_TASK_NAME = "stapel_agent.tasks.purge_prompt_logs"

#: Key of the shipped beat entry, so a host can override the cadence by
#: writing the same key after the splat.
PURGE_BEAT_KEY = "agent-prompt-log-retention"


def purge_prompt_logs(*, older_than_days: int | None = None) -> int:
    """Scrub the text of PromptLog rows past the retention window.

    Returns and logs the row count: retention that runs invisibly cannot
    be monitored, and a job nobody can observe is indistinguishable from
    a job that stopped running.
    """
    from .retention import purge_prompt_logs as _purge

    scrubbed = _purge(older_than_days=older_than_days)
    logger.info("agent retention purge: scrubbed %s prompt log row(s)", scrubbed)
    return scrubbed


def get_agent_beat_schedule() -> dict:
    """Beat entry for the retention purge. Add to `CELERY_BEAT_SCHEDULE`."""
    from celery.schedules import crontab

    return {
        PURGE_BEAT_KEY: {
            "task": PURGE_TASK_NAME,
            "schedule": crontab(hour=4, minute=20),  # daily, 04:20 UTC
        },
    }


try:  # pragma: no cover — exercised by whichever profile the host installs
    from celery import shared_task
except ImportError:
    pass
else:
    purge_prompt_logs = shared_task(name=PURGE_TASK_NAME)(purge_prompt_logs)


__all__ = [
    "PURGE_BEAT_KEY",
    "PURGE_TASK_NAME",
    "get_agent_beat_schedule",
    "purge_prompt_logs",
]
