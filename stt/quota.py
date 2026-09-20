"""Ask the providers what is left, before they answer with a refusal.

THE DEFECT THIS EXISTS FOR
    ``stt/failures.py`` closed the half of this that is about *handling* an
    exhausted account: a quota refusal is retryable, the fallback chain gets
    its turn, and ``reason: quota`` lands on the PromptLog row. What it did
    not do is make the condition VISIBLE. The audit that produced this
    module found the empty-wallet state discoverable only three ways, all
    after the fact: a support ticket, a SQL query nobody runs, or a grep
    through worker logs. The account that burned 41 recordings had been
    under 10% for nine days.

    So this module asks. Two sources, one alert:

    * **Periodically** (``stapel_agent.tasks.check_stt_quotas``, hourly by
      default): every configured provider that exposes a balance endpoint
      is polled and its remaining fraction recorded as the gauge
      ``stt_provider_quota_ratio{provider}``. A gauge, not a counter: the
      question is "how full is it now", and a restart must not reset the
      answer to zero.
    * **On the live refusal**: the moment a transcription is declined with
      ``reason: quota``, the same alert fires with ``ratio=0.0``. The
      periodic poll can be up to an hour stale and some providers expose
      no endpoint at all, so the refusal itself has to be a first-class
      signal — that is what makes "the next failure" visible without
      reading a log.

THREE OUTPUTS, ONE CALL
    :func:`notify_quota_low` does all three, always in the same order, so a
    deployment can pick whichever it already watches:

    1. ``logger.warning`` — always, and the floor. A host with no metrics
       backend, no alerts module and no receivers still has the line.
    2. the Django signal :data:`provider_quota_low` — a host connects a
       receiver and routes it wherever it likes. Chosen over
       ``stapel_core.comm.signal`` deliberately: that primitive addresses a
       realtime STREAM (a socket group), and an operations alert has no
       stream to be on.
    3. ``stapel_alerts.capture`` — IMPORT-GUARDED. stapel-alerts is not a
       dependency of this package and must not become one; a deployment
       that installed it gets the alert filed, and one that did not loses
       nothing it had.

TWO THRESHOLDS, EACH FIRING ONCE PER CROSSING
    ``WARN_RATIO`` (10%) and ``CRITICAL_RATIO`` (2%). Both are alerted, and
    they are separate severities rather than one repeated line, because
    they ask for different things: at 10% someone should plan a top-up, at
    2% someone should do it now. The scheduled poll is its own
    deduplication — once an hour per provider — so the sweep keeps no
    suppression state; a watchdog that remembers what it already said is a
    watchdog that goes quiet after a restart at exactly the wrong moment.

THE REFUSAL PATH IS RATE-LIMITED, AND ONLY IT
    The sweep runs hourly; refusals run at the rate customers upload. A
    client stand exhausted one provider's allowance and every recording
    that hit it raised its own alert — ten recordings, ten pages, one
    fact. So the live-refusal path (and only it) carries a throttle:
    ``ERROR_LOG_INTERVAL_SECONDS`` (3600) per provider, the first refusal
    in the window logged at ERROR and alerted, the rest counted and
    folded into the next one. The count is carried in the message, so the
    line still says how bad it is rather than hiding the scale it
    suppressed.

    The throttle is per PROCESS, deliberately: shared state would need a
    cache every deployment has to configure, and the failure mode of
    getting it wrong is silence. Four workers mean at most four lines an
    hour instead of four hundred, which is the difference that matters.

THE STATE IS A GAUGE, NOT ONLY A LINE
    ``stt_provider_quota_exhausted{provider}`` is 1 while a provider is
    known to be refusing for quota and 0 once one of its calls succeeds
    or a poll finds headroom. A log line answers "did something happen";
    a gauge answers "is it happening NOW", which is the question an alert
    rule and a dashboard both ask. It is recorded for every provider that
    refuses, including the ones with no balance endpoint at all — which
    is most of them, and exactly the case
    ``stt_provider_quota_ratio`` cannot cover.
"""
from __future__ import annotations

import logging

import django.dispatch

from .base import ProviderQuota

logger = logging.getLogger(__name__)

