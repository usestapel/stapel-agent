"""Scheduled work of stapel-agent — retention, and the STT quota watchdog.

`PROMPT_LOG_RETENTION_DAYS` and the `purge_prompt_logs` management command
have existed since the AGENT-02 audit; nothing shipped that a scheduler
could reference, so every host had to invent its own cron entry and the
client deployment ran with no beat at all — a retention policy that was
a number in a settings file. This module is the missing half:
:func:`get_agent_beat_schedule` is the entry a host splices into
`CELERY_BEAT_SCHEDULE`, and `checks.py` warns (``stapel_agent.W017``)
when a process that runs beat has no entry pointing here.

Celery is OPTIONAL and not a dependency of this package.
:func:`purge_prompt_logs` is a plain callable any scheduler (cron, systemd
timer, k8s CronJob) can invoke; when celery is installed it is
additionally registered as a shared task under the stable name below.

Since 0.25.0 there is a second entry beside it: :func:`check_stt_quotas`,
which asks every STT provider that exposes a balance how much is left and
alerts before the next recording is the one that finds out (see
:mod:`stapel_agent.stt.quota`). Same shape — a plain callable, registered
as a celery task when celery is installed, reachable from any scheduler.

Wire them in::

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

#: The STT provider-balance watchdog (see :mod:`stapel_agent.stt.quota`).
QUOTA_TASK_NAME = "stapel_agent.tasks.check_stt_quotas"
QUOTA_BEAT_KEY = "agent-stt-quota-watchdog"


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


def check_stt_quotas(*, timeout_seconds: int | None = None) -> list[dict]:
    """Ask every STT provider that exposes a balance how much is left.

    Returns one row per provider that answered. Records the gauge
    ``stt_provider_quota_ratio{provider}`` and raises the low-balance
    alert on the way — see :mod:`stapel_agent.stt.quota` for the three
    seams and the two thresholds.

    Hourly by default. An exhausted account is a condition that takes
    days to arrive and minutes to fix, so the cadence only has to beat
    the former; polling a billing endpoint every minute would be a cost
    of its own and, on some plans, a rate-limited one.
    """
    from .stt.quota import check_provider_quotas

    return check_provider_quotas(timeout_seconds=timeout_seconds)


def get_agent_beat_schedule() -> dict:
    """Beat entries for this package. Add to `CELERY_BEAT_SCHEDULE`.

    Two of them since 0.25.0: the retention purge and the STT quota
    watchdog. The entries' schedules are `crontab`/`timedelta` objects, so
    this call needs celery installed — a host with a beat schedule has it
    by definition. Without celery, schedule the callables above (or
    `manage.py purge_prompt_logs`) from cron and declare the retention one
    with ``PROMPT_LOG_RETENTION_SCHEDULED = True``.

    The watchdog entry is returned whether or not
    ``STT_QUOTA_WATCHDOG["ENABLED"]`` is set: the task itself checks, so a
    deployment that flips the setting does not also have to redeploy its
    beat schedule — and a schedule that silently loses an entry when a
    setting changes is the kind of drift nobody notices until the thing
    stops firing.
    """
    from celery.schedules import crontab

    return {
        PURGE_BEAT_KEY: {
            "task": PURGE_TASK_NAME,
            "schedule": crontab(hour=4, minute=20),  # daily, 04:20 UTC
        },
        QUOTA_BEAT_KEY: {
            "task": QUOTA_TASK_NAME,
            "schedule": crontab(minute=7),  # hourly, at :07
        },
    }


try:  # pragma: no cover — exercised by whichever profile the host installs
    from celery import shared_task
except ImportError:
    pass
else:
    purge_prompt_logs = shared_task(name=PURGE_TASK_NAME)(purge_prompt_logs)
    check_stt_quotas = shared_task(name=QUOTA_TASK_NAME)(check_stt_quotas)


__all__ = [
    "PURGE_BEAT_KEY",
    "PURGE_TASK_NAME",
    "QUOTA_BEAT_KEY",
    "QUOTA_TASK_NAME",
    "check_stt_quotas",
    "get_agent_beat_schedule",
    "purge_prompt_logs",
]
