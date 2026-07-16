"""Vision input through llm.complete — provider content-block mapping,
the supports_images service guard, cache bypass, ledger metadata, and
both surfaces (HTTP + comm) with schema rejects."""
import base64
import json
import sys
import types

import pytest
from stapel_core.comm import call
from stapel_core.comm.exceptions import SchemaValidationError

from stapel_agent import services
from stapel_agent.images.base import ImageRef
from stapel_agent.models import PromptLog, PromptSource
from stapel_agent.providers.anthropic import AnthropicProvider
from stapel_agent.providers.claude_cli import ClaudeCodeCLIProvider
from stapel_agent.providers.openai_compat import OpenAICompatProvider

PNG = b"\x89PNG\r\n\x1a\nfake"
PNG_B64 = base64.b64encode(PNG).decode()
URL_REF = ImageRef(url="https://cdn.test/shot.png")
DATA_REF = ImageRef(data=PNG, mime="image/webp")


class TestAnthropicMapping:
    def _capture(self, monkeypatch, settings):
        settings.STAPEL_AGENT = {"ANTHROPIC_API_KEY": "sk-ant-test"}
        captured = {}
        fake = types.ModuleType("anthropic")

        class FakeMessages:
            def create(self, **kwargs):
                captured["create_kwargs"] = kwargs
                usage = types.SimpleNamespace(
                    input_tokens=1, output_tokens=1,
                    cache_read_input_tokens=0, cache_creation_input_tokens=0,
                )
                block = types.SimpleNamespace(type="text", text="a cat")
                return types.SimpleNamespace(content=[block], usage=usage)

        class FakeAnthropic:
            def __init__(self, api_key):
                self.messages = FakeMessages()

        fake.Anthropic = FakeAnthropic
        monkeypatch.setitem(sys.modules, "anthropic", fake)
        return captured

    def test_supports_images_flag(self):
        assert AnthropicProvider.supports_images is True

    def test_content_blocks_url_and_base64(self, settings, monkeypatch):
        captured = self._capture(monkeypatch, settings)
        AnthropicProvider().complete(
            prompt="what is on these?", model="m", images=[URL_REF, DATA_REF]
        )
        content = captured["create_kwargs"]["messages"][0]["content"]
        assert content == [
            {"type": "image", "source": {"type": "url", "url": "https://cdn.test/shot.png"}},
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/webp",
                    "data": PNG_B64,
                },
            },
            {"type": "text", "text": "what is on these?"},
        ]

    def test_no_images_keeps_plain_string_content(self, settings, monkeypatch):
        captured = self._capture(monkeypatch, settings)
        AnthropicProvider().complete(prompt="hi", model="m")
        assert captured["create_kwargs"]["messages"] == [
            {"role": "user", "content": "hi"}
        ]


class TestOpenAICompatMapping:
    def _capture(self, monkeypatch, settings):
        settings.STAPEL_AGENT = {"OPENAI_COMPAT_BASE_URL": "https://api.x.test/v1"}
        captured = {}

        class FakeResponse:
            status_code = 200
            text = "{}"

            def json(self):
                return {
                    "choices": [{"message": {"content": "a dog"}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                }

        def fake_post(url, json=None, headers=None, timeout=None):
            captured.update(url=url, json=json)
            return FakeResponse()

        monkeypatch.setattr(
            "stapel_agent.providers.openai_compat.requests.post", fake_post
        )
        return captured

    def test_supports_images_flag(self):
        assert OpenAICompatProvider.supports_images is True

    def test_content_parts_url_and_data_uri(self, settings, monkeypatch):
        captured = self._capture(monkeypatch, settings)
        OpenAICompatProvider().complete(
            prompt="describe", model="gpt-4o", system_prompt="sys",
            images=[URL_REF, DATA_REF],
        )
        assert captured["json"]["messages"] == [
            {"role": "system", "content": "sys"},
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": "https://cdn.test/shot.png"},
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/webp;base64,{PNG_B64}"},
                    },
                    {"type": "text", "text": "describe"},
                ],
            },
        ]

    def test_no_images_keeps_plain_string_content(self, settings, monkeypatch):
        captured = self._capture(monkeypatch, settings)
        OpenAICompatProvider().complete(prompt="hi", model="m")
        assert captured["json"]["messages"] == [{"role": "user", "content": "hi"}]


