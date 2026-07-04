"""Image generation — registry merge semantics, W005/W006 checks, the
openai-images adapter (mocked requests), the service, and both surfaces."""
import base64
import json

import pytest
import requests
from stapel_core.comm import call, function_registry
from stapel_core.comm.exceptions import SchemaValidationError

from stapel_agent import services
from stapel_agent.checks import check_image_providers
from stapel_agent.images import (
    BUILTIN_IMAGE_PROVIDERS,
    _reset_runtime_image_providers,
    register_image_provider,
    registered_image_providers,
)
from stapel_agent.images.base import (
    GeneratedImage,
    ImageGenError,
    RetryableImageGenError,
)
from stapel_agent.images.providers.openai_images import OpenAIImagesProvider
from stapel_agent.models import PromptLog, PromptSource, PromptStatus
from stapel_agent.tests.fakes import (
    FakeImageProvider,
    NotAnImageProvider,
    SquareOnlyImageProvider,
)

FAKE_PATH = "stapel_agent.tests.fakes.FakeImageProvider"
SQUARE_PATH = "stapel_agent.tests.fakes.SquareOnlyImageProvider"
PNG_B64 = base64.b64encode(b"fake-png-bytes").decode()


@pytest.fixture(autouse=True)
def clean_runtime_registry():
    _reset_runtime_image_providers()
    yield
    _reset_runtime_image_providers()


class TestSettingsMerge:
    def test_settings_entries_merge_over_builtins(self, settings):
        settings.STAPEL_AGENT = {"IMAGE_PROVIDERS": {"fake-images": FAKE_PATH}}
        effective = registered_image_providers()
        assert effective["fake-images"] == FAKE_PATH
        for name, path in BUILTIN_IMAGE_PROVIDERS.items():
            assert effective[name] == path

    def test_builtins_still_resolvable_alongside_custom(self, settings):
        settings.STAPEL_AGENT = {"IMAGE_PROVIDERS": {"fake-images": FAKE_PATH}}
        assert isinstance(services.get_image_provider("fake-images"), FakeImageProvider)
        assert isinstance(
            services.get_image_provider("openai-images"), OpenAIImagesProvider
        )

    def test_settings_entry_overrides_builtin(self, settings):
        settings.STAPEL_AGENT = {"IMAGE_PROVIDERS": {"openai-images": FAKE_PATH}}
        assert isinstance(
            services.get_image_provider("openai-images"), FakeImageProvider
        )

    def test_none_removes_a_builtin(self, settings):
        settings.STAPEL_AGENT = {"IMAGE_PROVIDERS": {"openai-images": None}}
        assert "openai-images" not in registered_image_providers()
        with pytest.raises(ImageGenError, match="openai-images"):
            services.get_image_provider("openai-images")

    def test_empty_string_removes_too(self, settings):
        settings.STAPEL_AGENT = {"IMAGE_PROVIDERS": {"openai-images": ""}}
        assert "openai-images" not in registered_image_providers()


class TestRegisterImageProvider:
    def test_register_class(self):
        register_image_provider("fake-images", FakeImageProvider)
        assert registered_image_providers()["fake-images"] is FakeImageProvider
        assert isinstance(services.get_image_provider("fake-images"), FakeImageProvider)

    def test_register_dotted_path(self):
        register_image_provider("fake-images", FAKE_PATH)
        assert isinstance(services.get_image_provider("fake-images"), FakeImageProvider)

    def test_runtime_beats_settings_merge(self, settings):
        settings.STAPEL_AGENT = {"IMAGE_PROVIDERS": {"stability": FAKE_PATH}}
        register_image_provider("stability", SquareOnlyImageProvider)
        assert registered_image_providers()["stability"] is SquareOnlyImageProvider

    def test_register_none_masks_a_builtin(self):
        register_image_provider("openai-images", None)
        assert "openai-images" not in registered_image_providers()

    def test_reregistering_overrides(self):
        register_image_provider("x", FakeImageProvider)
        register_image_provider("x", SquareOnlyImageProvider)
        assert registered_image_providers()["x"] is SquareOnlyImageProvider

    def test_rejects_non_provider(self):
        with pytest.raises(TypeError, match="ImageGenProvider subclass"):
            register_image_provider("bad", NotAnImageProvider)

    def test_rejects_instances(self):
        with pytest.raises(TypeError):
            register_image_provider("bad", FakeImageProvider())


