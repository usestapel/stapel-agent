"""The empty wallet must announce itself before it refuses a recording.

``stt/failures.py`` made a quota refusal survivable — it walks the
fallback chain instead of reading as bad audio. It did not make the
condition VISIBLE: the account that burned 41 recordings had been under
10% for nine days, and the only artefacts that said so were a support
ticket and a log line nobody was watching.

These cover both halves: the scheduled sweep that asks, and the live
refusal that reports.
"""
import logging

import pytest

from stapel_agent import services, tasks
from stapel_agent.stt import quota
from stapel_agent.stt.base import AudioRef, ProviderQuota
from stapel_agent.tests.fakes import (
    QuotaProbeSttProvider,
    QuotaSttProvider,
    SilentQuotaSttProvider,
)

AUDIO = AudioRef(url="https://minio.test/bucket/rec.mp3")


@pytest.fixture
def probes(settings):
    """Two fakes: one that exposes a balance, one that does not."""
    settings.STAPEL_AGENT = {
        **getattr(settings, "STAPEL_AGENT", {}),
        "STT_PROVIDERS": {
            "quota-probe-stt": "stapel_agent.tests.fakes.QuotaProbeSttProvider",
            "silent-quota-stt": "stapel_agent.tests.fakes.SilentQuotaSttProvider",
            "quota-stt": "stapel_agent.tests.fakes.QuotaSttProvider",
        },
        "DEFAULT_STT_PROVIDER": "quota-probe-stt",
        "STT_QUOTA_WATCHDOG": {
            "ENABLED": True, "WARN_RATIO": 0.10, "CRITICAL_RATIO": 0.02,
        },
    }
    for cls in (QuotaProbeSttProvider, SilentQuotaSttProvider, QuotaSttProvider):
        cls.reset()
    yield QuotaProbeSttProvider
    for cls in (QuotaProbeSttProvider, SilentQuotaSttProvider, QuotaSttProvider):
        cls.reset()


@pytest.fixture
def gauges(monkeypatch):
    """Capture what the watchdog records, without a metrics backend."""
    recorded: list[tuple[str, float]] = []
    monkeypatch.setattr(
        quota, "record_gauge",
        lambda provider, ratio: recorded.append((provider, ratio)),
    )
    return recorded


@pytest.fixture
def alerts():
    """Collect ``provider_quota_low``, the seam every host can reach."""
    seen: list[dict] = []

    def receiver(sender, **kwargs):
        seen.append(kwargs)

    quota.provider_quota_low.connect(receiver)
    yield seen
    quota.provider_quota_low.disconnect(receiver)


class TestProviderQuota:
    def test_the_ratio_is_of_what_is_LEFT(self):
        q = ProviderQuota(provider="p", used=900.0, limit=1000.0)
        assert q.remaining == 100.0
        assert q.remaining_ratio == pytest.approx(0.1)

    def test_an_overspent_account_floors_at_zero(self):
        q = ProviderQuota(provider="p", used=1200.0, limit=1000.0)
        assert q.remaining == 0.0
        assert q.remaining_ratio == 0.0

    def test_an_unreadable_limit_cannot_divide_by_zero(self):
        assert ProviderQuota(provider="p", used=0.0, limit=0.0).remaining_ratio == 0.0


class TestThresholds:
    @pytest.mark.parametrize(
        "ratio,expected",
        [(1.0, None), (0.5, None), (0.10, None), (0.0999, "warning"),
         (0.05, "warning"), (0.02, "warning"), (0.0199, "critical"),
         (0.0, "critical")],
    )
    def test_severity(self, ratio, expected, probes):
        assert quota.severity_for(ratio) == expected

    def test_the_thresholds_are_the_hosts(self, settings, probes):
        settings.STAPEL_AGENT = {
            **settings.STAPEL_AGENT,
            "STT_QUOTA_WATCHDOG": {
                "ENABLED": True, "WARN_RATIO": 0.5, "CRITICAL_RATIO": 0.4,
            },
        }
        assert quota.severity_for(0.45) == "warning"
        assert quota.severity_for(0.39) == "critical"