@pytest.mark.django_db
class TestVisionService:
    def test_images_forwarded_to_the_provider(self, fake_provider):
        result = services.complete(
            "what is this?", "small", images=[URL_REF, DATA_REF], source="other"
        )
        assert result["status"] == "ok"
        assert fake_provider.calls[0]["images"] == [URL_REF, DATA_REF]

    def test_no_images_means_no_images_kwarg(self, fake_provider):
        # NoVisionProvider's complete() has the pre-vision signature — a
        # text-only call through it must not explode.
        from stapel_agent.tests.fakes import NoVisionProvider

        from stapel_agent.providers import register_provider

        register_provider("no-vision", NoVisionProvider)
        try:
            result = services.complete("hi", "small", provider="no-vision", source="other")
            assert result["status"] == "ok"
        finally:
            from stapel_agent.providers import _reset_runtime_providers

            _reset_runtime_providers()

    def test_claude_cli_does_not_support_images(self, settings):
        assert ClaudeCodeCLIProvider.supports_images is False
        result = services.complete(
            "hi", "small", provider="claude-code", images=[URL_REF], source="other"
        )
        assert result == {
            "status": "failure",
            "reason": "Provider 'claude-code' does not support image input",
        }
        assert PromptLog.objects.count() == 0  # never reached the backend

    def test_unsupporting_provider_fails_before_the_call(self, fake_provider):
        from stapel_agent.providers import _reset_runtime_providers, register_provider
        from stapel_agent.tests.fakes import NoVisionProvider

        register_provider("no-vision", NoVisionProvider)
        try:
            result = services.complete(
                "hi", "small", provider="no-vision", images=[URL_REF], source="other"
            )
            assert result["status"] == "failure"
            assert "does not support image input" in result["reason"]
            assert fake_provider.calls == []
        finally:
            _reset_runtime_providers()

    def test_image_requests_bypass_cache_lookup_and_store(self, settings, fake_provider):
        settings.STAPEL_AGENT = {
            **settings.STAPEL_AGENT,
            "CACHE_LOOKUP": {"llm_facade": True},
        }
        for _ in range(2):
            services.complete(
                "same text", "small", images=[URL_REF], source=PromptSource.LLM_FACADE
            )
        # identical text over (potentially different) pixels: no cache hit
        assert len(fake_provider.calls) == 2
        # and the rows must not serve later TEXT-only lookups either:
        # a pure text call afterwards may hit only text-keyed rows.
        services.complete("same text", "small", source=PromptSource.LLM_FACADE)
        assert len(fake_provider.calls) == 3  # hit a multimodal row = bug

    def test_ledger_metadata_counts_kinds_never_bytes(self, fake_provider):
        services.complete(
            "look", "small", images=[URL_REF, DATA_REF], source=PromptSource.LLM_FACADE
        )
        log = PromptLog.objects.get()
        assert log.metadata["images"] == {"count": 2, "kinds": ["url", "data"]}
        assert PNG_B64 not in json.dumps(log.metadata)
        assert log.prompt == "look"  # prompt text still logged


@pytest.mark.django_db
class TestVisionHttp:
    URL = "/agent/api/v1/llm/complete"

    def _post(self, client, body):
        return client.post(
            self.URL, body, format="json", HTTP_X_API_KEY="test-service-key"
        )

    def test_images_accepted_url_and_b64(self, api_client, fake_provider):
        resp = self._post(
            api_client,
            {
                "prompt": "what?",
                "model": "small",
                "images": [
                    {"url": "https://cdn.test/shot.png"},
                    {"data_b64": PNG_B64, "mime": "image/webp"},
                ],
            },
        )
        assert resp.status_code == 200, resp.content
        assert resp.json()["status"] == "ok"
        sent = fake_provider.calls[0]["images"]
        assert [i.kind for i in sent] == ["url", "data"]
        assert sent[1].data == PNG
        assert sent[1].mime == "image/webp"

    def test_backward_compatible_without_images(self, api_client, fake_provider):
        resp = self._post(api_client, {"prompt": "x", "model": "small"})
        assert resp.status_code == 200
        assert fake_provider.calls[0]["images"] is None

    def test_bad_base64_is_400(self, api_client, fake_provider):
        resp = self._post(
            api_client,
            {"prompt": "x", "model": "small", "images": [{"data_b64": "@@not-b64@@"}]},
        )
        assert resp.status_code == 400
        assert fake_provider.calls == []

    def test_both_url_and_b64_in_one_entry_is_400(self, api_client, fake_provider):
        resp = self._post(
            api_client,
            {
                "prompt": "x",
                "model": "small",
                "images": [{"url": "https://x/a.png", "data_b64": PNG_B64}],
            },
        )
        assert resp.status_code == 400

    def test_neither_key_in_one_entry_is_400(self, api_client, fake_provider):
        resp = self._post(
            api_client,
            {"prompt": "x", "model": "small", "images": [{"mime": "image/png"}]},
        )
        assert resp.status_code == 400

    def test_non_dict_entry_is_rejected(self, api_client, fake_provider):
        from stapel_core.django.api.errors import StapelValidationError

        from stapel_agent.serializers import CompleteRequestSerializer

        resp = self._post(
            api_client, {"prompt": "x", "model": "small", "images": ["nope"]}
        )
        assert resp.status_code == 400
        # and the validator itself rejects non-dict entries regardless of
        # what the field layer lets through
        with pytest.raises(StapelValidationError):
            CompleteRequestSerializer().validate_images(["nope"])


@pytest.mark.django_db
class TestVisionComm:
    def test_images_accepted(self, fake_provider):
        result = call(
            "llm.complete",
            {
                "prompt": "what?",
                "model": "small",
                "images": [
                    {"url": "https://cdn.test/shot.png"},
                    {"data_b64": PNG_B64},
                ],
            },
        )
        assert result["status"] == "ok"
        sent = fake_provider.calls[0]["images"]
        assert [i.kind for i in sent] == ["url", "data"]

    def test_schema_rejects_raw_bytes_key(self, fake_provider):
        # comm carries url/data_b64 only — a raw "data" key is rejected.
        with pytest.raises(SchemaValidationError):
            call(
                "llm.complete",
                {"prompt": "x", "model": "small", "images": [{"data": "AAAA"}]},
            )

    def test_schema_rejects_entry_with_both(self, fake_provider):
        with pytest.raises(SchemaValidationError):
            call(
                "llm.complete",
                {
                    "prompt": "x",
                    "model": "small",
                    "images": [{"url": "https://x/a", "data_b64": PNG_B64}],
                },
            )

    def test_schema_rejects_empty_entry(self, fake_provider):
        with pytest.raises(SchemaValidationError):
            call("llm.complete", {"prompt": "x", "model": "small", "images": [{}]})

    def test_bad_base64_degrades_to_failure_envelope(self, fake_provider):
        # b64 validity is beyond JSON Schema — the handler catches the
        # decode error instead of leaking a traceback through comm.
        result = call(
            "llm.complete",
            {"prompt": "x", "model": "small", "images": [{"data_b64": "@@nope@@"}]},
        )
        assert result["status"] == "failure"
        assert "Invalid image payload" in result["reason"]
        assert fake_provider.calls == []
