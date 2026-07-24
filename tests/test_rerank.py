"""Rerank seam — normalized schema and the input gate, both built-in
adapters (mocked ``requests``; no network, no keys), registry merge
semantics, W011/W012/W013 checks, the ``services.rerank`` surface, the
``llm.rerank`` comm function, the HTTP endpoint and the ledger privacy
canon (counts/usage only — NEVER the query, never the document texts).

Join invariants under test everywhere: results sorted by score
descending, ``index`` = position in the INPUT documents list, documents
never round-trip in the response; an out-of-range/duplicate index or a
count above the input size is a loud fatal failure, never a misaligned
join. Error taxonomy: 429/5xx/timeouts/transport →
``RetryableRerankError``, other 4xx / bad input / missing config →
fatal ``RerankError``.
"""
import json

import pytest
import requests

from stapel_core.comm import call, function_registry
from stapel_core.comm.exceptions import SchemaValidationError

from stapel_agent import services
from stapel_agent.checks import check_rerank_providers
from stapel_agent.models import PromptLog
from stapel_agent.providers.base import ProviderError
from stapel_agent.rerank import (
    BUILTIN_RERANK_PROVIDERS,
    _reset_runtime_rerank_providers,
    register_rerank_provider,
    registered_rerank_providers,
)
from stapel_agent.rerank.base import (
    NormalizedRerank,
    RerankError,
    RerankResult,
    RetryableRerankError,
    rank_results,
    require_rerank_inputs,
)
from stapel_agent.rerank.providers.deepinfra import (
    DeepInfraRerankProvider,
    build_rerank_request,
)
from stapel_agent.rerank.providers.http_server import HttpServerRerankProvider
from stapel_agent.tests.fakes import FakeRerankProvider

RERANK_URL = "/agent/api/v1/llm/rerank"


@pytest.fixture(autouse=True)
def clean_runtime_registry():
    _reset_runtime_rerank_providers()
    yield
    _reset_runtime_rerank_providers()


# ─── base ──────────────────────────────────────────────────────────────


class TestBase:
    def test_error_taxonomy_joins_the_house_hierarchy(self):
        assert issubclass(RerankError, ProviderError)
        assert issubclass(RetryableRerankError, RerankError)
        err = RerankError("boom", provider="p", status_code=401)
        assert err.provider == "p"
        assert err.status_code == 401

    def test_require_inputs_accepts_a_batch(self):
        assert require_rerank_inputs("q", ("a", "b")) == ("q", ["a", "b"])

    @pytest.mark.parametrize(
        "query, documents, top_n, match",
        [
            ("", ["a"], None, "query is empty"),
            ("   ", ["a"], None, "query is empty"),
            (42, ["a"], None, "query must be a string"),
            ("q", [], None, "documents is empty"),
            ("q", "not-a-list", None, "list of strings"),
            ("q", ["ok", 42], None, "not a string"),
            ("q", ["ok", "   "], None, "empty"),
            ("q", ["a"], 0, "top_n"),
            ("q", ["a"], -3, "top_n"),
        ],
    )
    def test_require_inputs_rejects_fatal(self, query, documents, top_n, match):
        with pytest.raises(RerankError, match=match) as exc_info:
            require_rerank_inputs(query, documents, top_n=top_n, provider="p")
        assert not isinstance(exc_info.value, RetryableRerankError)

    def test_rank_results_sorts_by_score_desc(self):
        ranked = rank_results(
            [
                RerankResult(index=0, score=0.1),
                RerankResult(index=1, score=0.9),
                RerankResult(index=2, score=0.5),
            ],
            n_documents=3,
        )
        assert [(r.index, r.score) for r in ranked] == [
            (1, 0.9), (2, 0.5), (0, 0.1),
        ]

    def test_rank_results_top_n_applied_after_sort(self):
        ranked = rank_results(
            [
                RerankResult(index=0, score=0.1),
                RerankResult(index=1, score=0.9),
                RerankResult(index=2, score=0.5),
            ],
            n_documents=3,
            top_n=2,
        )
        assert [r.index for r in ranked] == [1, 2]

    def test_rank_results_out_of_range_index_is_fatal(self):
        for bad in (3, -1):
            with pytest.raises(RerankError, match="out of range") as exc_info:
                rank_results(
                    [RerankResult(index=bad, score=0.5)],
                    n_documents=3,
                    provider="p",
                )
            assert not isinstance(exc_info.value, RetryableRerankError)

    def test_rank_results_count_above_input_is_fatal(self):
        with pytest.raises(RerankError, match="count mismatch"):
            rank_results(
                [RerankResult(index=0, score=0.1)] * 3,
                n_documents=2,
                provider="p",
            )

    def test_rank_results_duplicate_index_is_fatal(self):
        with pytest.raises(RerankError, match="twice"):
            rank_results(
                [
                    RerankResult(index=0, score=0.1),
                    RerankResult(index=0, score=0.9),
                ],
                n_documents=3,
                provider="p",
            )

    def test_to_dict(self):
        result = NormalizedRerank(
            provider="x",
            model="m",
            results=[RerankResult(index=1, score=0.9)],
            usage={"input_tokens": 7},
        )
        data = result.to_dict()
        assert data["results"] == [{"index": 1, "score": 0.9}]
        assert data["usage"] == {"input_tokens": 7}
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
        f"stapel_agent.rerank.providers.{module}.requests.post", fake_post
    )


