"""Embeddings seam — normalized schema and the empty-texts gate, both
built-in adapters (mocked ``requests``; no network, no keys), registry
merge semantics, W009/W010 checks, the ``services.embed`` surface, the
``llm.embed`` comm function, the HTTP endpoint and the ledger privacy
canon (counts/usage only — NEVER the texts, never the vectors).

Error taxonomy under test everywhere: 429/5xx/timeouts/transport →
``RetryableEmbeddingError``, other 4xx / bad input / missing config /
count mismatch → fatal ``EmbeddingError``.
"""
import json

import pytest
import requests

from stapel_core.comm import call, function_registry
from stapel_core.comm.exceptions import SchemaValidationError

from stapel_agent import services
from stapel_agent.checks import check_embedding_providers
from stapel_agent.embeddings import (
    BUILTIN_EMBEDDING_PROVIDERS,
    _reset_runtime_embedding_providers,
    register_embedding_provider,
    registered_embedding_providers,
)
from stapel_agent.embeddings.base import (
    EmbeddingError,
    NormalizedEmbeddings,
    RetryableEmbeddingError,
    require_texts,
)
from stapel_agent.embeddings.providers.http_server import HttpServerEmbeddingsProvider
from stapel_agent.embeddings.providers.openai_compat import OpenAIEmbeddingsProvider
from stapel_agent.models import PromptLog
from stapel_agent.providers.base import ProviderError
from stapel_agent.tests.fakes import FakeEmbeddingProvider

EMBED_URL = "/agent/api/v1/llm/embed"


@pytest.fixture(autouse=True)
def clean_runtime_registry():
    _reset_runtime_embedding_providers()
    yield
    _reset_runtime_embedding_providers()


# ─── base ──────────────────────────────────────────────────────────────


class TestBase:
    def test_error_taxonomy_joins_the_house_hierarchy(self):
        assert issubclass(EmbeddingError, ProviderError)
        assert issubclass(RetryableEmbeddingError, EmbeddingError)
        err = EmbeddingError("boom", provider="p", status_code=401)
        assert err.provider == "p"
        assert err.status_code == 401

    def test_require_texts_accepts_a_batch(self):
        assert require_texts(("a", "b")) == ["a", "b"]

    @pytest.mark.parametrize(
        "bad, match",
        [
            ([], "empty"),
            ("not-a-list", "list of strings"),
            (["ok", 42], "not a string"),
            (["ok", "   "], "empty"),
        ],
    )
    def test_require_texts_rejects_fatal(self, bad, match):
        with pytest.raises(EmbeddingError, match=match) as exc_info:
            require_texts(bad, provider="p")
        assert not isinstance(exc_info.value, RetryableEmbeddingError)

    def test_to_dict(self):
        emb = NormalizedEmbeddings(
            provider="x", model="m", dim=2, vectors=[[1.0, 2.0]], usage={"t": 1}
        )
        data = emb.to_dict()
        assert data["vectors"] == [[1.0, 2.0]]
        assert data["dim"] == 2
        assert data["raw"] == {}


# ─── adapters ──────────────────────────────────────────────────────────


class FakeResponse:
    def __init__(self, payload=None, status_code=200, text=None):
        self._payload = payload
        self.status_code = status_code
        self.text = text if text is not None else json.dumps(payload or {})

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def mock_post(monkeypatch, module, responses, captured):
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
        f"stapel_agent.embeddings.providers.{module}.requests.post", fake_post
    )


OPENAI_BODY = {
    "object": "list",
    "data": [
        # Deliberately OUT of order on the wire — the adapter must
        # re-order by index so vectors match input positions.
        {"object": "embedding", "index": 1, "embedding": [0.3, 0.4]},
        {"object": "embedding", "index": 0, "embedding": [0.1, 0.2]},
    ],
    "model": "text-embedding-3-small-2024",
    "usage": {"prompt_tokens": 5, "total_tokens": 5},
}


