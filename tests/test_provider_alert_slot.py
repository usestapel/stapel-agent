"""The provider alert window holds per SERVICE, not per worker.

A client host runs its services as gunicorn with two workers plus a Celery
pool. The window used to live in a module-level dict — one per PROCESS — so
"one alert per provider per hour" was one per provider per hour PER WORKER,
and an exhausted provider refusing every upload paged once per worker.
"""
import pytest

from stapel_agent import provider_health


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    # The alert STORE is stapel-alerts' business and has its own tests; what
    # is under test here is how many times the path is walked at all.
    monkeypatch.setattr(
        provider_health, "capture_alert",
        lambda message, *, kind, context, level: True,
    )
    provider_health.clear_slot()
    yield
    provider_health.clear_slot()


@pytest.fixture
def shared_cache(settings, tmp_path):
    """A cache that is actually shared between processes.

    locmem is per-process, so it is (correctly) NOT treated as shared and
    would exercise the fallback rather than the mechanism.
    """
    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
            "LOCATION": str(tmp_path / "throttle-cache"),
        }
    }
    from stapel_core.observability import throttle

    assert throttle.slot_is_shared() is True
    return settings


def _worker_refusals(count: int) -> int:
    """How many of *count* refusals in ONE worker were loud.

    A worker is a process with its own module state; the shared slot is what
    the two of them have in common, so simulating a second worker is
    clearing the process-local half and running again.
    """
    # A fresh worker is a process with EMPTY process-local state; what the
    # two workers have in common is the shared cache, and nothing else.
    provider_health._slots.clear()
    provider_health._refusing.clear()
    from stapel_core.observability import throttle

    throttle.clear_slots()
    loud = 0
    for _ in range(count):
        if provider_health.report_llm_out_of_credits("acme-llm"):
            loud += 1
    return loud


class TestTenRefusalsAcrossTwoWorkers:
    def test_raise_one_alert(self, shared_cache):
        first = _worker_refusals(5)
        second = _worker_refusals(5)
        assert first + second == 1, (first, second)

    def test_without_a_shared_cache_each_worker_still_throttles_itself(
        self, settings
    ):
        """The fallback is the OLD behaviour, not the absence of one.

        A deployment with no shared cache gets one alert per worker per
        window — which is what it had before — rather than an alert per
        refusal.
        """
        settings.CACHES = {
            "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
        }
        assert _worker_refusals(5) == 1
        assert _worker_refusals(5) == 1

    def test_a_provider_that_serves_again_re_arms_the_shared_slot(
        self, shared_cache
    ):
        assert _worker_refusals(3) == 1
        provider_health.report_llm_served("acme-llm")
        # A recurrence after a recovery is NEW news: it must not wait out the
        # remainder of the hour claimed before the provider came back.
        assert _worker_refusals(1) == 1


class TestGaugesDeclareHowWorkersCombine:
    def test_the_out_of_credits_flag_is_livemax(self, monkeypatch):
        seen = []
        import stapel_core.observability.metrics as core_metrics

        monkeypatch.setattr(
            core_metrics, "gauge",
            lambda name, value, labels=None, description="",
            multiprocess_mode=None: seen.append((name, multiprocess_mode)),
        )
        provider_health.report_llm_served("acme-llm")
        assert seen == [("llm_provider_out_of_credits", "livemax")]

    def test_the_quota_ratio_is_livemostrecent(self, monkeypatch):
        seen = []
        import stapel_core.observability.metrics as core_metrics

        monkeypatch.setattr(
            core_metrics, "gauge",
            lambda name, value, labels=None, description="",
            multiprocess_mode=None: seen.append((name, multiprocess_mode)),
        )
        from stapel_agent.stt import quota

        quota.record_gauge("acme-stt", 0.25)
        assert seen == [(quota.QUOTA_GAUGE, "livemostrecent")]