DEEPINFRA_BODY = {
    # Scores per pair, INPUT order — deliberately NOT descending, the
    # adapter must sort.
    "scores": [0.12, 0.98, 0.55],
    "input_tokens": 42,
    "request_id": "req-1",
}


class TestDeepInfraRerank:
    @pytest.fixture
    def configured(self, settings):
        settings.STAPEL_AGENT = {
            "RERANK_API_KEY": "di-key",
        }
        return settings

    def _run(self, monkeypatch, responses, **kwargs):
        captured = []
        mock_post(monkeypatch, "deepinfra", responses, captured)
        result = DeepInfraRerankProvider().rerank(**kwargs)
        return result, captured

    def test_builder_is_paired_arrays(self):
        # The [НЕ ВЕРИФИЦИРОВАНО live] wire shape lives in ONE pure
        # function — this is the assertion to revisit after a live check.
        body = build_rerank_request("q", ["d0", "d1", "d2"])
        assert body == {
            "queries": ["q", "q", "q"],
            "documents": ["d0", "d1", "d2"],
        }

    def test_builder_provider_options_merge_after(self):
        body = build_rerank_request("q", ["d"], {"instruction": "web search"})
        assert body == {
            "queries": ["q"],
            "documents": ["d"],
            "instruction": "web search",
        }

    def test_missing_api_key_is_fatal(self, settings):
        settings.STAPEL_AGENT = {}
        with pytest.raises(RerankError, match="RERANK_API_KEY"):
            DeepInfraRerankProvider().rerank(query="q", documents=["a"])

    def test_missing_base_url_is_fatal(self, settings):
        settings.STAPEL_AGENT = {"RERANK_API_KEY": "k", "RERANK_BASE_URL": ""}
        with pytest.raises(RerankError, match="RERANK_BASE_URL"):
            DeepInfraRerankProvider().rerank(query="q", documents=["a"])

    def test_happy_path_exact_request_shape(self, configured, monkeypatch):
        result, captured = self._run(
            monkeypatch,
            [FakeResponse(DEEPINFRA_BODY)],
            query="which doc?",
            documents=["one", "two", "three"],
            timeout_seconds=30,
        )
        req = captured[0]
        # default base URL + default model, straight from settings
        assert req["url"] == (
            "https://api.deepinfra.com/v1/inference/Qwen/Qwen3-Reranker-8B"
        )
        assert req["json"] == {
            "queries": ["which doc?", "which doc?", "which doc?"],
            "documents": ["one", "two", "three"],
        }
        assert req["headers"] == {
            "Content-Type": "application/json",
            "Authorization": "Bearer di-key",
        }
        assert req["timeout"] == 30

        assert result.provider == "deepinfra-rerank"
        assert result.model == "Qwen/Qwen3-Reranker-8B"
        # sorted by score desc, index = INPUT position
        assert [(r.index, r.score) for r in result.results] == [
            (1, 0.98), (2, 0.55), (0, 0.12),
        ]
        assert result.usage == {"input_tokens": 42}

    def test_default_timeout_from_settings(self, configured, monkeypatch):
        _, captured = self._run(
            monkeypatch, [FakeResponse(DEEPINFRA_BODY)],
            query="q", documents=["a", "b", "c"],
        )
        assert captured[0]["timeout"] == 120  # RERANK_TIMEOUT default

    def test_model_pin_and_settings_model(self, settings, monkeypatch):
        settings.STAPEL_AGENT = {
            "RERANK_API_KEY": "k",
            "RERANK_MODEL": "BAAI/bge-reranker-v2-m3",
        }
        captured = []
        mock_post(monkeypatch, "deepinfra", [FakeResponse({"scores": [0.5]})], captured)
        DeepInfraRerankProvider().rerank(query="q", documents=["a"])
        assert captured[0]["url"].endswith("/inference/BAAI/bge-reranker-v2-m3")

        class Pinned(DeepInfraRerankProvider):
            rerank_model = "Qwen/Qwen3-Reranker-0.6B"

        captured = []
        mock_post(monkeypatch, "deepinfra", [FakeResponse({"scores": [0.5]})], captured)
        result = Pinned().rerank(query="q", documents=["a"])
        assert captured[0]["url"].endswith("/inference/Qwen/Qwen3-Reranker-0.6B")
        assert result.model == "Qwen/Qwen3-Reranker-0.6B"

    def test_top_n_truncates_after_sort(self, configured, monkeypatch):
        result, _ = self._run(
            monkeypatch,
            [FakeResponse(DEEPINFRA_BODY)],
            query="q",
            documents=["one", "two", "three"],
            top_n=2,
        )
        assert [(r.index, r.score) for r in result.results] == [
            (1, 0.98), (2, 0.55),
        ]

    def test_raw_never_carries_the_scores_twice(self, configured, monkeypatch):
        result, _ = self._run(
            monkeypatch,
            [FakeResponse(DEEPINFRA_BODY)],
            query="q",
            documents=["one", "two", "three"],
        )
        assert "scores" not in result.raw
        assert result.raw["request_id"] == "req-1"

    def test_documents_never_round_trip(self, configured, monkeypatch):
        result, _ = self._run(
            monkeypatch,
            [FakeResponse(DEEPINFRA_BODY)],
            query="сколько стоит?",
            documents=["секретный документ", "another doc", "третий"],
        )
        surface = json.dumps(result.to_dict(), ensure_ascii=False)
        assert "секретный документ" not in surface
        assert "сколько стоит?" not in surface

    def test_count_mismatch_is_fatal(self, configured, monkeypatch):
        with pytest.raises(RerankError, match="count mismatch") as exc_info:
            self._run(
                monkeypatch,
                [FakeResponse(DEEPINFRA_BODY)],  # 3 scores
                query="q",
                documents=["a", "b"],
            )
        assert not isinstance(exc_info.value, RetryableRerankError)

    def test_empty_inputs_rejected_before_any_call(self, configured, monkeypatch):
        captured = []
        mock_post(monkeypatch, "deepinfra", [], captured)
        with pytest.raises(RerankError, match="query is empty"):
            DeepInfraRerankProvider().rerank(query="  ", documents=["a"])
        with pytest.raises(RerankError, match="documents is empty"):
            DeepInfraRerankProvider().rerank(query="q", documents=[])
        assert captured == []

    @pytest.mark.parametrize("status", [429, 500, 503])
    def test_transient_statuses_are_retryable(self, configured, monkeypatch, status):
        with pytest.raises(RetryableRerankError):
            self._run(
                monkeypatch,
                [FakeResponse(status_code=status, text="busy")],
                query="q",
                documents=["a"],
            )

    def test_client_error_is_fatal(self, configured, monkeypatch):
        with pytest.raises(RerankError) as exc_info:
            self._run(
                monkeypatch,
                [FakeResponse(status_code=401, text="bad key")],
                query="q",
                documents=["a"],
            )
        assert not isinstance(exc_info.value, RetryableRerankError)
        assert exc_info.value.status_code == 401

    def test_timeout_transport_and_non_json_are_retryable(
        self, configured, monkeypatch
    ):
        with pytest.raises(RetryableRerankError):
            self._run(
                monkeypatch, [requests.Timeout("slow")], query="q", documents=["a"]
            )
        with pytest.raises(RetryableRerankError):
            self._run(
                monkeypatch,
                [requests.ConnectionError("down")],
                query="q",
                documents=["a"],
            )
        with pytest.raises(RetryableRerankError, match="non-JSON"):
            self._run(
                monkeypatch, [FakeResponse(text="<html>")], query="q", documents=["a"]
            )

    def test_missing_scores_list_is_fatal(self, configured, monkeypatch):
        with pytest.raises(RerankError, match="no 'scores' list"):
            self._run(
                monkeypatch,
                [FakeResponse({"request_id": "r"})],
                query="q",
                documents=["a"],
            )

    def test_malformed_score_is_fatal(self, configured, monkeypatch):
        with pytest.raises(RerankError, match="malformed"):
            self._run(
                monkeypatch,
                [FakeResponse({"scores": ["not-a-number"]})],
                query="q",
                documents=["a"],
            )