class TestOpenAIEmbeddings:
    @pytest.fixture
    def configured(self, settings):
        settings.STAPEL_AGENT = {
            "EMBEDDINGS_BASE_URL": "https://api.openai.test/v1/",
            "EMBEDDINGS_API_KEY": "emb-key",
            "EMBEDDINGS_MODEL": "text-embedding-3-small",
        }
        return settings

    def _run(self, monkeypatch, responses, **kwargs):
        captured = []
        mock_post(monkeypatch, "openai_compat", responses, captured)
        result = OpenAIEmbeddingsProvider().embed(**kwargs)
        return result, captured

    def test_missing_base_url_is_fatal(self, settings):
        settings.STAPEL_AGENT = {}
        with pytest.raises(EmbeddingError, match="not configured"):
            OpenAIEmbeddingsProvider().embed(texts=["a"])

    def test_falls_back_to_openai_compat_settings(self, settings, monkeypatch):
        settings.STAPEL_AGENT = {
            "OPENAI_COMPAT_BASE_URL": "https://compat.test/v1",
            "OPENAI_COMPAT_API_KEY": "compat-key",
        }
        captured = []
        mock_post(monkeypatch, "openai_compat", [FakeResponse(OPENAI_BODY)], captured)
        OpenAIEmbeddingsProvider().embed(texts=["a", "b"])
        assert captured[0]["url"] == "https://compat.test/v1/embeddings"
        assert captured[0]["headers"]["Authorization"] == "Bearer compat-key"

    def test_happy_path_exact_request_shape(self, configured, monkeypatch):
        result, captured = self._run(
            monkeypatch,
            [FakeResponse(OPENAI_BODY)],
            texts=["first", "second"],
            timeout_seconds=30,
        )
        req = captured[0]
        assert req["url"] == "https://api.openai.test/v1/embeddings"
        assert req["json"] == {
            "model": "text-embedding-3-small",
            "input": ["first", "second"],
        }
        assert req["headers"] == {
            "Content-Type": "application/json",
            "Authorization": "Bearer emb-key",
        }
        assert req["timeout"] == 30

        assert result.provider == "openai-embeddings"
        # response echo wins over the requested model — real attribution
        assert result.model == "text-embedding-3-small-2024"
        assert result.dim == 2
        assert result.usage == {"prompt_tokens": 5, "total_tokens": 5}

    def test_order_restored_from_wire_indexes(self, configured, monkeypatch):
        result, _ = self._run(
            monkeypatch, [FakeResponse(OPENAI_BODY)], texts=["first", "second"]
        )
        # wire order was [index 1, index 0] — output must be input order
        assert result.vectors == [[0.1, 0.2], [0.3, 0.4]]

    def test_raw_never_carries_the_vectors_twice(self, configured, monkeypatch):
        result, _ = self._run(
            monkeypatch, [FakeResponse(OPENAI_BODY)], texts=["a", "b"]
        )
        assert "data" not in result.raw
        assert result.raw["model"] == "text-embedding-3-small-2024"

    def test_provider_options_merged_after_own_params(self, configured, monkeypatch):
        _, captured = self._run(
            monkeypatch,
            [FakeResponse(OPENAI_BODY)],
            texts=["a", "b"],
            provider_options={"dimensions": 256, "model": "pinned"},
        )
        assert captured[0]["json"] == {
            "model": "pinned",  # caller's pin wins — applied after
            "input": ["a", "b"],
            "dimensions": 256,
        }

    def test_count_mismatch_is_fatal(self, configured, monkeypatch):
        with pytest.raises(EmbeddingError, match="count mismatch") as exc_info:
            self._run(
                monkeypatch, [FakeResponse(OPENAI_BODY)], texts=["a", "b", "c"]
            )
        assert not isinstance(exc_info.value, RetryableEmbeddingError)

    def test_empty_batch_rejected_before_any_call(self, configured, monkeypatch):
        captured = []
        mock_post(monkeypatch, "openai_compat", [], captured)
        with pytest.raises(EmbeddingError, match="empty"):
            OpenAIEmbeddingsProvider().embed(texts=[])
        assert captured == []

    @pytest.mark.parametrize("status", [429, 500, 503])
    def test_transient_statuses_are_retryable(self, configured, monkeypatch, status):
        with pytest.raises(RetryableEmbeddingError):
            self._run(
                monkeypatch,
                [FakeResponse(status_code=status, text="busy")],
                texts=["a"],
            )

    def test_client_error_is_fatal(self, configured, monkeypatch):
        with pytest.raises(EmbeddingError) as exc_info:
            self._run(
                monkeypatch,
                [FakeResponse(status_code=401, text="bad key")],
                texts=["a"],
            )
        assert not isinstance(exc_info.value, RetryableEmbeddingError)
        assert exc_info.value.status_code == 401

    def test_timeout_transport_and_non_json_are_retryable(
        self, configured, monkeypatch
    ):
        with pytest.raises(RetryableEmbeddingError):
            self._run(monkeypatch, [requests.Timeout("slow")], texts=["a"])
        with pytest.raises(RetryableEmbeddingError):
            self._run(monkeypatch, [requests.ConnectionError("down")], texts=["a"])
        with pytest.raises(RetryableEmbeddingError, match="non-JSON"):
            self._run(monkeypatch, [FakeResponse(text="<html>")], texts=["a"])

    def test_missing_data_list_is_fatal(self, configured, monkeypatch):
        with pytest.raises(EmbeddingError, match="no 'data' list"):
            self._run(monkeypatch, [FakeResponse({"model": "m"})], texts=["a"])