class TestImageSystemChecks:
    def test_clean_default_config(self):
        assert check_image_providers(None) == []

    def test_unimportable_dotted_path_is_w005(self, settings):
        settings.STAPEL_AGENT = {"IMAGE_PROVIDERS": {"broken": "no.such.module.Cls"}}
        issues = check_image_providers(None)
        assert [i.id for i in issues] == ["stapel_agent.W005"]
        assert "broken" in issues[0].msg

    def test_non_image_provider_class_is_w005(self, settings):
        settings.STAPEL_AGENT = {
            "IMAGE_PROVIDERS": {"bad": "stapel_agent.tests.fakes.NotAnImageProvider"}
        }
        assert [i.id for i in check_image_providers(None)] == ["stapel_agent.W005"]

    def test_unknown_default_is_w006(self, settings):
        settings.STAPEL_AGENT = {"DEFAULT_IMAGE_PROVIDER": "ghost"}
        issues = check_image_providers(None)
        assert [i.id for i in issues] == ["stapel_agent.W006"]
        assert "ghost" in issues[0].msg

    def test_removing_the_default_is_w006(self, settings):
        settings.STAPEL_AGENT = {"IMAGE_PROVIDERS": {"openai-images": None}}
        assert "stapel_agent.W006" in [i.id for i in check_image_providers(None)]

    def test_runtime_registered_class_passes(self):
        register_image_provider("stability", FakeImageProvider)
        assert check_image_providers(None) == []

    def test_registered_with_django(self):
        from django.core.checks.registry import registry

        assert check_image_providers in registry.registered_checks


class FakeResponse:
    def __init__(self, payload=None, status_code=200, text=None):
        self._payload = payload
        self.status_code = status_code
        self.text = text if text is not None else json.dumps(payload or {})

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class TestOpenAIImagesAdapter:
    @pytest.fixture
    def configured(self, settings):
        settings.STAPEL_AGENT = {
            "IMAGES_BASE_URL": "http://images.test/v1/",
            "IMAGES_API_KEY": "sk-img",
            "IMAGES_MODEL": "gpt-image-1",
        }
        return settings

    def _run(self, monkeypatch, responses, **kwargs):
        captured = []
        queue = list(responses)

        def fake_post(url, json=None, headers=None, timeout=None):
            captured.append(
                {"url": url, "json": json, "headers": headers, "timeout": timeout}
            )
            step = queue.pop(0)
            if isinstance(step, Exception):
                raise step
            return step

        monkeypatch.setattr(
            "stapel_agent.images.providers.openai_images.requests.post", fake_post
        )
        result = OpenAIImagesProvider().generate(prompt="a cat", **kwargs)
        return result, captured

    def test_unconfigured_base_url_is_fatal(self, settings):
        settings.STAPEL_AGENT = {
            "IMAGES_BASE_URL": "",
            "OPENAI_COMPAT_BASE_URL": "",
        }
        with pytest.raises(ImageGenError, match="IMAGES_BASE_URL"):
            OpenAIImagesProvider().generate(prompt="x")

    def test_b64_response(self, configured, monkeypatch):
        images, captured = self._run(
            monkeypatch,
            [FakeResponse({"data": [{"b64_json": PNG_B64}]})],
            size="512x512",
            n=2,
            timeout_seconds=30,
        )
        call_ = captured[0]
        assert call_["url"] == "http://images.test/v1/images/generations"
        assert call_["headers"]["Authorization"] == "Bearer sk-img"
        assert call_["json"] == {
            "prompt": "a cat",
            "n": 2,
            "size": "512x512",
            "model": "gpt-image-1",
        }
        assert call_["timeout"] == 30
        assert images == [GeneratedImage(mime="image/png", data_b64=PNG_B64)]

    def test_url_response(self, configured, monkeypatch):
        images, _ = self._run(
            monkeypatch,
            [FakeResponse({"data": [{"url": "https://img.test/out.png"}]})],
        )
        assert images == [GeneratedImage(mime="image/png", url="https://img.test/out.png")]

    def test_falls_back_to_openai_compat_settings(self, settings, monkeypatch):
        settings.STAPEL_AGENT = {
            "OPENAI_COMPAT_BASE_URL": "https://api.openai.test/v1",
            "OPENAI_COMPAT_API_KEY": "sk-compat",
        }
        _, captured = self._run(
            monkeypatch, [FakeResponse({"data": [{"b64_json": PNG_B64}]})]
        )
        assert captured[0]["url"] == "https://api.openai.test/v1/images/generations"
        assert captured[0]["headers"]["Authorization"] == "Bearer sk-compat"
        # no IMAGES_MODEL configured → the key is omitted entirely
        assert "model" not in captured[0]["json"]

    def test_429_is_retryable(self, configured, monkeypatch):
        with pytest.raises(RetryableImageGenError, match="rate-limited") as e:
            self._run(monkeypatch, [FakeResponse(status_code=429, text="slow")])
        assert e.value.status_code == 429

    def test_5xx_is_retryable(self, configured, monkeypatch):
        with pytest.raises(RetryableImageGenError, match="503"):
            self._run(monkeypatch, [FakeResponse(status_code=503, text="down")])

    def test_4xx_is_fatal(self, configured, monkeypatch):
        with pytest.raises(ImageGenError, match="400") as e:
            self._run(monkeypatch, [FakeResponse(status_code=400, text="bad size")])
        assert not isinstance(e.value, RetryableImageGenError)

    def test_timeout_is_retryable(self, configured, monkeypatch):
        with pytest.raises(RetryableImageGenError, match="timed out"):
            self._run(monkeypatch, [requests.Timeout("slow")])

    def test_transport_error_is_retryable(self, configured, monkeypatch):
        with pytest.raises(RetryableImageGenError, match="transport"):
            self._run(monkeypatch, [requests.ConnectionError("refused")])

    def test_non_json_is_retryable(self, configured, monkeypatch):
        with pytest.raises(RetryableImageGenError, match="non-JSON"):
            self._run(monkeypatch, [FakeResponse(payload=None, text="<html>")])

    def test_empty_data_is_fatal(self, configured, monkeypatch):
        with pytest.raises(ImageGenError, match="no images"):
            self._run(monkeypatch, [FakeResponse({"data": []})])


