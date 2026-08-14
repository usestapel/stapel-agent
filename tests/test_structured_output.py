"""Schema-constrained output: the decoder is constrained, or the call fails.

Ported from the iron-benchmark harness, where the prompt-only path was
measured failing in production (2026-07-03): asked for JSON in prose, the
model returned valid JSON whose single ``summary`` field held the entire
answer as pseudo-XML while every structured field came back empty. The
caller could not tell — which is why an unsupported provider now fails
the call instead of answering the prose way.
"""
import json

import pytest
from pydantic import BaseModel, ConfigDict

from stapel_agent import services
from stapel_agent.providers.base import ProviderResult
from stapel_agent.providers.openai_compat import OpenAICompatProvider
from stapel_agent.tests.fakes import FakeProvider, RecordingCachePolicy

SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "integer"}},
    "required": ["answer"],
    "additionalProperties": False,
}


@pytest.mark.django_db
class TestServiceSeam:
    def test_schema_reaches_the_provider(self, fake_provider):
        result = services.complete("p", "small", schema=SCHEMA)
        assert result["status"] == "ok"
        assert fake_provider.calls[0]["schema"] == SCHEMA

    def test_no_schema_kwarg_when_none_requested(self, fake_provider):
        """Pre-schema subclasses keep working — the kwarg only travels
        when a schema was actually asked for."""
        services.complete("p", "small")
        assert fake_provider.calls[0]["schema"] is None

    def test_provider_without_the_capability_fails_the_call(self, settings):
        settings.STAPEL_AGENT = {
            "PROVIDERS": {"no-schema": "stapel_agent.tests.fakes.NoVisionProvider"},
            "DEFAULT_PROVIDER": "no-schema",
        }
        FakeProvider.reset()
        result = services.complete("p", "small", schema=SCHEMA)
        assert result["status"] == "failure"
        assert "no-schema" in result["reason"]
        assert "JSON schema" in result["reason"]
        # The point of failing: nothing was generated from an
        # unconstrained decoder and handed back as if it were.
        assert FakeProvider.calls == []


@pytest.mark.django_db
class TestCompleteJson:
    def test_schema_drops_the_coaxing_system_prompt(self, fake_provider):
        result = services.complete_json("p", "small", schema=SCHEMA)
        assert result["status"] == "ok"
        assert fake_provider.calls[0]["system_prompt"] is None
        assert fake_provider.calls[0]["schema"] == SCHEMA

    def test_caller_system_prompt_survives_a_schema(self, fake_provider):
        services.complete_json("p", "small", system_prompt="be terse", schema=SCHEMA)
        assert fake_provider.calls[0]["system_prompt"] == "be terse"

    def test_without_a_schema_the_json_prompt_is_still_injected(self, fake_provider):
        services.complete_json("p", "small")
        assert fake_provider.calls[0]["system_prompt"] == services.JSON_API_SYSTEM_PROMPT
        assert fake_provider.calls[0]["schema"] is None


@pytest.mark.django_db
class TestCacheInteraction:
    """A schema changes the SHAPE of the answer; the prompt cache is keyed
    on text alone and cannot see it. Same hazard as images."""

    @pytest.fixture
    def caching(self, settings):
        settings.STAPEL_AGENT = {
            "PROVIDERS": {"fake": "stapel_agent.tests.fakes.FakeProvider"},
            "DEFAULT_PROVIDER": "fake",
            "CACHE_POLICY": "stapel_agent.tests.fakes.RecordingCachePolicy",
        }
        FakeProvider.reset()
        RecordingCachePolicy.reset()
        yield RecordingCachePolicy
        RecordingCachePolicy.reset()
        FakeProvider.reset()

    def test_schema_bypasses_lookup_and_store(self, caching):
        services.complete("p", "small", source="llm_facade", schema=SCHEMA)
        services.complete("p", "small", source="llm_facade", schema=SCHEMA)
        assert caching.lookups == []
        assert caching.stores == []
        assert len(FakeProvider.calls) == 2

    def test_the_same_prompt_without_a_schema_still_caches(self, caching):
        services.complete("p", "small", source="llm_facade", user_id="u1")
        assert len(caching.stores) == 1


class TestAnthropicWireFormat:
    @pytest.fixture
    def configured(self, settings):
        settings.STAPEL_AGENT = {"ANTHROPIC_API_KEY": "sk-test"}
        return settings

    def _capture(self, monkeypatch):
        captured = {}

        class FakeMessage:
            content = []
            usage = None

        class FakeMessages:
            def create(self, **kwargs):
                captured.update(kwargs)
                return FakeMessage()

        class FakeAnthropic:
            def __init__(self, api_key=None):
                self.messages = FakeMessages()

        module = type("m", (), {"Anthropic": FakeAnthropic})
        monkeypatch.setitem(__import__("sys").modules, "anthropic", module)
        return captured

    def test_schema_becomes_output_config_format(self, configured, monkeypatch):
        from stapel_agent.providers.anthropic import AnthropicProvider

        captured = self._capture(monkeypatch)
        AnthropicProvider().complete(prompt="p", model="m", schema=SCHEMA)
        assert captured["output_config"] == {
            "format": {"type": "json_schema", "schema": SCHEMA}
        }

    def test_no_output_config_without_a_schema(self, configured, monkeypatch):
        from stapel_agent.providers.anthropic import AnthropicProvider

        captured = self._capture(monkeypatch)
        AnthropicProvider().complete(prompt="p", model="m")
        assert "output_config" not in captured