HTTP_BODY = {
    "vectors": [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]],
    "model": "bge-m3",
    "dim": 3,
}


class TestHttpServerEmbeddings:
    @pytest.fixture
    def configured(self, settings):
        settings.STAPEL_AGENT = {
            "EMBEDDINGS_HTTP_BASE_URL": "http://bge-m3:9000/",
        }
        return settings

    def _run(self, monkeypatch, responses, **kwargs):
        captured = []
        mock_post(monkeypatch, "http_server", responses, captured)
        result = HttpServerEmbeddingsProvider().embed(**kwargs)
        return result, captured

    def test_missing_base_url_is_fatal(self, settings):
        settings.STAPEL_AGENT = {}
        with pytest.raises(EmbeddingError, match="EMBEDDINGS_HTTP_BASE_URL"):
            HttpServerEmbeddingsProvider().embed(texts=["a"])

    def test_happy_path_exact_request_shape(self, configured, monkeypatch):
        result, captured = self._run(
            monkeypatch, [FakeResponse(HTTP_BODY)], texts=["раз", "два"]
        )
        req = captured[0]
        assert req["url"] == "http://bge-m3:9000/embed"
        assert req["json"] == {"texts": ["раз", "два"]}
        # no key configured → no Authorization header
        assert req["headers"] == {"Content-Type": "application/json"}
        assert req["timeout"] == 120  # EMBEDDINGS_TIMEOUT default

        assert result.provider == "embeddings-http"
        assert result.model == "bge-m3"  # server echo — real attribution
        assert result.dim == 3
        assert result.vectors == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
        assert result.usage is None
        assert "vectors" not in result.raw

    def test_key_becomes_bearer_header(self, settings, monkeypatch):
        settings.STAPEL_AGENT = {
            "EMBEDDINGS_HTTP_BASE_URL": "http://bge-m3:9000",
            "EMBEDDINGS_HTTP_API_KEY": "self-host-key",
        }
        _, captured = self._run(monkeypatch, [FakeResponse(HTTP_BODY)], texts=["a", "b"])
        assert captured[0]["headers"]["Authorization"] == "Bearer self-host-key"

    def test_provider_options_merged_into_body(self, configured, monkeypatch):
        _, captured = self._run(
            monkeypatch,
            [FakeResponse(HTTP_BODY)],
            texts=["a", "b"],
            provider_options={"normalize": True},
        )
        assert captured[0]["json"] == {"texts": ["a", "b"], "normalize": True}

    def test_dim_inferred_when_absent(self, configured, monkeypatch):
        result, _ = self._run(
            monkeypatch, [FakeResponse({"vectors": [[1.0, 2.0]]})], texts=["a"]
        )
        assert result.dim == 2
        assert result.model is None  # no echo → no pretended attribution

    def test_count_mismatch_is_fatal(self, configured, monkeypatch):
        with pytest.raises(EmbeddingError, match="count mismatch"):
            self._run(monkeypatch, [FakeResponse(HTTP_BODY)], texts=["a"])

    def test_missing_vectors_list_is_fatal(self, configured, monkeypatch):
        with pytest.raises(EmbeddingError, match="no 'vectors' list"):
            self._run(monkeypatch, [FakeResponse({"model": "m"})], texts=["a"])

    @pytest.mark.parametrize("status", [429, 502])
    def test_transient_statuses_are_retryable(self, configured, monkeypatch, status):
        with pytest.raises(RetryableEmbeddingError):
            self._run(
                monkeypatch,
                [FakeResponse(status_code=status, text="busy")],
                texts=["a"],
            )

    def test_client_error_is_fatal(self, configured, monkeypatch):
        with pytest.raises(EmbeddingError) as exc_info:
            self._run(
                monkeypatch,
                [FakeResponse(status_code=413, text="too large")],
                texts=["a"],
            )
        assert not isinstance(exc_info.value, RetryableEmbeddingError)