@pytest.mark.django_db
class TestGenerateImageService:
    def test_happy_path(self, fake_images):
        result = services.generate_image("a cat", size="512x512", n=1)
        assert result == {
            "status": "ok",
            "images": [{"data_b64": PNG_B64, "mime": "image/png"}],
            "provider_used": "fake-images",
        }
        assert fake_images.calls[0] == {
            "prompt": "a cat",
            "size": "512x512",
            "n": 1,
            "timeout_seconds": None,
        }

    def test_unknown_provider_is_failure(self, fake_images):
        result = services.generate_image("a cat", provider="ghost")
        assert result["status"] == "failure"
        assert "ghost" in result["reason"]
        assert fake_images.calls == []

    def test_unsupported_size_fails_before_the_call(self, fake_images):
        result = services.generate_image(
            "a cat", size="1792x1024", provider="square-images"
        )
        assert result["status"] == "failure"
        assert "1792x1024" in result["reason"]
        assert SquareOnlyImageProvider.calls == []

    def test_supported_size_passes_the_gate(self, fake_images):
        result = services.generate_image(
            "a cat", size="1024x1024", provider="square-images"
        )
        assert result["status"] == "ok"

    def test_provider_error_is_failure_envelope(self, fake_images):
        fake_images.error = ImageGenError("prompt rejected", provider="fake-images")
        result = services.generate_image("bad prompt")
        assert result == {"status": "failure", "reason": "prompt rejected"}

    def test_unloadable_provider_is_failure(self, settings):
        settings.STAPEL_AGENT = {
            "IMAGE_PROVIDERS": {"broken": "no.such.module.ImgCls"},
        }
        result = services.generate_image("x", provider="broken")
        assert result["status"] == "failure"
        assert "could not be loaded" in result["reason"]
        log = PromptLog.objects.get()
        assert log.status == PromptStatus.ERROR
        assert log.model == "broken"

    def test_success_ledger_row(self, fake_images):
        services.generate_image(
            "a cat on a mat", user_id="u-3", metadata={"origin": "test"}
        )
        log = PromptLog.objects.get()
        assert log.source == PromptSource.GENERATE_IMAGE
        assert log.status == PromptStatus.SUCCESS
        assert log.model == "fake-images"  # provider name, not an LLM model
        assert log.model_size == ""
        assert log.prompt == "a cat on a mat"
        # the response is NEVER logged raw — only shape metadata
        assert log.response is None
        assert PNG_B64 not in json.dumps(log.metadata)
        assert log.metadata["origin"] == "test"
        assert log.metadata["size"] == "1024x1024"
        assert log.metadata["n"] == 1
        assert log.metadata["images"] == {
            "count": 1,
            "mimes": ["image/png"],
            "bytes_total": len(b"fake-png-bytes"),
        }
        # image generation has no token accounting
        assert log.input_tokens is None
        assert log.output_tokens is None
        assert log.duration_ms is not None
        assert log.user_id == "u-3"

    def test_failure_ledger_row(self, fake_images):
        fake_images.error = ImageGenError("nope", provider="fake-images")
        services.generate_image("x")
        log = PromptLog.objects.get()
        assert log.status == PromptStatus.ERROR
        assert log.error_message == "nope"
        assert log.response is None