class TestSweep:
    def test_a_healthy_account_is_gauged_and_not_alerted(
        self, probes, gauges, alerts
    ):
        rows = quota.check_provider_quotas()

        assert QuotaProbeSttProvider.probes, "the watchdog must ASK, not guess"
        assert ("quota-probe-stt", pytest.approx(0.9)) in gauges
        assert alerts == []
        (row,) = [r for r in rows if r["provider"] == "quota-probe-stt"]
        assert row["severity"] is None
        assert row["unit"] == "characters"

    def test_a_provider_with_no_balance_endpoint_is_absent_not_empty(
        self, probes, gauges, alerts
    ):
        rows = quota.check_provider_quotas()

        names = {row["provider"] for row in rows}
        assert "silent-quota-stt" not in names
        # "We do not know" must never be gauged as 0.0 — that is an alert
        # for every provider that has no endpoint, forever.
        assert all(name != "silent-quota-stt" for name, _ in gauges)
        assert alerts == []

    @pytest.mark.parametrize(
        "used,expected", [(910.0, "warning"), (995.0, "critical")]
    )
    def test_a_low_balance_alerts_at_its_severity(
        self, probes, gauges, alerts, used, expected
    ):
        QuotaProbeSttProvider.quota = ProviderQuota(
            provider="quota-probe-stt", used=used, limit=1000.0,
            unit="characters",
        )

        quota.check_provider_quotas()

        (alert,) = alerts
        assert alert["provider"] == "quota-probe-stt"
        assert alert["severity"] == expected
        assert alert["source"] == "watchdog"
        assert alert["quota"].remaining == pytest.approx(1000.0 - used)

    def test_the_warning_log_is_the_floor(self, probes, gauges, caplog):
        QuotaProbeSttProvider.quota = ProviderQuota(
            provider="quota-probe-stt", used=999.0, limit=1000.0,
            unit="characters",
        )
        with caplog.at_level(logging.WARNING, logger="stapel_agent.stt.quota"):
            quota.check_provider_quotas()

        # A host with no metrics backend, no stapel-alerts and no receiver
        # still has this line. It carries the numbers, not just a ratio.
        (record,) = [r for r in caplog.records if "allowance" in r.getMessage()]
        message = record.getMessage()
        assert "quota-probe-stt" in message
        assert "critical" in message
        assert "1 of 1000 characters left" in message

    def test_a_probe_that_raises_does_not_end_the_sweep(
        self, probes, gauges, alerts
    ):
        QuotaProbeSttProvider.quota_error = RuntimeError("balance API is down")

        rows = quota.check_provider_quotas()

        assert rows == []          # nothing knowable, nothing invented
        assert gauges == []
        assert alerts == []

    def test_disabled_means_disabled(self, settings, probes, gauges, alerts):
        settings.STAPEL_AGENT = {
            **settings.STAPEL_AGENT,
            "STT_QUOTA_WATCHDOG": {
                "ENABLED": False, "WARN_RATIO": 0.10, "CRITICAL_RATIO": 0.02,
            },
        }
        assert quota.check_provider_quotas() == []
        assert QuotaProbeSttProvider.probes == []


@pytest.mark.django_db
class TestLiveRefusal:
    def test_a_quota_decline_raises_the_same_alert(self, probes, gauges, alerts):
        result = services.transcribe(AUDIO, provider="quota-stt")

        assert result["status"] == "failure"
        (alert,) = alerts
        assert alert["provider"] == "quota-stt"
        # Always critical: the provider has just refused, now — a stronger
        # statement than any poll, and not to be averaged with one.
        assert alert["severity"] == "critical"
        assert alert["source"] == "refusal"
        assert alert["ratio"] == 0.0
        assert ("quota-stt", 0.0) in gauges

    def test_other_refusals_do_not_alert(self, settings, probes, gauges, alerts):
        settings.STAPEL_AGENT = {
            **settings.STAPEL_AGENT,
            "STT_PROVIDERS": {
                **settings.STAPEL_AGENT["STT_PROVIDERS"],
                "retry-stt": "stapel_agent.tests.fakes.RetryableSttProvider",
            },
        }
        from stapel_agent.tests.fakes import RetryableSttProvider

        RetryableSttProvider.reset()
        services.transcribe(AUDIO, provider="retry-stt")

        # A 429 is throttling, not an empty wallet. Paging on it is how an
        # alert channel becomes one nobody reads.
        assert alerts == []

    def test_the_switch_covers_the_refusal_path_too(
        self, settings, probes, gauges, alerts
    ):
        settings.STAPEL_AGENT = {
            **settings.STAPEL_AGENT,
            "STT_QUOTA_WATCHDOG": {
                "ENABLED": False, "WARN_RATIO": 0.10, "CRITICAL_RATIO": 0.02,
            },
        }
        services.transcribe(AUDIO, provider="quota-stt")
        assert alerts == []

    def test_a_receiver_that_raises_does_not_lose_the_transcription(
        self, probes, gauges
    ):
        def angry(sender, **kwargs):
            raise RuntimeError("my pager is broken")

        quota.provider_quota_low.connect(angry)
        try:
            result = services.transcribe(AUDIO, provider="quota-stt")
        finally:
            quota.provider_quota_low.disconnect(angry)
        # The walk continues; the alert's failure is the alert's problem.
        assert result["status"] == "failure"
        assert "quota" in result["reason"]