# ─── registry + checks ─────────────────────────────────────────────────


class TestRegistry:
    def test_builtins_registered(self):
        effective = registered_embedding_providers()
        assert "openai-embeddings" in effective
        assert "embeddings-http" in effective
        assert set(BUILTIN_EMBEDDING_PROVIDERS) == {
            "openai-embeddings", "embeddings-http",
        }

    def test_settings_merge_over_builtins(self, settings):
        settings.STAPEL_AGENT = {
            "EMBEDDING_PROVIDERS": {
                "custom": "stapel_agent.tests.fakes.FakeEmbeddingProvider",
                "embeddings-http": None,  # None removes a name
            }
        }
        effective = registered_embedding_providers()
        assert "custom" in effective
        assert "embeddings-http" not in effective
        assert "openai-embeddings" in effective  # built-ins not restated

    def test_runtime_beats_settings(self, settings):
        settings.STAPEL_AGENT = {"EMBEDDING_PROVIDERS": {"x": "not.a.real.Path"}}
        register_embedding_provider("x", FakeEmbeddingProvider)
        assert registered_embedding_providers()["x"] is FakeEmbeddingProvider

    def test_register_rejects_non_provider(self):
        with pytest.raises(TypeError):
            register_embedding_provider("bad", object)


class TestSystemChecks:
    def test_clean_default_config(self):
        assert check_embedding_providers(None) == []

    def test_unimportable_entry_is_w009(self, settings):
        settings.STAPEL_AGENT = {
            "EMBEDDING_PROVIDERS": {"broken": "no.such.module.Cls"}
        }
        assert [i.id for i in check_embedding_providers(None)] == [
            "stapel_agent.W009"
        ]

    def test_non_provider_class_is_w009(self, settings):
        settings.STAPEL_AGENT = {
            "EMBEDDING_PROVIDERS": {
                "bad": "stapel_agent.tests.fakes.NotAnEmbeddingProvider"
            }
        }
        assert [i.id for i in check_embedding_providers(None)] == [
            "stapel_agent.W009"
        ]

    def test_unknown_default_is_w010(self, settings):
        settings.STAPEL_AGENT = {"DEFAULT_EMBEDDING_PROVIDER": "ghost"}
        issues = check_embedding_providers(None)
        assert [i.id for i in issues] == ["stapel_agent.W010"]
        assert "ghost" in issues[0].msg

    def test_registered_with_django(self):
        from django.core.checks.registry import registry

        assert check_embedding_providers in registry.registered_checks