#: Sent when a provider's remaining allowance crosses a configured
#: threshold, and when a live call is refused for ``reason: quota``.
#:
#: Receivers get ``sender`` (this module), ``provider`` (str),
#: ``ratio`` (float 0.0–1.0), ``severity`` (``"warning"`` | ``"critical"``),
#: ``quota`` (:class:`~stapel_agent.stt.base.ProviderQuota` or ``None`` —
#: ``None`` on the live-refusal path, where no balance was fetched) and
#: ``source`` (``"watchdog"`` | ``"refusal"``)::
#:
#:     from stapel_agent.stt.quota import provider_quota_low
#:
#:     @receiver(provider_quota_low)
#:     def page_someone(sender, provider, ratio, severity, **kw): ...
provider_quota_low = django.dispatch.Signal()

#: The gauge name. Unprefixed here — ``stapel_core.observability`` applies
#: the deployment's own namespace.
QUOTA_GAUGE = "stt_provider_quota_ratio"

#: 1 while a provider is known to be refusing calls for quota, 0 once one
#: of its calls succeeds or a poll finds headroom. Unlike the ratio gauge
#: this one needs no balance endpoint, so it covers every provider.
EXHAUSTED_GAUGE = "stt_provider_quota_exhausted"

SEVERITY_WARNING = "warning"
SEVERITY_CRITICAL = "critical"

#: The throttle key for one provider's refusals, in the shared slot table.
def _slot_key(provider: str) -> str:
    return f"stt_provider_out_of_credits:{provider}"


def watchdog_settings() -> dict:
    """``STT_QUOTA_WATCHDOG``, with the shipped defaults filled in.

    Read at call time, like everything else in this package, so a test or a
    host can flip ``ENABLED`` without an import-order dance.
    """
    from ..conf import agent_settings

    defaults = agent_settings.defaults["STT_QUOTA_WATCHDOG"]
    try:
        configured = agent_settings.STT_QUOTA_WATCHDOG or {}
    except Exception:  # Django absent or settings not configured
        configured = {}
    if not isinstance(configured, dict):
        logger.warning(
            "stapel-agent: STAPEL_AGENT['STT_QUOTA_WATCHDOG'] is %s, not a "
            "dict — using the defaults.", type(configured).__name__,
        )
        configured = {}
    return {**defaults, **configured}


def enabled() -> bool:
    return bool(watchdog_settings().get("ENABLED"))


def severity_for(ratio: float, settings: dict | None = None) -> str | None:
    """``"critical"``, ``"warning"`` or ``None`` for a remaining *ratio*.

    Critical is checked first: an account under 2% is also under 10%, and
    the louder of the two is the one an operator needs.
    """
    conf = settings if settings is not None else watchdog_settings()
    try:
        critical = float(conf.get("CRITICAL_RATIO"))
        warn = float(conf.get("WARN_RATIO"))
    except (TypeError, ValueError):
        logger.warning(
            "stapel-agent: STT_QUOTA_WATCHDOG thresholds are not numbers — "
            "no quota alert can be raised until they are."
        )
        return None
    if ratio < critical:
        return SEVERITY_CRITICAL
    if ratio < warn:
        return SEVERITY_WARNING
    return None


def record_gauge(provider: str, ratio: float) -> None:
    """Record ``stt_provider_quota_ratio{provider}``. Never raises.

    ``stapel_core.observability`` already swallows a backend failure; the
    guard here is for the older cores that have no such module at all —
    this package's floor covers it, but a metric is not worth a crash even
    when the floor is wrong.
    """
    try:
        from stapel_core.observability.metrics import gauge

        gauge(
            QUOTA_GAUGE,
            float(ratio),
            {"provider": provider},
            description=(
                "Fraction of the STT provider's prepaid allowance still "
                "unspent (1.0 = untouched, 0.0 = exhausted)."
            ),
            # Every worker reads the SAME number from the provider's API, so
            # the freshest reading is the truth: `livesum` would multiply the
            # remaining allowance by the worker count and `livemax` would
            # keep reporting the most optimistic stale one. The library
            # default would emit one series per pid.
            multiprocess_mode="livemostrecent",
        )
    except Exception:  # pragma: no cover - no backend / ancient core
        logger.debug(
            "stapel-agent: could not record the %s gauge for %s",
            QUOTA_GAUGE, provider, exc_info=True,
        )


def record_exhausted(provider: str, exhausted: bool) -> None:
    """Record ``stt_provider_quota_exhausted{provider}``. Never raises.

    Written on both edges — 1 when a call is refused for quota, 0 when
    one succeeds — because a gauge that is only ever set to 1 is a
    counter with a misleading name, and an operator watching it would
    never see the recovery.
    """
    from ..provider_health import record_state_gauge

    record_state_gauge(
        EXHAUSTED_GAUGE,
        provider,
        1.0 if exhausted else 0.0,
        description=(
            "1 while this STT provider is refusing calls because its "
            "quota/billing allowance is exhausted, 0 otherwise."
        ),
    )


