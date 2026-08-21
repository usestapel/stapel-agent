"""The settings namespace does not take orders from the environment.

``AppSettings`` falls back to ``os.environ[KEY]`` for every key not listed
in ``no_env``, and this namespace's key names are generic: ``CLI_BINARY``,
``CACHE_POLICY``, ``MAX_TOKENS``, ``DEFAULT_PROVIDER``. In a shared pod or
a compose file a same-named variable belonging to something else lands on
them, and what it lands on is not cosmetic — argv[0] of a subprocess, an
``import_string()`` target, the SSRF/DoS ceilings on a caller-supplied URL
and the cross-tenant cache gate.

The second half of the file is the download's host allowlist: an empty
list used to mean "any public host", i.e. the un-configured deployment was
the most open one.
"""
import os

import pytest

from stapel_agent import conf
from stapel_agent.conf import NO_ENV, agent_settings
from stapel_agent.stt.base import (
    AudioRef,
    RetryableTranscriptionError,
    TranscriptionError,
)
from stapel_agent.tests.fakes import serve_audio


@pytest.fixture
def env(monkeypatch):
    """Set an environment variable and re-read the namespace around it."""

    def _set(key, value):
        monkeypatch.setenv(key, value)
        agent_settings.reload()

    yield _set
    agent_settings.reload()


class TestEnvironmentCannotReachTheseKeys:
    @pytest.mark.parametrize("key", sorted(NO_ENV))
    def test_no_env_key_ignores_the_environment(self, key, env):
        """Every declared key resolves to its default, not to the string an
        environment variable put there."""
        env(key, "value-from-the-environment")
        assert getattr(agent_settings, key) != "value-from-the-environment"

    def test_env_var_cannot_choose_the_subprocess_binary(self, env):
        # providers/claude_cli.py feeds this straight to subprocess.run as
        # argv[0]. An env var picking the executable is code execution.
        env("CLI_BINARY", "/tmp/not-claude")
        assert agent_settings.CLI_BINARY == "claude"

    def test_env_var_cannot_choose_the_cache_policy(self, env):
        # An import_strings key: the value becomes import_string(value),
        # so an env var would select which class runs in this process.
        from stapel_agent.cache import PromptLogCachePolicy

        env("CACHE_POLICY", "stapel_agent.tests.fakes.CrossTenantCachePolicy")
        assert agent_settings.CACHE_POLICY is PromptLogCachePolicy

    def test_env_var_cannot_raise_the_download_ceilings(self, env):
        env("STT_DOWNLOAD_MAX_BYTES", "999999999999")
        env("STT_DOWNLOAD_TOTAL_DEADLINE", "86400")
        assert agent_settings.STT_DOWNLOAD_MAX_BYTES == 128 * 1024 * 1024
        assert agent_settings.STT_DOWNLOAD_TOTAL_DEADLINE == 300.0

    def test_env_var_cannot_widen_the_download_allowlist(self, env):
        env("STT_DOWNLOAD_ALLOWED_HOSTS", "attacker.test")
        assert agent_settings.STT_DOWNLOAD_ALLOWED_HOSTS == []

    def test_env_var_cannot_open_the_unscoped_cache(self, env):
        # The gate that decides whether one tenant's answer may be served
        # to a call that carries no tenant at all.
        env("CACHE_ALLOW_UNSCOPED", "llm_facade")
        assert agent_settings.CACHE_ALLOW_UNSCOPED == []

    def test_credentials_and_endpoints_still_come_from_the_environment(
        self, env
    ):
        """The closure is scoped, not blanket. Keys, base URLs and model
        names are per-deployment configuration and the environment is
        their canonical channel — closing those would break every host
        without making anything safer."""
        env("ANTHROPIC_API_KEY", "from-env")
        env("WHISPER_BASE_URL", "https://whisper.internal/v1")
        assert agent_settings.ANTHROPIC_API_KEY == "from-env"
        assert agent_settings.WHISPER_BASE_URL == "https://whisper.internal/v1"

    def test_no_env_is_actually_wired_into_the_namespace(self):
        assert set(NO_ENV) <= agent_settings.no_env

    def test_every_no_env_key_exists_in_the_defaults(self):
        """A typo in the tuple would silently protect nothing."""
        assert not set(NO_ENV) - set(agent_settings.defaults)