@pytest.mark.django_db
class TestGenerateImageHttp:
    URL = "/agent/api/llm/generate-image"

    def _post(self, client, body=None, **kwargs):
        return client.post(
            self.URL, body or {"prompt": "a cat"}, format="json", **kwargs
        )

    def test_anonymous_rejected(self, api_client, fake_images):
        assert self._post(api_client).status_code in (401, 403)
        assert fake_images.calls == []

    def test_wrong_api_key_rejected(self, api_client, fake_images):
        resp = self._post(api_client, HTTP_X_API_KEY="wrong-key")
        assert resp.status_code in (401, 403)

    def test_service_key_happy_path(self, api_client, fake_images):
        resp = self._post(
            api_client,
            {"prompt": "a cat", "size": "512x512", "n": 2, "provider": "fake-images"},
            HTTP_X_API_KEY="test-service-key",
        )
        assert resp.status_code == 200, resp.content
        data = resp.json()
        assert data["status"] == "ok"
        assert data["provider_used"] == "fake-images"
        assert data["images"] == [{"data_b64": PNG_B64, "mime": "image/png"}]
        assert fake_images.calls[0]["n"] == 2

    def test_staff_user_accepted_and_logged(self, staff_client, staff_user, fake_images):
        assert self._post(staff_client).status_code == 200
        log = PromptLog.objects.get()
        assert log.source == "generate_image"
        assert log.user_id == str(staff_user.pk)

    def test_generation_failure_is_http_200(self, api_client, fake_images):
        fake_images.error = ImageGenError("backend down", provider="fake-images")
        resp = self._post(api_client, HTTP_X_API_KEY="test-service-key")
        assert resp.status_code == 200
        assert resp.json() == {"status": "failure", "reason": "backend down"}

    def test_missing_prompt_is_400(self, api_client, fake_images):
        resp = self._post(
            api_client, {"size": "512x512"}, HTTP_X_API_KEY="test-service-key"
        )
        assert resp.status_code == 400

    def test_n_out_of_bounds_is_400(self, api_client, fake_images):
        for bad_n in (0, 11):
            resp = self._post(
                api_client,
                {"prompt": "x", "n": bad_n},
                HTTP_X_API_KEY="test-service-key",
            )
            assert resp.status_code == 400, bad_n
        assert fake_images.calls == []


@pytest.mark.django_db
class TestGenerateImageComm:
    def test_function_registered(self):
        assert "llm.generate_image" in function_registry.names()

    def test_happy_path(self, fake_images):
        result = call("llm.generate_image", {"prompt": "a cat", "size": "512x512"})
        assert result["status"] == "ok"
        assert result["images"][0]["data_b64"] == PNG_B64
        assert result["provider_used"] == "fake-images"

    def test_defaults_applied(self, fake_images):
        call("llm.generate_image", {"prompt": "a cat"})
        assert fake_images.calls[0]["size"] == "1024x1024"
        assert fake_images.calls[0]["n"] == 1

    def test_failure_is_a_status_dict(self, fake_images):
        result = call("llm.generate_image", {"prompt": "x", "provider": "ghost"})
        assert result["status"] == "failure"
        assert "ghost" in result["reason"]

    def test_schema_rejects_missing_prompt(self, fake_images):
        with pytest.raises(SchemaValidationError):
            call("llm.generate_image", {"size": "512x512"})

    def test_schema_rejects_bad_n(self, fake_images):
        with pytest.raises(SchemaValidationError):
            call("llm.generate_image", {"prompt": "x", "n": 0})
        with pytest.raises(SchemaValidationError):
            call("llm.generate_image", {"prompt": "x", "n": 11})

    def test_schema_rejects_extra_keys(self, fake_images):
        with pytest.raises(SchemaValidationError):
            call("llm.generate_image", {"prompt": "x", "beep": 1})