def error_log_interval(settings: dict | None = None) -> float:
    """Seconds between two loud refusals for one provider.

    ``STT_QUOTA_WATCHDOG["ERROR_LOG_INTERVAL_SECONDS"]`` when the
    deployment states one — the STT chain may legitimately want a
    different cadence from the text one — otherwise the fleet-wide
    ``PROVIDER_ALERT_INTERVAL_SECONDS``.
    """
    from ..provider_health import alert_interval

    conf = settings if settings is not None else watchdog_settings()
    stated = conf.get("ERROR_LOG_INTERVAL_SECONDS")
    if stated is None:
        return alert_interval()
    try:
        return float(stated)
    except (TypeError, ValueError):
        logger.warning(
            "stapel-agent: STT_QUOTA_WATCHDOG['ERROR_LOG_INTERVAL_SECONDS'] "
            "is not a number — using the fleet default.",
        )
        return alert_interval()


def _claim_refusal_slot(provider: str, interval: float) -> tuple[bool, int]:
    """``(be_loud, suppressed_since_the_last_loud_one)`` for one refusal."""
    from ..provider_health import claim_slot

    return claim_slot(_slot_key(provider), interval)


def reset_refusal_throttle(provider: str = "") -> None:
    """Forget what was already said, for *provider* or for all of them.

    Called when a provider serves a request again: the next exhaustion is
    a NEW fact and has to be as loud as the first one was.
    """
    from ..provider_health import clear_slot

    clear_slot(_slot_key(provider) if provider else "")


def notify_quota_low(
    provider: str,
    ratio: float,
    *,
    severity: str,
    quota: ProviderQuota | None = None,
    source: str = "watchdog",
    detail: str = "",
    log_level: int = logging.WARNING,
) -> None:
    """Raise the alert on all three seams. Never raises.

    See the module docstring for why there are three and why the log is
    unconditional.
    """
    human = quota.to_dict() if quota is not None else {}
    message = (
        f"stapel-agent: STT provider {provider!r} is at "
        f"{ratio * 100:.1f}% of its allowance ({severity})"
    )
    if human:
        message += (
            f" — {human['remaining']:.0f} of {human['limit']:.0f} "
            f"{human['unit'] or 'units'} left"
        )
    if detail:
        message += f" — {detail}"
    logger.log(log_level, "%s [source=%s]", message, source)

    context = {
        "provider": provider,
        "ratio": round(float(ratio), 6),
        "severity": severity,
        "source": source,
        **({"quota": human} if human else {}),
        **({"detail": detail[:500]} if detail else {}),
    }

    try:
        provider_quota_low.send(
            sender=__name__,
            provider=provider,
            ratio=float(ratio),
            severity=severity,
            quota=quota,
            source=source,
        )
    except Exception:  # pragma: no cover - a receiver's bug is not ours
        logger.warning(
            "stapel-agent: a provider_quota_low receiver raised", exc_info=True
        )

    # Through provider_health.capture_alert, not through its own copy of the
    # import-and-swallow: that copy is how the STT surface inherited the same
    # silence as the text one when `from stapel_alerts import capture` started
    # returning a module. One helper means one place where a broken alert path
    # becomes a loud ERROR (`alert_path_broken:<exc class>`), for both
    # surfaces. An absent stapel-alerts stays silent — the log line above is
    # the alert, which is the whole reason it is unconditional.
    from ..provider_health import ALERT_KIND, capture_alert

    capture_alert(
        message,
        level="error" if severity == SEVERITY_CRITICAL else "warning",
        kind=ALERT_KIND,
        context=context,
    )


