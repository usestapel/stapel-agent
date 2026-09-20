"""Is a provider refusing right now, and has anyone been told once?

THE TWO DEFECTS THIS CLOSES, ONE ON EACH SURFACE
    A client stand ran nine days with an ElevenLabs account under 10% and
    learned about it from a support ticket. The STT watchdog
    (:mod:`stapel_agent.stt.quota`) answered that half. On 2026-09-20 the
    same stand's TEXT provider answered ``403 "your team has either used
    all available credits or reached its monthly spending limit"`` and
    every recording that day lost its summary — silently, because
    summarisation is best-effort by design. Nothing was gauged and
    nothing was paged.

    And the fix for "nothing was paged" has a failure mode of its own: an
    exhausted account refuses once per customer upload, so an alert per
    refusal is a pager that fires a hundred times about one fact and
    trains everyone to mute it.

SO: A GAUGE FOR THE STATE, A THROTTLED ERROR FOR THE NEWS
    * ``<surface>_provider_out_of_credits{provider}`` — 1 while the
      provider is refusing for quota/billing, 0 once it serves a call
      again. A gauge answers "is it happening NOW", which is the question
      an alert rule asks; a log line only ever answers "did it happen".
    * one ERROR + one ``stapel_alerts`` capture per provider per
      ``PROVIDER_ALERT_INTERVAL_SECONDS`` (3600), carrying the count of
      refusals folded into it, under a stable fingerprint token
      (``llm_provider_out_of_credits:<provider>``) so the alert store
      groups every occurrence into one issue instead of one per message.

    The throttle is per PROCESS, deliberately: shared state would need a
    cache every deployment has to configure, and the failure mode of
    getting that wrong is silence. Four workers mean at most four lines
    an hour instead of four hundred, which is the difference that
    matters.

Nothing here raises. A provider that cannot be paid is a bad hour; an
alerting path that can end a request is a bad architecture.
"""
from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

#: The text-LLM gauge and the fingerprint token that groups its alerts.
LLM_OUT_OF_CREDITS_GAUGE = "llm_provider_out_of_credits"
LLM_OUT_OF_CREDITS_FINGERPRINT = "llm_provider_out_of_credits"

#: The fingerprint token for "the alert path itself is broken" — the one
#: failure an alert library cannot report through itself. See capture_alert.
ALERT_PATH_BROKEN_FINGERPRINT = "alert_path_broken"

#: The stapel-alerts event KIND every alert here is filed under.
#:
#: "manual" is that library's name for an explicit ``capture(...)`` — its kind
#: is a closed set (exception / log / dlq / monitoring / manual) describing the
#: INPUT, not the subject. We used to pass "provider_quota", which is not a
#: member: the store silently normalised it to "exception", so every provider
#: alert we raised was filed in the tracker as an unhandled exception. What the
#: alert is ABOUT is carried by the fingerprint token and the context, which is
#: where a reader (and a query) can actually use it.
ALERT_KIND = "manual"

#: Fallback when no deployment states one.
DEFAULT_ALERT_INTERVAL_SECONDS = 3600

#: key -> (monotonic time of the last loud report, reports suppressed since)
_slots: dict[str, tuple[float, int]] = {}


def alert_interval() -> float:
    """Seconds between two loud reports about one provider."""
    from .conf import agent_settings

    try:
        return float(agent_settings.PROVIDER_ALERT_INTERVAL_SECONDS)
    except Exception:
        return float(DEFAULT_ALERT_INTERVAL_SECONDS)


def claim_slot(key: str, interval: float) -> tuple[bool, int]:
    """``(be_loud, suppressed_since_the_last_loud_one)`` for one report.

    ``interval <= 0`` disables the throttle — a test's setting, not a
    stand's.
    """
    now = time.monotonic()
    last, suppressed = _slots.get(key, (None, 0))
    if last is None or interval <= 0 or (now - last) >= interval:
        _slots[key] = (now, 0)
        return True, suppressed
    _slots[key] = (last, suppressed + 1)
    return False, suppressed + 1


def clear_slot(key: str = "") -> None:
    """Forget what was already said, for *key* or for all of them.

    Called when a provider serves a request again: the next exhaustion is
    a NEW fact and has to be as loud as the first one was.
    """
    if key:
        _slots.pop(key, None)
    else:
        _slots.clear()


def record_state_gauge(
    name: str, provider: str, value: float, *, description: str = ""
) -> None:
    """Record ``<name>{provider}``. Never raises.

    ``stapel_core.observability`` already swallows a backend failure; the
    guard here is for a core too old to have the module at all — a metric
    is not worth a crash even when the floor is wrong.
    """
    try:
        from stapel_core.observability.metrics import gauge

        gauge(name, float(value), {"provider": provider}, description=description)
    except Exception:  # pragma: no cover - no backend / ancient core
        logger.debug(
            "stapel-agent: could not record the %s gauge for %s",
            name, provider, exc_info=True,
        )


