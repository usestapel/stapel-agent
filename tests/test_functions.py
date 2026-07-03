"""comm Function tests — llm.complete / llm.translate called in-process
via stapel_core.comm.call with schema validation ON."""
import pytest
from stapel_core.comm import call, function_registry
from stapel_core.comm.exceptions import SchemaValidationError

from stapel_agent.providers.base import ProviderResult


class TestRegistration:
    def test_functions_registered(self):
        names = function_registry.names()
        assert "llm.complete" in names
        assert "llm.translate" in names


@pytest.mark.django_db
class TestLlmComplete:
    def test_happy_path(self, fake_provider):
        result = call("llm.complete", {"prompt": "give json", "model": "small"})
        assert result["status"] == "ok"
        assert result["result"] == {"answer": 42}
        assert result["usage"] == {"input_tokens": 10, "output_tokens": 5}

    def test_provider_and_system_prompt_forwarded(self, fake_provider):
        call(
            "llm.complete",
            {
                "prompt": "p",
                "model": "medium",
                "system_prompt": "custom",
                "provider": "fake",
            },
        )
        assert fake_provider.calls[0]["system_prompt"] == "custom"
        assert fake_provider.calls[0]["model"] == "claude-sonnet-5"

    def test_schema_rejects_missing_prompt(self, fake_provider):
        with pytest.raises(SchemaValidationError):
            call("llm.complete", {"model": "small"})

    def test_schema_rejects_bad_model_size(self, fake_provider):
        with pytest.raises(SchemaValidationError):
            call("llm.complete", {"prompt": "p", "model": "xl"})

    def test_schema_rejects_extra_keys(self, fake_provider):
        with pytest.raises(SchemaValidationError):
            call("llm.complete", {"prompt": "p", "model": "small", "beep": 1})


@pytest.mark.django_db
class TestLlmTranslate:
    def test_happy_path(self, fake_provider):
        fake_provider.result = ProviderResult(text='{"k": "Hallo"}')
        result = call(
            "llm.translate",
            {"from_lang": "auto", "to": "de", "entries": {"k": "Hello"}},
        )
        assert result == {"status": "ok", "result": {"k": "Hallo"}}

    def test_empty_entries_short_circuits(self, fake_provider):
        result = call(
            "llm.translate", {"from_lang": "auto", "to": "de", "entries": {}}
        )
        assert result == {"status": "ok", "result": {}}
        assert fake_provider.calls == []

    def test_schema_uses_from_lang_not_from(self, fake_provider):
        # The wire key "from" belongs to the HTTP surface only; the comm
        # payload uses the Python-safe "from_lang".
        with pytest.raises(SchemaValidationError):
            call("llm.translate", {"from": "auto", "to": "de", "entries": {}})

    def test_schema_rejects_non_string_entry_values(self, fake_provider):
        with pytest.raises(SchemaValidationError):
            call(
                "llm.translate",
                {"from_lang": "auto", "to": "de", "entries": {"k": 5}},
            )