TEI_BODY = [
    # Deliberately NOT sorted by score on the wire — the adapter must
    # re-sort; indexes are the server's join keys into the input list.
    {"index": 0, "score": 0.11},
    {"index": 2, "score": 0.87},
    {"index": 1, "score": 0.42},
]


class TestHttpServerRerank:
    @pytest.fixture
    def configured(self, settings):
        settings.STAPEL_AGENT = {
            "RERANK_HTTP_BASE_URL": "http://reranker:8080/",
        }
        return settings

    def _run(self, monkeypatch, responses, **kwargs):
        captured = []
        mock_post(monkeypatch, "http_server", responses, captured)
        result = HttpServerRerankProvider().rerank(**kwargs)
        return result, captured

    def test_missing_base_url_is_fatal(self, settings):
        settings.STAPEL_AGENT = {}
        with pytest.raises(RerankError, match="RERANK_HTTP_BASE_URL"):
            HttpServerRerankProvider().rerank(query="q", documents=["a"])

    def test_happy_path_exact_request_shape(self, configured, monkeypatch):
        result, captured = self._run(
            monkeypatch,
            [FakeResponse(TEI_BODY)],
            query="что важнее?",
            documents=["раз", "два", "три"],
        )
        req = captured[0]
        assert req["url"] == "http://reranker:8080/rerank"
        assert req["json"] == {
            "query": "что важнее?",
            "texts": ["раз", "два", "три"],
        }
        # the keyless self-host fallback — never an Authorization header
        assert req["headers"] == {"Content-Type": "application/json"}
        assert req["timeout"] == 120  # RERANK_TIMEOUT default

        assert result.provider == "rerank-http"
        assert result.model is None  # fixed server-side, no pretended attribution
        assert [(r.index, r.score) for r in result.results] == [
            (2, 0.87), (1, 0.42), (0, 0.11),
        ]
        assert result.usage is None
        assert result.raw == {}

    def test_provider_options_merged_into_body(self, configured, monkeypatch):
        _, captured = self._run(
            monkeypatch,
            [FakeResponse(TEI_BODY)],
            query="q",
            documents=["a", "b", "c"],
            provider_options={"raw_scores": True, "truncate": True},
        )
        assert captured[0]["json"] == {
            "query": "q",
            "texts": ["a", "b", "c"],
            "raw_scores": True,
            "truncate": True,
        }

    def test_top_n_truncates_after_sort(self, configured, monkeypatch):
        result, _ = self._run(
            monkeypatch,
            [FakeResponse(TEI_BODY)],
            query="q",
            documents=["a", "b", "c"],
            top_n=1,
        )
        assert [(r.index, r.score) for r in result.results] == [(2, 0.87)]

    def test_out_of_range_index_is_fatal(self, configured, monkeypatch):
        with pytest.raises(RerankError, match="out of range") as exc_info:
            self._run(
                monkeypatch,
                [FakeResponse([{"index": 5, "score": 0.9}])],
                query="q",
                documents=["a", "b"],
            )
        assert not isinstance(exc_info.value, RetryableRerankError)

    def test_count_above_input_is_fatal(self, configured, monkeypatch):
        with pytest.raises(RerankError, match="count mismatch"):
            self._run(
                monkeypatch,
                [FakeResponse(TEI_BODY)],  # 3 entries
                query="q",
                documents=["a", "b"],
            )

    def test_duplicate_index_is_fatal(self, configured, monkeypatch):
        with pytest.raises(RerankError, match="twice"):
            self._run(
                monkeypatch,
                [FakeResponse([
                    {"index": 0, "score": 0.9},
                    {"index": 0, "score": 0.1},
                ])],
                query="q",
                documents=["a", "b"],
            )

    def test_non_list_response_is_fatal(self, configured, monkeypatch):
        with pytest.raises(RerankError, match="not a list"):
            self._run(
                monkeypatch,
                [FakeResponse({"scores": [0.1]})],
                query="q",
                documents=["a"],
            )

    def test_malformed_entry_is_fatal(self, configured, monkeypatch):
        with pytest.raises(RerankError, match="malformed"):
            self._run(
                monkeypatch,
                [FakeResponse([{"score": 0.5}])],  # no index
                query="q",
                documents=["a"],
            )

    @pytest.mark.parametrize("status", [429, 502])
    def test_transient_statuses_are_retryable(self, configured, monkeypatch, status):
        with pytest.raises(RetryableRerankError):
            self._run(
                monkeypatch,
                [FakeResponse(status_code=status, text="busy")],
                query="q",
                documents=["a"],
            )

    def test_client_error_is_fatal(self, configured, monkeypatch):
        with pytest.raises(RerankError) as exc_info:
            self._run(
                monkeypatch,
                [FakeResponse(status_code=413, text="too large")],
                query="q",
                documents=["a"],
            )
        assert not isinstance(exc_info.value, RetryableRerankError)