def capture_alert(message: str, *, kind: str, context: dict, level: str) -> bool:
    """File the alert with stapel-alerts if the deployment has it.

    Returns whether the alert path was walked without raising. The single
    place in this package that talks to the alert store, so the two rules
    below hold for every alert we raise rather than for whichever call site
    remembered them.

    IMPORT-GUARDED: stapel-alerts is not a dependency of this package and
    must not become one. A deployment that installed it gets the alert
    filed; one that did not loses nothing it had, because the log line the
    caller already emitted is the floor. ``ImportError`` is therefore a
    configuration, not a fault, and is silent.

    A FAILURE OF THE ALERT PATH ITSELF IS LOUD, ONCE
        Until 0.30.1 this swallowed everything into a WARNING on this
        module's own logger, and on a production host that is indistinguishable
        from silence. stapel-alerts 0.2.3 exported ``capture`` as a function
        AND shipped a submodule of the same name, so once anything imported
        the submodule — its own log handler does, on the first WARNING record
        of the process — ``from stapel_alerts import capture`` handed back a
        MODULE and every call here raised ``TypeError: 'module' object is not
        callable``. This except clause caught it, wrote a WARNING nobody
        routes, and the provider-out-of-credits alert reached the tracker zero
        times across two days of a provider refusing.

        So a broken alert path now logs at ERROR — the level the fleet's
        Telegram handler carries — under the distinct fingerprint
        ``alert_path_broken:<exception class>``, throttled by the same
        per-process slot the alerts themselves use so a per-request failure
        cannot become a per-request page. An alerting failure still never
        reaches the caller: this returns False, it does not raise.
    """
    try:
        from stapel_alerts import capture
    except ImportError:
        return False
    try:
        capture(message, level=level, kind=kind, context=context)
        return True
    except Exception as exc:
        report_alert_path_broken(exc)
        return False


def report_alert_path_broken(exc: BaseException) -> bool:
    """The alert path itself failed. Say so at ERROR, once per window.

    Returns whether this one was loud. Deliberately NOT routed back through
    :func:`capture_alert`: the thing that is broken is the alert store, and
    a report about it that goes through the alert store is a report nobody
    gets.
    """
    fingerprint = f"{ALERT_PATH_BROKEN_FINGERPRINT}:{type(exc).__name__}"
    loud, suppressed = claim_slot(fingerprint, alert_interval())
    if not loud:
        logger.info(
            "stapel-agent: %s — again (%d since the last ERROR)",
            fingerprint, suppressed,
        )
        return False
    message = (
        f"stapel-agent: {fingerprint} — filing an alert with stapel-alerts "
        f"raised {type(exc).__name__}: {exc}. ALERTS FROM THIS PROCESS ARE "
        f"NOT REACHING THE TRACKER; every alert raised while this holds is "
        f"lost, not delayed"
    )
    if suppressed:
        message += f" [+{suppressed} further failure(s) since the last ERROR]"
    logger.error(message, exc_info=True)
    return True


def report_llm_out_of_credits(provider: str, detail: str = "") -> bool:
    """A text provider refused for quota/billing. Returns whether it was loud.

    The gauge is written every time (idempotent, no volume); the ERROR
    and the alert at most once per provider per window.
    """
    record_state_gauge(
        LLM_OUT_OF_CREDITS_GAUGE,
        provider,
        1.0,
        description=(
            "1 while this text-LLM provider is refusing calls because its "
            "credits or spending limit are exhausted, 0 otherwise."
        ),
    )

    fingerprint = f"{LLM_OUT_OF_CREDITS_FINGERPRINT}:{provider}"
    loud, suppressed = claim_slot(fingerprint, alert_interval())
    if not loud:
        logger.info(
            "stapel-agent: %s — refused again (%d since the last alert)",
            fingerprint, suppressed,
        )
        return False

    message = (
        f"stapel-agent: {fingerprint} — LLM provider {provider!r} is out of "
        f"credits or over its spending limit; calls are being served by the "
        f"fallback chain where one is configured"
    )
    if suppressed:
        message += f" [+{suppressed} further refusal(s) since the last alert]"
    if detail:
        message += f" — {detail[:300]}"
    logger.error(message)
    capture_alert(
        message,
        kind=ALERT_KIND,
        level="error",
        context={
            "fingerprint": fingerprint,
            "provider": provider,
            "surface": "llm",
            **({"detail": detail[:500]} if detail else {}),
            **({"suppressed": suppressed} if suppressed else {}),
        },
    )
    return True


def report_llm_served(provider: str) -> None:
    """*provider* answered a call — clear its gauge and re-arm its alert."""
    fingerprint = f"{LLM_OUT_OF_CREDITS_FINGERPRINT}:{provider}"
    if _slots.pop(fingerprint, None) is not None:
        logger.info(
            "stapel-agent: LLM provider %r is serving calls again — alerts "
            "for it are armed anew", provider,
        )
    record_state_gauge(
        LLM_OUT_OF_CREDITS_GAUGE,
        provider,
        0.0,
        description=(
            "1 while this text-LLM provider is refusing calls because its "
            "credits or spending limit are exhausted, 0 otherwise."
        ),
    )


__all__ = [
    "ALERT_PATH_BROKEN_FINGERPRINT",
    "DEFAULT_ALERT_INTERVAL_SECONDS",
    "LLM_OUT_OF_CREDITS_FINGERPRINT",
    "LLM_OUT_OF_CREDITS_GAUGE",
    "alert_interval",
    "capture_alert",
    "claim_slot",
    "clear_slot",
    "record_state_gauge",
    "report_alert_path_broken",
    "report_llm_out_of_credits",
    "report_llm_served",
]
