"""The provider alerts, through the REAL stapel-alerts, into a REAL store.

WHY THIS FILE EXISTS AT ALL
    ``test_llm_provider_chain.py`` proves the alert is DECIDED: the gauge is
    written, the ERROR is logged, the throttle holds. It proves nothing about
    the alert being FILED, because it never leaves this package — and on
    2026-09-20 a production host had a text provider refusing for two days
    while the "LLM provider out of credits" alert reached the tracker exactly
    zero times.

    The break was one seam wide. stapel-alerts 0.2.3 exported ``capture`` as a
    function and also shipped a submodule named ``capture``; PEP 562's
    ``__getattr__`` runs only when attribute lookup FAILS, so once anything
    imported the submodule — the alert store's own log handler does, on the
    first WARNING record of the process — ``from stapel_alerts import capture``
    returned a MODULE and every call raised ``TypeError: 'module' object is not
    callable``. Our ``capture_alert`` caught it and wrote a WARNING nobody
    routes.

    A test that mocks ``capture`` cannot see any of that. It asserts against
    the only participant that was never broken.

SO: A REAL STORE, AND BOTH IMPORT ORDERS
    Each case configures a throwaway Django with ``stapel_alerts`` installed in
    owner mode over an in-memory sqlite, runs the real ``report_*`` function,
    and asserts a row landed carrying the fingerprint token the alert rules
    group on.

    In SUBPROCESSES, because the defect is a property of import ORDER and
    binding is per-process and permanent. By the time any in-process test runs,
    something has already imported something, which is exactly why a shared
    interpreter made this invisible for two days. Each order gets a clean one.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

stapel_alerts = pytest.importorskip(
    "stapel_alerts", reason="the real-path test needs the real alert store"
)

#: Order A — the export first. This worked even on 0.2.3.
ORDER_EXPORT_FIRST = "from stapel_alerts import capture  # noqa: F401"

#: Order B — the submodule first. THE REGRESSION: on 0.2.3 this bound the
#: submodule onto the package and every later `from stapel_alerts import
#: capture` in the process got a module.
ORDER_SUBMODULE_FIRST = "import stapel_alerts._capture  # noqa: F401"

#: Order C — how a real service reaches it: the alert store's own log handler
#: imports the private module on the first WARNING record, before any library
#: asks the package for the export.
ORDER_HANDLER_FIRST = textwrap.dedent(
    """
    import logging
    from stapel_alerts.inputs import AlertsLogHandler
    logging.getLogger("warmup").addHandler(AlertsLogHandler())
    logging.getLogger("warmup").warning("first record of the process")
    """
)

_SETTINGS = """
import django
from django.conf import settings

settings.configure(
    SECRET_KEY="test-secret-key-not-for-production",
    INSTALLED_APPS=[
        "django.contrib.contenttypes",
        "django.contrib.auth",
        "stapel_core.django.apps.CommonDjangoConfig",
        "stapel_core.django.users",
        "stapel_agent",
        "stapel_alerts",
    ],
    AUTH_USER_MODEL="users.User",
    DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
    DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
    USE_TZ=True,
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
    STAPEL_BUS_BACKEND="stapel_core.bus.backends.memory.MemoryBus",
    STAPEL_COMM={"OUTBOX_ENABLED": False, "ACTION_TRANSPORT": "inprocess"},
    # Owner mode: this process holds the store, so capture() writes rows here
    # instead of trying to POST them at an OWNER_URL.
    STAPEL_ALERTS={"MODE": "owner", "SERVICE": "svc-test", "RATE_LIMIT": 1000},
    STAPEL_AGENT={"PROVIDER_ALERT_INTERVAL_SECONDS": 3600},
    MIGRATION_MODULES={"users": None, "alerts": None, "agent": None},
)
django.setup()

