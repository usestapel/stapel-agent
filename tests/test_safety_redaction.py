"""Unit tests for stapel_agent.safety.redaction.

The guard arrived from the recordings product with no tests of its own (its
only caller was covered indirectly, through the artifact write path). These
are written against the contract stated in the module docstring: refuse the
write, name the variable, never echo the value.

Every test that needs a secret in the environment sets it with monkeypatch —
the gate reads ``os.environ`` live on each call, so there is nothing to reset
beyond the fixture's own teardown, and no test may depend on a real key
happening to be exported in the shell that ran pytest.
"""

from __future__ import annotations

import pytest

from stapel_agent.safety.redaction import (
    KEY_ENV_SUFFIXES,
    KEY_PREFIXES,
    MIN_SECRET_LEN,
    RedactionError,
    redaction_gate,
)


class TestPassesCleanText:
    def test_plain_text_is_allowed(self):
        redaction_gate("a summary of the meeting, no secrets here")

    def test_empty_text_is_allowed(self):
        redaction_gate("")

    def test_env_var_name_alone_is_not_a_leak(self, monkeypatch):
        """The gate matches VALUES. A prompt that mentions the variable by
        name is not a leak, and refusing it would make the guard unusable in
        exactly the documentation the guard is supposed to protect."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-live-0123456789abcdef")
        redaction_gate("set OPENAI_API_KEY in the environment before running")


class TestRefusesEnvSecrets:
    @pytest.mark.parametrize("suffix", KEY_ENV_SUFFIXES)
    def test_every_credential_suffix_is_gated(self, monkeypatch, suffix):
        monkeypatch.setenv(f"ACME{suffix}", "supersecretvalue123")
        with pytest.raises(RedactionError):
            redaction_gate("transcript ... supersecretvalue123 ... end")

    def test_message_names_the_variable_and_not_the_value(self, monkeypatch):
        monkeypatch.setenv("PROVIDER_API_KEY", "supersecretvalue123")
        with pytest.raises(RedactionError) as exc:
            redaction_gate("leaked supersecretvalue123")
        message = str(exc.value)
        assert "PROVIDER_API_KEY" in message
        # The whole point of naming only the variable: an exception message is
        # itself a thing that gets logged.
        assert "supersecretvalue123" not in message

    def test_is_a_runtime_error(self, monkeypatch):
        """Callers that only guard against RuntimeError still stop the write."""
        monkeypatch.setenv("PROVIDER_API_KEY", "supersecretvalue123")
        with pytest.raises(RuntimeError):
            redaction_gate("leaked supersecretvalue123")

    def test_secret_embedded_mid_payload_is_found(self, monkeypatch):
        monkeypatch.setenv("DEEPGRAM_API_TOKEN", "abcdefghijkl0123")
        payload = '{"notes": "prefixabcdefghijkl0123suffix", "n": 3}'
        with pytest.raises(RedactionError):
            redaction_gate(payload)


class TestDoesNotRefuseAtRandom:
    def test_short_values_are_ignored(self, monkeypatch):
        """A short value is a flag or a mode name, not a credential — matching
        on it would refuse writes at random."""
        short = "x" * (MIN_SECRET_LEN - 1)
        monkeypatch.setenv("FEATURE_SECRET", short)
        redaction_gate(f"the mode is {short} for this run")

    def test_minimum_length_value_is_gated(self, monkeypatch):
        """The threshold is inclusive: MIN_SECRET_LEN characters is a secret."""
        value = "y" * MIN_SECRET_LEN
        monkeypatch.setenv("FEATURE_SECRET", value)
        with pytest.raises(RedactionError):
            redaction_gate(f"leaked {value}")

    def test_non_credential_variables_are_ignored(self, monkeypatch):
        monkeypatch.setenv("STAPEL_BASE_URL", "https://example.invalid/api")
        redaction_gate("posted to https://example.invalid/api")


class TestLiteralPrefixes:
    @pytest.mark.parametrize("prefix", KEY_PREFIXES)
    def test_prefixed_token_is_refused_without_any_env_var(self, prefix):
        """A key can reach a payload from somewhere other than our own
        environment — an operator pasting one in, a provider quoting the
        Authorization header back in an error body."""
        with pytest.raises(RedactionError) as exc:
            redaction_gate(f"here you go: {prefix}0123456789abcdef")
        assert prefix in str(exc.value)

    def test_anthropic_prefix_is_covered(self):
        assert "sk-ant-" in KEY_PREFIXES


class TestHostExtensionSeam:
    def test_added_suffix_is_honoured(self, monkeypatch):
        """A host whose secrets are not named ``*_API_KEY`` extends the tuple
        in AppConfig.ready(); the gate reads it on every call."""
        from stapel_agent.safety import redaction

        monkeypatch.setattr(
            redaction, "KEY_ENV_SUFFIXES", (*KEY_ENV_SUFFIXES, "_CREDENTIAL"))
        monkeypatch.setenv("LEGACY_CREDENTIAL", "supersecretvalue123")
        with pytest.raises(RedactionError):
            redaction_gate("leaked supersecretvalue123")

    def test_added_prefix_is_honoured(self, monkeypatch):
        from stapel_agent.safety import redaction

        monkeypatch.setattr(
            redaction, "KEY_PREFIXES", (*KEY_PREFIXES, "ghp_"))
        with pytest.raises(RedactionError):
            redaction_gate("token ghp_0123456789abcdef")