# ─── registry + checks ─────────────────────────────────────────────────


class TestRegistry:
    def test_builtins_registered(self):
        effective = registered_rerank_providers()
        assert "deepinfra-rerank" in effective
        assert "rerank-http" in effective
        assert set(BUILTIN_RERANK_PROVIDERS) == {
            "deepinfra-rerank", "rerank-http",
        }

    def test_settings_merge_over_builtins(self, settings):
        settings.STAPEL_AGENT = {
            "RERANK_PROVIDERS": {
                "custom": "stapel_agent.tests.fakes.FakeRerankProvider",
                "rerank-http": None,  # None removes a name
            }
        }
        effective = registered_rerank_providers()
        assert "custom" in effective
        assert "rerank-http" not in effective
        assert "deepinfra-rerank" in effective  # built-ins not restated

    def test_runtime_beats_settings(self, settings):
        settings.STAPEL_AGENT = {"RERANK_PROVIDERS": {"x": "not.a.real.Path"}}
        register_rerank_provider("x", FakeRerankProvider)
        assert registered_rerank_providers()["x"] is FakeRerankProvider

    def test_register_rejects_non_provider(self):
        with pytest.raises(TypeError):
            register_rerank_provider("bad", object)


class TestSystemChecks:
    def test_clean_default_config(self):
        assert check_rerank_providers(None) == []

    def test_unimportable_entry_is_w011(self, settings):
        settings.STAPEL_AGENT = {
            "RERANK_PROVIDERS": {"broken": "no.such.module.Cls"}
        }
        assert [i.id for i in check_rerank_providers(None)] == [
            "stapel_agent.W011"
        ]

    def test_non_provider_class_is_w011(self, settings):
        settings.STAPEL_AGENT = {
            "RERANK_PROVIDERS": {
                "bad": "stapel_agent.tests.fakes.NotARerankProvider"
            }
        }
        assert [i.id for i in check_rerank_providers(None)] == [
            "stapel_agent.W011"
        ]

    def test_unknown_default_is_w012(self, settings):
        settings.STAPEL_AGENT = {"DEFAULT_RERANK_PROVIDER": "ghost"}
        issues = check_rerank_providers(None)
        assert [i.id for i in issues] == ["stapel_agent.W012"]
        assert "ghost" in issues[0].msg

    def test_http_default_without_base_url_is_w013(self, settings):
        settings.STAPEL_AGENT = {"DEFAULT_RERANK_PROVIDER": "rerank-http"}
        issues = check_rerank_providers(None)
        assert [i.id for i in issues] == ["stapel_agent.W013"]
        assert "RERANK_HTTP_BASE_URL" in issues[0].msg

    def test_http_default_with_base_url_is_clean(self, settings):
        settings.STAPEL_AGENT = {
            "DEFAULT_RERANK_PROVIDER": "rerank-http",
            "RERANK_HTTP_BASE_URL": "http://reranker:8080",
        }
        assert check_rerank_providers(None) == []

    def test_registered_with_django(self):
        from django.core.checks.registry import registry

        assert check_rerank_providers in registry.registered_checks