# ─── service ───────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestEmbedService:
    def test_happy_path_envelope_preserves_order(self, fake_embeddings):
        result = services.embed(["alpha", "beta", "gamma"])
        assert result["status"] == "ok"
        assert result["provider_used"] == "fake-embed"
        emb = result["embeddings"]
        assert emb["provider"] == "fake-embed"
        assert emb["model"] == "fake-embed-1"
        assert emb["dim"] == 2
        # the fake fingerprints position i into vectors[i][0]
        assert [v[0] for v in emb["vectors"]] == [0.0, 1.0, 2.0]

    def test_ledger_row_is_counts_and_usage_only(self, fake_embeddings):
        secret = "СЕКРЕТНЫЙ客户text-А"
        services.embed([secret, "second secret"], user_id="u1")
        log = PromptLog.objects.get()
        assert log.source == "embed"
        assert log.model == "fake-embed"
        assert log.status == "success"
        assert log.user_id == "u1"
        # the privacy canon: counts/usage only — NEVER the texts, and no
        # vectors either. Serialize the WHOLE row surface and scan it.
        row_surface = json.dumps(
            {
                "prompt": log.prompt,
                "system_prompt": log.system_prompt,
                "response": log.response,
                "error_message": log.error_message,
                "metadata": log.metadata,
            },
            ensure_ascii=False,
        )
        assert secret not in row_surface
        assert "second secret" not in row_surface
        assert "vectors" not in row_surface
        assert log.prompt == "texts:2"
        assert log.metadata["batch_size"] == 2
        assert log.metadata["model"] == "fake-embed-1"
        assert log.metadata["dim"] == 2
        assert log.metadata["usage"] == {
            "prompt_tokens": len(secret) + len("second secret")
        }

    def test_failure_row_never_carries_texts_either(self, fake_embeddings):
        result = services.embed(["classified"], provider="fatal-embed")
        assert result == {"status": "failure", "reason": "auth rejected"}
        log = PromptLog.objects.get()
        assert log.status == "error"
        assert "classified" not in json.dumps(
            {"prompt": log.prompt, "metadata": log.metadata, "error": log.error_message}
        )
        assert log.prompt == "texts:1"

    def test_empty_batch_is_failure_envelope(self, fake_embeddings):
        result = services.embed([])
        assert result["status"] == "failure"
        assert "empty" in result["reason"]

    def test_unknown_provider_is_failure(self, fake_embeddings):
        result = services.embed(["a"], provider="ghost")
        assert result["status"] == "failure"
        assert "Unknown embedding provider 'ghost'" in result["reason"]

    def test_options_and_timeout_forwarded(self, fake_embeddings):
        services.embed(
            ["a"], timeout_seconds=15, provider_options={"normalize": True}
        )
        call = fake_embeddings.calls[0]
        assert call["timeout_seconds"] == 15
        assert call["provider_options"] == {"normalize": True}


# ─── comm function ─────────────────────────────────────────────────────