class TestBeatEntry:
    def test_the_watchdog_has_a_schedulable_entry(self):
        celery = pytest.importorskip("celery")
        assert celery  # the schedule needs crontab
        schedule = tasks.get_agent_beat_schedule()
        entry = schedule[tasks.QUOTA_BEAT_KEY]
        assert entry["task"] == tasks.QUOTA_TASK_NAME
        # Hourly: an exhausted account takes days to arrive and minutes to
        # fix, so the cadence only has to beat the former.
        assert entry["schedule"].hour == set(range(24))

    def test_the_task_name_is_importable_under_that_name(self):
        module, _, attr = tasks.QUOTA_TASK_NAME.rpartition(".")
        assert module == "stapel_agent.tasks"
        assert callable(getattr(tasks, attr))


class TestElevenLabsProbe:
    """The one adapter that HAS a balance endpoint."""

    def _provider(self, settings, monkeypatch, *, status=200, body=None,
                  boom=None):
        from stapel_agent.stt.providers import elevenlabs

        settings.STAPEL_AGENT = {
            **getattr(settings, "STAPEL_AGENT", {}),
            "ELEVENLABS_API_KEY": "xi-test",
        }

        class _Resp:
            status_code = status

            def json(self):
                return body or {}

        seen: list[dict] = []

        def fake_get(url, headers=None, timeout=None):
            seen.append({"url": url, "headers": headers, "timeout": timeout})
            if boom is not None:
                raise boom
            return _Resp()

        monkeypatch.setattr(elevenlabs.requests, "get", fake_get)
        return elevenlabs.ElevenLabsProvider(), seen

    def test_it_reads_character_count_over_character_limit(
        self, settings, monkeypatch
    ):
        provider, seen = self._provider(
            settings, monkeypatch,
            body={"character_count": 23736, "character_limit": 25000,
                  "tier": "creator"},
        )
        result = provider.quota_status()

        assert seen[0]["url"].endswith("/v1/user/subscription")
        assert seen[0]["headers"]["xi-api-key"] == "xi-test"
        assert result.used == 23736
        assert result.limit == 25000
        assert result.unit == "characters"
        assert result.remaining_ratio == pytest.approx(0.05056, rel=1e-3)
        # The plan and the reset date, and nothing else: the subscription
        # payload also carries billing detail that has no business in a
        # log line or an alert context.
        assert set(result.raw) == {"tier", "next_reset_unix"}

    def test_no_key_means_no_probe(self, settings, monkeypatch):
        provider, seen = self._provider(settings, monkeypatch)
        settings.STAPEL_AGENT = {
            **settings.STAPEL_AGENT, "ELEVENLABS_API_KEY": "",
        }
        assert provider.quota_status() is None
        assert seen == []

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"status": 401, "body": {}},
            {"body": {"character_count": 5}},           # no limit
            {"body": {"character_limit": 0, "character_count": 0}},
            {"body": {"character_limit": "lots", "character_count": "some"}},
            {"boom": RuntimeError("connection reset")},
        ],
    )
    def test_every_unreadable_answer_is_None_not_an_alert(
        self, settings, monkeypatch, kwargs
    ):
        provider, _ = self._provider(settings, monkeypatch, **kwargs)
        assert provider.quota_status() is None


class TestAssemblyAI:
    def test_it_reports_no_balance_endpoint(self):
        from stapel_agent.stt.providers.assemblyai import AssemblyAIProvider

        # Stated, not omitted: the survey found no account/balance route,
        # and the refusal path is what covers this provider.
        assert AssemblyAIProvider().quota_status() is None