# ─── service ───────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestRerankService:
    def test_happy_path_envelope_sorted_join(self, fake_rerank):
        # the fake scores by document length → longest first
        result = services.rerank("query", ["aa", "aaaa", "a"])
        assert result["status"] == "ok"
        assert result["provider_used"] == "fake-rerank"
        rr = result["rerank"]
        assert rr["provider"] == "fake-rerank"
        assert rr["model"] == "fake-rerank-1"
        assert [r["index"] for r in rr["results"]] == [1, 0, 2]
        assert [r["score"] for r in rr["results"]] == [4.0, 2.0, 1.0]

    def test_top_n_forwarded_and_applied(self, fake_rerank):
        result = services.rerank("query", ["aa", "aaaa", "a"], top_n=2)
        assert [r["index"] for r in result["rerank"]["results"]] == [1, 0]
        assert fake_rerank.calls[0]["top_n"] == 2

    def test_ledger_row_is_counts_and_usage_only(self, fake_rerank):
        secret_q = "СЕКРЕТНЫЙ-запрос-客户"
        secret_doc = "confidential dossier"
        services.rerank(secret_q, [secret_doc, "second doc text"], user_id="u1")
        log = PromptLog.objects.get()
        assert log.source == "rerank"
        assert log.model == "fake-rerank"
        assert log.status == "success"
        assert log.user_id == "u1"
        # the privacy canon: counts/usage only — NEVER the query, never
        # the documents. Serialize the WHOLE row surface and scan it.
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
        assert secret_q not in row_surface
        assert secret_doc not in row_surface
        assert "second doc text" not in row_surface
        assert log.prompt == "query+docs:2"
        assert log.metadata["document_count"] == 2
        assert log.metadata["result_count"] == 2
        assert log.metadata["model"] == "fake-rerank-1"
        assert log.metadata["usage"] == {
            "input_tokens": len(secret_q) + len(secret_doc) + len("second doc text")
        }

    def test_failure_row_never_carries_texts_either(self, fake_rerank):
        result = services.rerank("classified query", ["classified doc"],
                                 provider="fatal-rerank")
        assert result == {"status": "failure", "reason": "auth rejected"}
        log = PromptLog.objects.get()
        assert log.status == "error"
        assert "classified" not in json.dumps(
            {"prompt": log.prompt, "metadata": log.metadata, "error": log.error_message}
        )
        assert log.prompt == "query+docs:1"

    def test_empty_documents_is_failure_envelope(self, fake_rerank):
        result = services.rerank("q", [])
        assert result["status"] == "failure"
        assert "empty" in result["reason"]

    def test_empty_query_is_failure_envelope(self, fake_rerank):
        result = services.rerank("  ", ["a"])
        assert result["status"] == "failure"
        assert "query is empty" in result["reason"]

    def test_unknown_provider_is_failure(self, fake_rerank):
        result = services.rerank("q", ["a"], provider="ghost")
        assert result["status"] == "failure"
        assert "Unknown rerank provider 'ghost'" in result["reason"]

    def test_options_and_timeout_forwarded(self, fake_rerank):
        services.rerank(
            "q", ["a"], timeout_seconds=15, provider_options={"truncate": True}
        )
        call = fake_rerank.calls[0]
        assert call["timeout_seconds"] == 15
        assert call["provider_options"] == {"truncate": True}


