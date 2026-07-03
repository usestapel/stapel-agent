"""Provider tests — mocked HTTP, subprocess and SDK; no network, no CLI."""
import json
import subprocess
import sys
import types

import pytest

from stapel_agent.providers.anthropic import AnthropicProvider
from stapel_agent.providers.base import (
    LlmProvider,
    ProviderError,
    ProviderResult,
    ProviderTimeout,
)
from stapel_agent.providers.claude_cli import ClaudeCodeCLIProvider
from stapel_agent.providers.openai_compat import OpenAICompatProvider


class TestBase:
    def test_provider_result_defaults(self):
        result = ProviderResult(text="hi")
        assert (
            result.input_tokens,
            result.output_tokens,
            result.thinking_tokens,
            result.cache_read_tokens,
            result.cache_write_tokens,
        ) == (0, 0, 0, 0, 0)

    def test_default_resolve_model_passthrough(self):
        class P(LlmProvider):
            def complete(self, *, prompt, model, system_prompt=None):
                return ProviderResult(text="")

        assert P().resolve_model("small", "some-model") == "some-model"


class TestOpenAICompat:
    @pytest.fixture
    def configured(self, settings):
        settings.STAPEL_AGENT = {
            "OPENAI_COMPAT_BASE_URL": "https://api.example.test/v1/",
            "OPENAI_COMPAT_API_KEY": "sk-test",
            "OPENAI_COMPAT_MODELS": {"small": "gpt-4o-mini"},
        }
        return settings

    def _mock_post(self, monkeypatch, payload=None, status_code=200, exc=None):
        captured = {}

        class FakeResponse:
            def __init__(self):
                self.status_code = status_code
                self.text = json.dumps(payload or {})

            def json(self):
                return payload

        def fake_post(url, json=None, headers=None, timeout=None):
            if exc is not None:
                raise exc
            captured.update(url=url, json=json, headers=headers, timeout=timeout)
            return FakeResponse()

        monkeypatch.setattr(
            "stapel_agent.providers.openai_compat.requests.post", fake_post
        )
        return captured

    def test_success_and_usage_mapping(self, configured, monkeypatch):
        captured = self._mock_post(
            monkeypatch,
            payload={
                "choices": [{"message": {"content": "bonjour"}}],
                "usage": {
                    "prompt_tokens": 7,
                    "completion_tokens": 3,
                    "completion_tokens_details": {"reasoning_tokens": 2},
                },
            },
        )
        result = OpenAICompatProvider().complete(
            prompt="hello", model="gpt-4o-mini", system_prompt="be brief"
        )
        assert result.text == "bonjour"
        assert result.input_tokens == 7
        assert result.output_tokens == 3
        assert result.thinking_tokens == 2
        assert captured["url"] == "https://api.example.test/v1/chat/completions"
        assert captured["headers"]["Authorization"] == "Bearer sk-test"
        assert captured["json"]["model"] == "gpt-4o-mini"
        assert captured["json"]["messages"] == [
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "hello"},
        ]

    def test_no_reasoning_tokens_detail(self, configured, monkeypatch):
        self._mock_post(
            monkeypatch,
            payload={
                "choices": [{"message": {"content": "x"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )
        result = OpenAICompatProvider().complete(prompt="p", model="m")
        assert result.thinking_tokens == 0

    def test_resolve_model_by_size_with_fallback(self, configured):
        provider = OpenAICompatProvider()
        assert provider.resolve_model("small", "claude-haiku") == "gpt-4o-mini"
        assert provider.resolve_model("large", "claude-opus") == "claude-opus"

    def test_unconfigured_base_url(self, settings):
        settings.STAPEL_AGENT = {"OPENAI_COMPAT_BASE_URL": ""}
        with pytest.raises(ProviderError, match="OPENAI_COMPAT_BASE_URL"):
            OpenAICompatProvider().complete(prompt="p", model="m")

    def test_http_error(self, configured, monkeypatch):
        self._mock_post(monkeypatch, payload={"error": "nope"}, status_code=500)
        with pytest.raises(ProviderError, match="HTTP 500"):
            OpenAICompatProvider().complete(prompt="p", model="m")

    def test_timeout(self, configured, monkeypatch):
        import requests

        self._mock_post(monkeypatch, exc=requests.Timeout("slow"))
        with pytest.raises(ProviderTimeout):
            OpenAICompatProvider().complete(prompt="p", model="m")

    def test_connection_error(self, configured, monkeypatch):
        import requests

        self._mock_post(monkeypatch, exc=requests.ConnectionError("refused"))
        with pytest.raises(ProviderError, match="unreachable"):
            OpenAICompatProvider().complete(prompt="p", model="m")

    def test_malformed_body(self, configured, monkeypatch):
        self._mock_post(monkeypatch, payload={"choices": []})
        with pytest.raises(ProviderError, match="Unexpected response"):
            OpenAICompatProvider().complete(prompt="p", model="m")


class TestClaudeCodeCLI:
    def _mock_run(self, monkeypatch, *, stdout="", stderr="", returncode=0, exc=None):
        captured = {}

        def fake_run(args, capture_output=None, text=None, timeout=None, cwd=None):
            captured.update(
                args=args, capture_output=capture_output, text=text,
                timeout=timeout, cwd=cwd,
            )
            if exc is not None:
                raise exc
            return types.SimpleNamespace(
                returncode=returncode, stdout=stdout, stderr=stderr
            )

        monkeypatch.setattr(
            "stapel_agent.providers.claude_cli.subprocess.run", fake_run
        )
        return captured

    def test_success_json(self, monkeypatch):
        import tempfile

        captured = self._mock_run(
            monkeypatch,
            stdout=json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "result": '{"ok": true}',
                    "usage": {
                        "input_tokens": 11,
                        "output_tokens": 6,
                        "cache_creation_input_tokens": 4,
                        "cache_read_input_tokens": 9,
                    },
                }
            ),
        )
        result = ClaudeCodeCLIProvider().complete(
            prompt="hi", model="claude-sonnet-5", system_prompt="sys"
        )
        assert result.text == '{"ok": true}'
        assert result.input_tokens == 11
        assert result.output_tokens == 6
        assert result.cache_write_tokens == 4
        assert result.cache_read_tokens == 9
        assert captured["args"] == [
            "claude", "-p", "hi",
            "--model", "claude-sonnet-5",
            "--output-format", "json",
            "--system-prompt", "sys",
        ]
        assert captured["cwd"] == tempfile.gettempdir()
        assert captured["timeout"] == 120

    def test_no_system_prompt_flag_when_absent(self, monkeypatch):
        captured = self._mock_run(monkeypatch, stdout="{}")
        ClaudeCodeCLIProvider().complete(prompt="hi", model="m")
        assert "--system-prompt" not in captured["args"]

    def test_non_json_stdout_is_plain_text(self, monkeypatch):
        self._mock_run(monkeypatch, stdout="plain text answer\n")
        result = ClaudeCodeCLIProvider().complete(prompt="hi", model="m")
        assert result.text == "plain text answer"
        assert result.input_tokens == 0

    def test_non_dict_json_stdout_is_plain_text(self, monkeypatch):
        self._mock_run(monkeypatch, stdout='[1, 2, 3]')
        result = ClaudeCodeCLIProvider().complete(prompt="hi", model="m")
        assert result.text == "[1, 2, 3]"

    def test_is_error_result(self, monkeypatch):
        self._mock_run(
            monkeypatch,
            stdout=json.dumps({"is_error": True, "result": "rate limited"}),
        )
        with pytest.raises(ProviderError, match="rate limited"):
            ClaudeCodeCLIProvider().complete(prompt="hi", model="m")

    def test_nonzero_exit(self, monkeypatch):
        self._mock_run(monkeypatch, returncode=2, stderr="bad flag")
        with pytest.raises(ProviderError, match="bad flag"):
            ClaudeCodeCLIProvider().complete(prompt="hi", model="m")

    def test_timeout(self, monkeypatch):
        self._mock_run(
            monkeypatch, exc=subprocess.TimeoutExpired(cmd="claude", timeout=120)
        )
        with pytest.raises(ProviderTimeout):
            ClaudeCodeCLIProvider().complete(prompt="hi", model="m")

    def test_cli_not_found_message(self, monkeypatch):
        self._mock_run(monkeypatch, exc=FileNotFoundError("claude"))
        with pytest.raises(
            ProviderError,
            match="claude CLI not found — install it or pick another provider",
        ):
            ClaudeCodeCLIProvider().complete(prompt="hi", model="m")

    @pytest.mark.django_db
    def test_timeout_maps_to_timeout_status_via_service(
        self, settings, monkeypatch
    ):
        from stapel_agent import services
        from stapel_agent.models import PromptLog, PromptStatus

        self._mock_run(
            monkeypatch, exc=subprocess.TimeoutExpired(cmd="claude", timeout=120)
        )
        result = services.complete(
            "hi", "small", provider="claude-code", source="other"
        )
        assert result == {"status": "failure", "reason": "Execution timed out"}
        assert PromptLog.objects.get().status == PromptStatus.TIMEOUT


class TestAnthropic:
    def test_missing_key_is_clear_error(self, settings, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        settings.STAPEL_AGENT = {"ANTHROPIC_API_KEY": ""}
        with pytest.raises(ProviderError, match="API key not configured"):
            AnthropicProvider().complete(prompt="p", model="m")

    def test_missing_package_is_clear_error(self, settings, monkeypatch):
        settings.STAPEL_AGENT = {"ANTHROPIC_API_KEY": "sk-ant-test"}
        monkeypatch.setitem(sys.modules, "anthropic", None)
        with pytest.raises(ProviderError, match="not installed"):
            AnthropicProvider().complete(prompt="p", model="m")

    def _fake_sdk(self, monkeypatch, *, create=None):
        captured = {}
        fake = types.ModuleType("anthropic")

        class FakeMessages:
            def create(self, **kwargs):
                captured["create_kwargs"] = kwargs
                if create is not None:
                    return create(**kwargs)
                usage = types.SimpleNamespace(
                    input_tokens=9,
                    output_tokens=4,
                    cache_read_input_tokens=2,
                    cache_creation_input_tokens=1,
                )
                block = types.SimpleNamespace(type="text", text='{"done": 1}')
                return types.SimpleNamespace(content=[block], usage=usage)

        class FakeAnthropic:
            def __init__(self, api_key):
                captured["api_key"] = api_key
                self.messages = FakeMessages()

        fake.Anthropic = FakeAnthropic
        monkeypatch.setitem(sys.modules, "anthropic", fake)
        return captured

    def test_success_and_usage_mapping(self, settings, monkeypatch):
        settings.STAPEL_AGENT = {"ANTHROPIC_API_KEY": "sk-ant-test"}
        captured = self._fake_sdk(monkeypatch)
        result = AnthropicProvider().complete(
            prompt="p", model="claude-sonnet-5", system_prompt="sys"
        )
        assert result.text == '{"done": 1}'
        assert result.input_tokens == 9
        assert result.output_tokens == 4
        assert result.cache_read_tokens == 2
        assert result.cache_write_tokens == 1
        assert captured["api_key"] == "sk-ant-test"
        kwargs = captured["create_kwargs"]
        assert kwargs["model"] == "claude-sonnet-5"
        assert kwargs["system"] == "sys"
        assert kwargs["max_tokens"] == 4096
        assert kwargs["messages"] == [{"role": "user", "content": "p"}]

    def test_no_system_kwarg_when_absent(self, settings, monkeypatch):
        settings.STAPEL_AGENT = {"ANTHROPIC_API_KEY": "sk-ant-test"}
        captured = self._fake_sdk(monkeypatch)
        AnthropicProvider().complete(prompt="p", model="m")
        assert "system" not in captured["create_kwargs"]

    def test_sdk_error_becomes_provider_error(self, settings, monkeypatch):
        settings.STAPEL_AGENT = {"ANTHROPIC_API_KEY": "sk-ant-test"}

        def boom(**kwargs):
            raise RuntimeError("rate limited")

        self._fake_sdk(monkeypatch, create=boom)
        with pytest.raises(ProviderError, match="rate limited"):
            AnthropicProvider().complete(prompt="p", model="m")