class TestBooleanKeysSurviveAStringValue:
    """``AppSettings`` does no coercion, so a boolean read through plain
    ``bool()`` reverses the operator's intent: ``bool("false")`` is True."""

    def test_string_false_does_not_open_the_download(self, settings):
        settings.STAPEL_AGENT = {"STT_DOWNLOAD_ALLOW_ANY_HOST": "false"}
        assert conf.stt_download_allow_any_host() is False

    def test_string_true_opens_it(self, settings):
        settings.STAPEL_AGENT = {"STT_DOWNLOAD_ALLOW_ANY_HOST": "true"}
        assert conf.stt_download_allow_any_host() is True

    def test_string_false_turns_pyannote_exclusive_off(self, env):
        # Env-readable on purpose (it shapes a request, it decides no
        # trust question) — which is exactly why it needs the accessor.
        env("PYANNOTEAI_EXCLUSIVE", "false")
        assert conf.pyannoteai_exclusive() is False

    def test_the_default_stays_on(self):
        assert conf.pyannoteai_exclusive() is True

    def test_string_false_does_not_declare_retention_scheduled(self, settings):
        settings.STAPEL_AGENT = {"PROMPT_LOG_RETENTION_SCHEDULED": "false"}
        assert conf.prompt_log_retention_scheduled() is False


class TestEmptyAllowlistIsNotAWildcard:
    """An empty ``STT_DOWNLOAD_ALLOWED_HOSTS`` used to mean "any public
    host", so the deployment that configured nothing was the one that
    would fetch whatever host a request named."""

    def test_empty_allowlist_refuses_the_download(self, monkeypatch):
        seen = serve_audio(monkeypatch, b"audio", allow_any_host=False)
        with pytest.raises(TranscriptionError, match="no_allowed_hosts") as e:
            AudioRef(url="https://cdn.test/a.mp3").read_bytes(provider="p")
        # Fatal, not retryable: the next provider would refuse identically.
        assert not isinstance(e.value, RetryableTranscriptionError)
        assert e.value.provider == "p"
        # And nothing was dialled — the refusal happens before the fetch.
        assert seen == []

    def test_the_refusal_names_both_ways_out(self, monkeypatch):
        serve_audio(monkeypatch, b"audio", allow_any_host=False)
        with pytest.raises(TranscriptionError) as e:
            AudioRef(url="https://cdn.test/a.mp3").read_bytes(provider="p")
        assert "STT_DOWNLOAD_ALLOWED_HOSTS" in str(e.value)
        assert "STT_DOWNLOAD_ALLOW_ANY_HOST" in str(e.value)

    def test_an_allowlisted_host_is_fetched(self, monkeypatch, settings):
        settings.STAPEL_AGENT = {"STT_DOWNLOAD_ALLOWED_HOSTS": ["store.test"]}
        serve_audio(monkeypatch, b"ok", allow_any_host=False)
        assert (
            AudioRef(url="https://store.test/a.mp3").read_bytes(provider="p")
            == b"ok"
        )

    def test_the_opt_out_restores_any_public_host(self, monkeypatch, settings):
        settings.STAPEL_AGENT = {"STT_DOWNLOAD_ALLOW_ANY_HOST": True}
        serve_audio(monkeypatch, b"ok", allow_any_host=False)
        assert (
            AudioRef(url="https://cdn.test/a.mp3").read_bytes(provider="p")
            == b"ok"
        )

    def test_the_opt_out_does_not_disarm_the_other_guards(
        self, monkeypatch, settings
    ):
        """Opting into any host opts into any *public* host: the fetcher's
        private/loopback/metadata refusals are not part of the trade."""
        settings.STAPEL_AGENT = {"STT_DOWNLOAD_ALLOW_ANY_HOST": True}
        serve_audio(monkeypatch, b"ok", ip="169.254.169.254", allow_any_host=False)
        with pytest.raises(TranscriptionError, match="blocked_ip"):
            AudioRef(url="https://metadata.test/a.mp3").read_bytes(provider="p")

    def test_data_and_path_refs_are_unaffected(self, tmp_path):
        """The gate is about fetching a caller-named host, nothing else."""
        f = tmp_path / "a.wav"
        f.write_bytes(b"RIFFdata")
        assert AudioRef(data=b"abc").read_bytes(provider="p") == b"abc"
        assert AudioRef(path=str(f)).read_bytes(provider="p") == b"RIFFdata"

    def test_the_refusal_survives_an_environment_variable(
        self, monkeypatch, env
    ):
        """The opt-out is a no_env key: a stray variable must not be able
        to re-open the surface this finding closed."""
        serve_audio(monkeypatch, b"audio", allow_any_host=False)
        env("STT_DOWNLOAD_ALLOW_ANY_HOST", "true")
        with pytest.raises(TranscriptionError, match="no_allowed_hosts"):
            AudioRef(url="https://cdn.test/a.mp3").read_bytes(provider="p")