# ─── comm function ─────────────────────────────────────────────────────


@pytest.mark.django_db
class TestLlmRerankFunction:
    def test_registered(self):
        assert "llm.rerank" in function_registry.names()

    def test_happy_path(self, fake_rerank):
        result = call("llm.rerank", {"query": "q", "documents": ["aa", "aaaa"]})
        assert result["status"] == "ok"
        assert result["provider_used"] == "fake-rerank"
        assert [r["index"] for r in result["rerank"]["results"]] == [1, 0]

    def test_top_n_and_provider_forwarded(self, fake_rerank):
        result = call(
            "llm.rerank",
            {"query": "q", "documents": ["aa", "aaaa", "a"], "top_n": 1},
        )
        assert [r["index"] for r in result["rerank"]["results"]] == [1]
        result = call(
            "llm.rerank",
            {"query": "q", "documents": ["x"], "provider": "fatal-rerank"},
        )
        assert result == {"status": "failure", "reason": "auth rejected"}

    def test_schema_rejects_missing_query(self, fake_rerank):
        with pytest.raises(SchemaValidationError):
            call("llm.rerank", {"documents": ["a"]})

    def test_schema_rejects_missing_documents(self, fake_rerank):
        with pytest.raises(SchemaValidationError):
            call("llm.rerank", {"query": "q"})

    def test_schema_rejects_empty_query(self, fake_rerank):
        with pytest.raises(SchemaValidationError):
            call("llm.rerank", {"query": "", "documents": ["a"]})

    def test_schema_rejects_empty_documents(self, fake_rerank):
        with pytest.raises(SchemaValidationError):
            call("llm.rerank", {"query": "q", "documents": []})

    def test_schema_rejects_non_string_documents(self, fake_rerank):
        with pytest.raises(SchemaValidationError):
            call("llm.rerank", {"query": "q", "documents": ["ok", 42]})

    def test_schema_rejects_non_positive_top_n(self, fake_rerank):
        with pytest.raises(SchemaValidationError):
            call("llm.rerank", {"query": "q", "documents": ["a"], "top_n": 0})

    def test_schema_rejects_extra_keys(self, fake_rerank):
        with pytest.raises(SchemaValidationError):
            call("llm.rerank", {"query": "q", "documents": ["a"], "beep": 1})