@pytest.mark.django_db
class TestLlmEmbedFunction:
    def test_registered(self):
        assert "llm.embed" in function_registry.names()

    def test_happy_path(self, fake_embeddings):
        result = call("llm.embed", {"texts": ["one", "two"]})
        assert result["status"] == "ok"
        assert result["provider_used"] == "fake-embed"
        assert [v[0] for v in result["embeddings"]["vectors"]] == [0.0, 1.0]

    def test_provider_pin_forwarded(self, fake_embeddings):
        result = call("llm.embed", {"texts": ["x"], "provider": "fatal-embed"})
        assert result == {"status": "failure", "reason": "auth rejected"}

    def test_schema_rejects_missing_texts(self, fake_embeddings):
        with pytest.raises(SchemaValidationError):
            call("llm.embed", {"provider": "fake-embed"})

    def test_schema_rejects_empty_texts(self, fake_embeddings):
        with pytest.raises(SchemaValidationError):
            call("llm.embed", {"texts": []})

    def test_schema_rejects_non_string_items(self, fake_embeddings):
        with pytest.raises(SchemaValidationError):
            call("llm.embed", {"texts": ["ok", 42]})

    def test_schema_rejects_extra_keys(self, fake_embeddings):
        with pytest.raises(SchemaValidationError):
            call("llm.embed", {"texts": ["a"], "beep": 1})


# ─── HTTP endpoint ─────────────────────────────────────────────────────


@pytest.mark.django_db
class TestEmbedEndpoint:
    def _post(self, client, body=None, **kwargs):
        body = body or {"texts": ["hello", "world"]}
        return client.post(EMBED_URL, body, format="json", **kwargs)

    def test_anonymous_rejected(self, api_client, fake_embeddings):
        assert self._post(api_client).status_code in (401, 403)
        assert fake_embeddings.calls == []

    def test_plain_user_rejected(self, user, fake_embeddings):
        from rest_framework.test import APIClient

        client = APIClient()
        client.force_authenticate(user=user)
        assert self._post(client).status_code == 403

    def test_service_key_happy_path(self, api_client, fake_embeddings):
        resp = self._post(
            api_client,
            {
                "texts": ["hello", "world"],
                "provider": "fake-embed",
                "timeout_seconds": 20,
            },
            HTTP_X_API_KEY="test-service-key",
        )
        assert resp.status_code == 200, resp.content
        data = resp.json()
        assert data["status"] == "ok"
        assert data["provider_used"] == "fake-embed"
        assert [v[0] for v in data["embeddings"]["vectors"]] == [0.0, 1.0]
        call = fake_embeddings.calls[0]
        assert call["texts"] == ["hello", "world"]
        assert call["timeout_seconds"] == 20

    def test_staff_user_accepted_and_logged(
        self, staff_client, staff_user, fake_embeddings
    ):
        resp = self._post(staff_client)
        assert resp.status_code == 200
        log = PromptLog.objects.get()
        assert log.source == "embed"
        assert log.user_id == str(staff_user.pk)

    def test_embedding_failure_is_http_200(self, api_client, fake_embeddings):
        resp = self._post(
            api_client,
            {"texts": ["x"], "provider": "fatal-embed"},
            HTTP_X_API_KEY="test-service-key",
        )
        assert resp.status_code == 200
        assert resp.json() == {"status": "failure", "reason": "auth rejected"}

    def test_empty_texts_is_400(self, api_client, fake_embeddings):
        resp = self._post(
            api_client, {"texts": []}, HTTP_X_API_KEY="test-service-key"
        )
        assert resp.status_code == 400
        assert fake_embeddings.calls == []

    def test_blank_text_entry_is_400(self, api_client, fake_embeddings):
        resp = self._post(
            api_client, {"texts": ["ok", "  "]}, HTTP_X_API_KEY="test-service-key"
        )
        assert resp.status_code == 400
        assert fake_embeddings.calls == []

    def test_missing_texts_is_400(self, api_client, fake_embeddings):
        resp = self._post(
            api_client, {"provider": "fake-embed"}, HTTP_X_API_KEY="test-service-key"
        )
        assert resp.status_code == 400
        assert fake_embeddings.calls == []

    def test_non_positive_timeout_is_400(self, api_client, fake_embeddings):
        resp = self._post(
            api_client,
            {"texts": ["a"], "timeout_seconds": 0},
            HTTP_X_API_KEY="test-service-key",
        )
        assert resp.status_code == 400
        assert fake_embeddings.calls == []