class TestTheClosedAllowlistIsAnnouncedAtStartup:
    """W015: the refusal above is correct and invisible.

    It fires per request, in a worker, on a path callers treat as
    best-effort — the iron-agent dev stand ran with an empty allowlist for
    its whole life and every transcription was refused before the first
    DNS lookup, with green checks and a green deploy (2026-08-21).
    """

    @pytest.fixture(autouse=True)
    def clean_runtime_registry(self):
        from stapel_agent.stt import _reset_runtime_stt_providers

        _reset_runtime_stt_providers()
        yield
        _reset_runtime_stt_providers()

    def _run(self):
        from stapel_agent.checks import check_stt_download_allowlist

        return check_stt_download_allowlist(app_configs=None)

    def _ids(self):
        return [issue.id for issue in self._run()]

    def test_the_shipped_default_warns(self, settings):
        settings.STAPEL_AGENT = {}
        assert self._ids() == ["stapel_agent.W015"]

    def test_it_is_a_warning_not_an_error(self, settings):
        """A host that installed this app for text completion alone must
        not be blocked from deploying."""
        from django.core import checks as django_checks

        settings.STAPEL_AGENT = {}
        assert self._run()[0].level == django_checks.WARNING

    def test_the_warning_names_the_setting_the_fix_and_the_env_trap(
        self, settings
    ):
        settings.STAPEL_AGENT = {}
        issue = self._run()[0]
        assert "STT_DOWNLOAD_ALLOWED_HOSTS" in issue.msg
        assert "no_allowed_hosts" in issue.msg
        assert "settings.py" in issue.hint
        assert "NO_ENV" in issue.hint
        assert "STT_DOWNLOAD_ALLOW_ANY_HOST" in issue.hint

    def test_an_allowlist_silences_it(self, settings):
        settings.STAPEL_AGENT = {
            "STT_DOWNLOAD_ALLOWED_HOSTS": ["store.test"]
        }
        assert self._ids() == []

    def test_the_declared_wildcard_silences_it(self, settings):
        settings.STAPEL_AGENT = {"STT_DOWNLOAD_ALLOW_ANY_HOST": True}
        assert self._ids() == []

    def test_a_string_wildcard_is_read_as_a_boolean(self, settings):
        """``bool("false")`` is True — the accessor, not ``bool()``."""
        settings.STAPEL_AGENT = {"STT_DOWNLOAD_ALLOW_ANY_HOST": "false"}
        assert self._ids() == ["stapel_agent.W015"]

    def test_removing_the_stt_surface_silences_it(self, settings):
        """Masking every adapter removes the surface; nothing to warn about."""
        from stapel_agent.stt import BUILTIN_STT_PROVIDERS

        settings.STAPEL_AGENT = {
            "STT_PROVIDERS": {name: None for name in BUILTIN_STT_PROVIDERS}
        }
        assert self._ids() == []

    def test_it_is_registered_with_django(self):
        from django.core.checks.registry import registry

        from stapel_agent.checks import check_stt_download_allowlist

        assert check_stt_download_allowlist in registry.get_checks()


def test_the_environment_is_not_read_for_no_env_keys_at_all(monkeypatch):
    """Belt and braces: the namespace must not even consult os.environ for
    a protected key (a future refactor could re-introduce the lookup and
    still pass the value assertions by accident)."""
    asked = []
    real_get = os.environ.get

    def spy(key, *a, **kw):
        asked.append(key)
        return real_get(key, *a, **kw)

    monkeypatch.setattr(os.environ, "get", spy)
    agent_settings.reload()
    for key in NO_ENV:
        getattr(agent_settings, key)
    agent_settings.reload()
    assert not set(asked) & set(NO_ENV)