# ─── HTTP endpoint ─────────────────────────────────────────────────────


@pytest.mark.django_db
class TestRerankEndpoint:
    def _post(self, client, body=None, **kwargs):
        body = body or {"query": "which?", "documents": ["aa", "aaaa"]}
        return client.post(RERANK_URL, body, format="json", **kwargs)

    def test_anonymous_rejected(self, api_client, fake_rerank):
        assert self._post(api_client).status_code in (401, 403)
        assert fake_rerank.calls == []

    def test_plain_user_rejected(self, user, fake_rerank):
        from rest_framework.test import APIClient

        client = APIClient()
        client.force_authenticate(user=user)
        assert self._post(client).status_code == 403

    def test_service_key_happy_path(self, api_client, fake_rerank):
        resp = self._post(
            api_client,
            {
                "query": "which?",
                "documents": ["aa", "aaaa", "a"],
                "top_n": 2,
                "provider": "fake-rerank",
                "timeout_seconds": 20,
            },
            HTTP_X_API_KEY="test-service-key",
        )
        assert resp.status_code == 200, resp.content
        data = resp.json()
        assert data["status"] == "ok"
        assert data["provider_used"] == "fake-rerank"
        assert [r["index"] for r in data["rerank"]["results"]] == [1, 0]
        # documents never round-trip in the response
        assert "aaaa" not in json.dumps(data)
        call = fake_rerank.calls[0]
        assert call["query"] == "which?"
        assert call["documents"] == ["aa", "aaaa", "a"]
        assert call["top_n"] == 2
        assert call["timeout_seconds"] == 20

    def test_staff_user_accepted_and_logged(
        self, staff_client, staff_user, fake_rerank
    ):
        resp = self._post(staff_client)
        assert resp.status_code == 200
        log = PromptLog.objects.get()
        assert log.source == "rerank"
        assert log.user_id == str(staff_user.pk)

    def test_rerank_failure_is_http_200(self, api_client, fake_rerank):
        resp = self._post(
            api_client,
            {"query": "q", "documents": ["x"], "provider": "fatal-rerank"},
            HTTP_X_API_KEY="test-service-key",
        )
        assert resp.status_code == 200
        assert resp.json() == {"status": "failure", "reason": "auth rejected"}

    def test_empty_documents_is_400(self, api_client, fake_rerank):
        resp = self._post(
            api_client,
            {"query": "q", "documents": []},
            HTTP_X_API_KEY="test-service-key",
        )
        assert resp.status_code == 400
        assert fake_rerank.calls == []

    def test_blank_document_entry_is_400(self, api_client, fake_rerank):
        resp = self._post(
            api_client,
            {"query": "q", "documents": ["ok", "  "]},
            HTTP_X_API_KEY="test-service-key",
        )
        assert resp.status_code == 400
        assert fake_rerank.calls == []

    def test_blank_query_is_400(self, api_client, fake_rerank):
        resp = self._post(
            api_client,
            {"query": "   ", "documents": ["a"]},
            HTTP_X_API_KEY="test-service-key",
        )
        assert resp.status_code == 400
        assert fake_rerank.calls == []

    def test_missing_query_is_400(self, api_client, fake_rerank):
        resp = self._post(
            api_client,
            {"documents": ["a"]},
            HTTP_X_API_KEY="test-service-key",
        )
        assert resp.status_code == 400
        assert fake_rerank.calls == []

    def test_non_positive_top_n_is_400(self, api_client, fake_rerank):
        resp = self._post(
            api_client,
            {"query": "q", "documents": ["a"], "top_n": 0},
            HTTP_X_API_KEY="test-service-key",
        )
        assert resp.status_code == 400
        assert fake_rerank.calls == []

    def test_non_positive_timeout_is_400(self, api_client, fake_rerank):
        resp = self._post(
            api_client,
            {"query": "q", "documents": ["a"], "timeout_seconds": 0},
            HTTP_X_API_KEY="test-service-key",
        )
        assert resp.status_code == 400
        assert fake_rerank.calls == []