from django.test.utils import setup_test_environment
setup_test_environment()
from django.test.runner import DiscoverRunner
_runner = DiscoverRunner(verbosity=0, interactive=False)
_old = _runner.setup_databases()
"""

#: THE ASSERTION IS ON ErrorEvent, NOT Issue, AND THAT IS THE WHOLE POINT.
#:
#: A first draft asserted only that some ISSUE carried the fingerprint token,
#: and it passed against the broken stapel-alerts 0.2.3 — not because the
#: alert worked, but because ``report_llm_out_of_credits`` logs its message at
#: ERROR immediately before filing it, and the alert store installs a handler
#: on the ROOT logger. The tracker got a row with all the right words in it
#: while the call this file exists to protect raised TypeError into a
#: swallowed except. The gate would have proved nothing, twice.
#:
#: Grouping is why the issue alone cannot answer it: the log line and the
#: captured message are the SAME TEXT, so they fingerprint together into ONE
#: issue, whose ``kind`` is whatever created it first — ``log``. The per
#: occurrence ``ErrorEvent`` is the honest record: the handler's event carries
#: ``kind="log"`` and the handler's own context (logger/module/func/line),
#: while ``capture_alert``'s event carries ``kind="manual"`` and the context WE
#: passed. Requiring an event with our kind AND our provider in its context is
#: a statement about the capture path and nothing else.
#:
#: Writing this is also what found the mislabelling: the first version of the
#: assertion looked for ``kind="provider_quota"``, the kind this package was
#: passing, and no event ever had it. ``provider_quota`` is not a member of the
#: store's closed EventKind set, so every provider alert we filed was being
#: normalised to ``exception`` and read in the tracker as a crash.
_ASSERT_ROW = """
from stapel_alerts.models import ErrorEvent, Issue

events = list(ErrorEvent.objects.all())
inventory = (
    " | ".join("%s ctx=%s" % (e.kind, sorted(e.context)) for e in events) or "<empty>"
)
captured = [
    e for e in events
    if e.kind == "manual" and e.context.get("provider") == PROVIDER
]
assert captured, (
    "NOTHING REACHED THE STORE THROUGH capture(). The store holds %d event(s), "
    "none of them filed by the alert path (kind='manual', "
    "context.provider=%r) — any row present came from the root log handler, "
    "which is how this defect stayed invisible for two days. Events: %s"
    % (len(events), PROVIDER, inventory)
)
titles = " | ".join(Issue.objects.values_list("title", flat=True))
assert EXPECTED in titles, (
    "no issue carries the fingerprint token %r; got: %s" % (EXPECTED, titles)
)
print("OK", len(captured), len(events))
"""


def _run(
    order: str, body: str, expected: str, provider: str
) -> subprocess.CompletedProcess:
    """One clean interpreter: configure, then take *order*, then alert.

    The order preamble runs AFTER ``settings.configure`` because that is the
    real sequence — a service boots Django and only then writes its first log
    record. It is still before anything in this package has asked the alerts
    package for its export, which is the only ordering the defect cared about.
    """
    script = "\n".join(
        [
            _SETTINGS,
            order,
            f"EXPECTED = {expected!r}",
            f"PROVIDER = {provider!r}",
            body,
            _ASSERT_ROW,
        ]
    )
    return subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True
    )


ORDERS = [
    pytest.param(ORDER_EXPORT_FIRST, id="export-first"),
    pytest.param(ORDER_SUBMODULE_FIRST, id="submodule-first"),
    pytest.param(ORDER_HANDLER_FIRST, id="handler-first"),
]


@pytest.mark.parametrize("order", ORDERS)
def test_the_llm_out_of_credits_alert_reaches_the_store(order):
    """``report_llm_out_of_credits`` files a real issue, under every order."""
    body = """
from stapel_agent.provider_health import report_llm_out_of_credits
assert report_llm_out_of_credits("acme-llm", detail="403 out of credits") is True
"""
    proc = _run(order, body, "llm_provider_out_of_credits:acme-llm", "acme-llm")
    assert proc.returncode == 0, f"\n--- order ---\n{order}\n--- stderr ---\n{proc.stderr}"
    assert proc.stdout.startswith("OK")


@pytest.mark.parametrize("order", ORDERS)
def test_the_stt_quota_alert_reaches_the_store(order):
    """The STT surface goes through the same helper, so it gets the same proof."""
    body = """