class TestOpenAICompatWireFormat:
    @pytest.fixture
    def configured(self, settings):
        settings.STAPEL_AGENT = {
            "OPENAI_COMPAT_BASE_URL": "https://api.example.test/v1",
            "OPENAI_COMPAT_API_KEY": "sk-test",
        }
        return settings

    def _capture(self, monkeypatch):
        captured = {}

        class FakeResponse:
            status_code = 200
            text = ""

            def json(self):
                return {"choices": [{"message": {"content": '{"answer": 1}'}}]}

        def fake_post(url, json=None, headers=None, timeout=None):
            captured.update(url=url, json=json)
            return FakeResponse()

        monkeypatch.setattr(
            "stapel_agent.providers.openai_compat.requests.post", fake_post
        )
        return captured

    def test_schema_becomes_strict_response_format(self, configured, monkeypatch):
        captured = self._capture(monkeypatch)
        result = OpenAICompatProvider().complete(prompt="p", model="m", schema=SCHEMA)
        assert json.loads(result.text) == {"answer": 1}
        response_format = captured["json"]["response_format"]
        assert response_format["type"] == "json_schema"
        assert response_format["json_schema"]["schema"] == SCHEMA
        # Without strict the endpoint may return prose anyway — that is
        # the difference between a constraint and a hint.
        assert response_format["json_schema"]["strict"] is True

    def test_no_response_format_without_a_schema(self, configured, monkeypatch):
        captured = self._capture(monkeypatch)
        OpenAICompatProvider().complete(prompt="p", model="m")
        assert "response_format" not in captured["json"]


def test_the_cli_provider_does_not_claim_the_capability():
    """The claude CLI has no schema flag — claiming supports_schema would
    be a check carrying its own answer."""
    from stapel_agent.providers.claude_cli import ClaudeCodeCLIProvider

    assert ClaudeCodeCLIProvider.supports_schema is False


def test_result_of_a_constrained_call_parses(fake_provider, db):
    FakeProvider.result = ProviderResult(text='{"answer": 42}')
    out = services.complete_json("p", "small", schema=SCHEMA)
    assert out["result"] == {"answer": 42}


class Answer(BaseModel):
    """A model whose shape IS the constraint (see _resolve_schema)."""

    model_config = ConfigDict(extra="forbid")

    answer: int


@pytest.mark.django_db
class TestPydanticModelAsSchema:
    """The schema and the type that reads the answer back must not be two
    hand-written copies of one truth."""

    def test_constraint_is_derived_from_the_model(self, fake_provider):
        services.complete_json("p", "small", schema=Answer)
        sent = fake_provider.calls[0]["schema"]
        assert sent == Answer.model_json_schema()
        # extra="forbid" is what puts this in the schema, and strict modes
        # require it — proving it survives the derivation.
        assert sent["additionalProperties"] is False

    def test_result_is_a_validated_instance(self, fake_provider):
        FakeProvider.result = ProviderResult(text='{"answer": 42}')
        out = services.complete_json("p", "small", schema=Answer)
        assert out["status"] == "ok"
        assert isinstance(out["result"], Answer)
        assert out["result"].answer == 42

    def test_answer_that_does_not_fit_the_model_is_a_failure(self, fake_provider):
        FakeProvider.result = ProviderResult(text='{"answer": "not a number"}')
        out = services.complete_json("p", "small", schema=Answer)
        assert out["status"] == "failure"
        assert "Answer" in out["reason"]

    def test_extra_field_is_a_failure_not_a_silent_drop(self, fake_provider):
        FakeProvider.result = ProviderResult(text='{"answer": 1, "smuggled": "x"}')
        out = services.complete_json("p", "small", schema=Answer)
        assert out["status"] == "failure"
        assert "smuggled" in out["reason"]

    def test_a_dict_schema_still_returns_a_dict(self, fake_provider):
        FakeProvider.result = ProviderResult(text='{"answer": 42}')
        out = services.complete_json("p", "small", schema=SCHEMA)
        assert out["result"] == {"answer": 42}
        assert not isinstance(out["result"], BaseModel)

    def test_a_model_works_through_complete_too(self, fake_provider):
        """complete() constrains but returns raw text — the model is used
        for the schema only, not to parse."""
        out = services.complete("p", "small", schema=Answer)
        assert out["status"] == "ok"
        assert out["result"] == '{"answer": 42}'
        assert fake_provider.calls[0]["schema"] == Answer.model_json_schema()

    def test_something_that_is_neither_is_rejected_loudly(self, fake_provider):
        with pytest.raises(TypeError, match="JSON Schema dict or a pydantic"):
            services.complete_json("p", "small", schema="just a string")