def report_quota_refusal(provider: str, detail: str = "") -> None:
    """A live call was declined for ``reason: quota`` — alert on it.

    Always ``critical`` and always ``ratio=0.0``: the provider has just
    said, about this very second, that it will not serve the request. That
    is a stronger statement than any poll, and the two must not be
    averaged into one lukewarm number.

    Quiet when the watchdog is disabled — one switch owns both paths, so
    turning it off turns off all of it rather than half.

    LOUD ONCE PER PROVIDER PER WINDOW. The gauges are written on every
    refusal (they are idempotent and carry no volume), the ERROR line and
    the alert at most once per ``ERROR_LOG_INTERVAL_SECONDS``, carrying
    the number of refusals folded into it. An exhausted account refuses
    once per upload, and a page per upload is a page nobody reads.
    """
    if not enabled():
        return
    record_gauge(provider, 0.0)
    record_exhausted(provider, True)

    loud, suppressed = _claim_refusal_slot(provider, error_log_interval())
    if not loud:
        logger.info(
            "stapel-agent: STT provider %r refused for quota again "
            "(%d since the last alert, suppressed for up to %.0fs)",
            provider, suppressed, error_log_interval(),
        )
        return

    if suppressed:
        detail = (
            f"{detail} [+{suppressed} further refusal(s) since the last alert]"
            if detail
            else f"+{suppressed} further refusal(s) since the last alert"
        )
    notify_quota_low(
        provider,
        0.0,
        severity=SEVERITY_CRITICAL,
        quota=None,
        source="refusal",
        detail=detail,
        # ERROR, not WARNING: unlike a low balance, this is not a
        # forecast — recordings are failing right now.
        log_level=logging.ERROR,
    )


def report_quota_served(provider: str) -> None:
    """*provider* answered a call — it is not exhausted. Never raises.

    The other edge of :data:`EXHAUSTED_GAUGE`, and the reset of the
    refusal throttle: after a top-up the first new exhaustion has to be
    as loud as the first one ever was, and a gauge that only ever rises
    would keep an alert rule firing over an account that was refilled an
    hour ago.
    """
    if not enabled():
        return
    reset_refusal_throttle(provider)
    record_exhausted(provider, False)


def provider_quota(name: str, *, timeout_seconds: int | None = None):
    """One provider's balance, or ``None``. Never raises.

    ``None`` covers three different situations on purpose — not registered,
    no balance endpoint, endpoint unreachable — because the watchdog's job
    is to alert on a LOW balance, and "we do not know" is not low. Each is
    logged distinctly so an operator can tell them apart.
    """
    from ..services import get_stt_provider

    try:
        backend = get_stt_provider(name)
    except Exception as exc:
        logger.info(
            "stapel-agent: quota watchdog skipped %r — not resolvable (%s)",
            name, exc,
        )
        return None
    try:
        return backend.quota_status(timeout_seconds=timeout_seconds)
    except Exception:  # pragma: no cover - an adapter must not break the sweep
        logger.warning(
            "stapel-agent: quota_status() raised for %r — the sweep "
            "continues without it", name, exc_info=True,
        )
        return None


def check_provider_quotas(*, timeout_seconds: int | None = None) -> list[dict]:
    """Poll every configured STT provider. Returns one row per answer.

    The body of the scheduled task (:func:`stapel_agent.tasks.check_stt_quotas`)
    and a plain callable any other scheduler can run. Providers that expose
    no balance are absent from the result rather than present with a zero —
    see :meth:`SttProvider.quota_status`.
    """
    if not enabled():
        logger.debug(
            "stapel-agent: STT quota watchdog is disabled "
            "(STAPEL_AGENT['STT_QUOTA_WATCHDOG']['ENABLED'])"
        )
        return []

    from . import registered_stt_providers

    conf = watchdog_settings()
    rows: list[dict] = []
    for name in sorted(registered_stt_providers()):
        quota = provider_quota(name, timeout_seconds=timeout_seconds)
        if quota is None:
            continue
        ratio = quota.remaining_ratio
        record_gauge(name, ratio)
        # The poll is the only thing that can clear the exhausted gauge
        # for a provider nobody is calling any more — a stand that fell
        # back to a second engine would otherwise stay red until someone
        # re-routed traffic back at the refilled one.
        record_exhausted(name, ratio <= 0.0)
        if ratio > 0.0:
            reset_refusal_throttle(name)
        severity = severity_for(ratio, conf)
        if severity:
            notify_quota_low(
                name, ratio, severity=severity, quota=quota, source="watchdog"
            )
        rows.append({**quota.to_dict(), "severity": severity})

    logger.info(
        "stapel-agent: STT quota watchdog surveyed %d provider(s), %d "
        "below threshold",
        len(rows),
        sum(1 for row in rows if row["severity"]),
    )
    return rows


__all__ = [
    "EXHAUSTED_GAUGE",
    "QUOTA_GAUGE",
    "SEVERITY_CRITICAL",
    "SEVERITY_WARNING",
    "check_provider_quotas",
    "enabled",
    "error_log_interval",
    "notify_quota_low",
    "provider_quota",
    "provider_quota_low",
    "record_exhausted",
    "record_gauge",
    "report_quota_refusal",
    "report_quota_served",
    "reset_refusal_throttle",
    "severity_for",
    "watchdog_settings",
]