from stapel_agent.stt.quota import notify_quota_low
notify_quota_low("acme-stt", 0.01, severity="critical", source="watchdog")
"""
    proc = _run(order, body, "acme-stt", "acme-stt")
    assert proc.returncode == 0, f"\n--- order ---\n{order}\n--- stderr ---\n{proc.stderr}"
    assert proc.stdout.startswith("OK")


def test_a_broken_alert_path_is_an_ERROR_with_its_own_fingerprint(caplog):
    """The regression that hid the regression.

    When ``capture`` raises — a module where a function was expected being
    the case that actually happened — the caller must not be told, and the
    operator must. ERROR is the level the fleet's Telegram handler carries.
    """
    import logging

    from stapel_agent import provider_health

    provider_health.clear_slot()

    def _explode(*a, **kw):
        raise TypeError("'module' object is not callable")

    import stapel_alerts

    original = stapel_alerts.capture
    stapel_alerts.capture = _explode
    try:
        with caplog.at_level(logging.ERROR, logger="stapel_agent.provider_health"):
            filed = provider_health.capture_alert(
                "anything", kind="provider_quota", level="error", context={}
            )
    finally:
        stapel_alerts.capture = original
        provider_health.clear_slot()

    assert filed is False, "a failed capture must not report success"
    (record,) = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert "alert_path_broken:TypeError" in record.getMessage()
    assert "NOT REACHING THE TRACKER" in record.getMessage()


def test_a_broken_alert_path_pages_once_per_window_and_counts_the_rest(
    caplog, settings
):
    """A provider refusing per request must not page per request.

    Ten failures inside one window are one ERROR. What the window swallowed
    is not lost — it is carried into the next loud report, so the operator
    reads "this happened 9 more times" rather than inferring it from silence.
    """
    import logging

    from stapel_agent import provider_health

    settings.STAPEL_AGENT = {
        **getattr(settings, "STAPEL_AGENT", {}),
        "PROVIDER_ALERT_INTERVAL_SECONDS": 3600,
    }
    provider_health.clear_slot()

    def _explode(*a, **kw):
        raise TypeError("'module' object is not callable")

    import stapel_alerts

    original = stapel_alerts.capture
    stapel_alerts.capture = _explode
    try:
        with caplog.at_level(logging.ERROR, logger="stapel_agent.provider_health"):
            for _ in range(10):
                provider_health.capture_alert(
                    "anything", kind="provider_quota", level="error", context={}
                )

        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(errors) == 1, f"expected one ERROR per window, got {len(errors)}"

        # A window later: 0 disables the throttle, which is how the suite ends
        # a window without moving a monotonic clock.
        settings.STAPEL_AGENT = {
            **settings.STAPEL_AGENT,
            "PROVIDER_ALERT_INTERVAL_SECONDS": 0,
        }
        caplog.clear()
        with caplog.at_level(logging.ERROR, logger="stapel_agent.provider_health"):
            provider_health.capture_alert(
                "anything", kind="provider_quota", level="error", context={}
            )
    finally:
        stapel_alerts.capture = original
        provider_health.clear_slot()

    (record,) = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert "+9 further failure(s)" in record.getMessage()


def test_capture_is_not_shadowed_in_the_installed_alerts():
    """The floor this package's alerts depend on, asserted where it is used.

    IN A SUBPROCESS, and the reason is the defect itself. Checked in-process,
    this assertion passes against the BROKEN stapel-alerts 0.2.3 — by the time
    pytest has collected this file something has resolved the export and
    cached the function, so the check looks at a package that was already put
    in its working state. That is the same blindness that let 0.2.3 ship.

    So: a clean interpreter, and the submodule given its chance to shadow
    first. On a fixed alerts there is no ``stapel_alerts.capture`` module to
    import and the ModuleNotFoundError IS the pass; on a shadowed one the
    import succeeds, binds, and the export comes back a module.
    """
    script = textwrap.dedent(
        """
        import importlib
        import types

        import stapel_alerts

        try:
            importlib.import_module("stapel_alerts.capture")
        except ModuleNotFoundError:
            pass  # fixed: the public name cannot be taken by a module

        from stapel_alerts import capture
        assert not isinstance(capture, types.ModuleType), (
            "stapel_alerts.capture is a MODULE — this deployment is on an "
            "alerts release where the export is shadowed by a submodule "
            "(< 0.2.4). Every alert this package raises is dropped."
        )
        assert callable(capture)
        print("OK")
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.startswith("OK")
