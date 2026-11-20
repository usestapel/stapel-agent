"""The JSON bodies the committed schema declares are the bodies the views send.

An ``@extend_schema`` annotation is a claim the generator cannot check. This
gate performs each JSON operation of ``docs/schema.v2.json`` against the fake
providers and validates the body against the schema it promised — on the
success path and, for the two typed bodies, on the HTTP-200 failure path the
house contract answers with. A JSON operation with no recipe fails rather
than being skipped.
"""
import copy
import json
from pathlib import Path

import jsonschema
import pytest

from stapel_agent.providers.base import ProviderError, ProviderResult

pytestmark = pytest.mark.django_db

SCHEMA = json.loads(
    (Path(__file__).resolve().parent.parent / "docs" / "schema.v2.json").read_text()
)
V1 = "/agent/api/v1/llm"
KEY = {"HTTP_X_API_KEY": "test-service-key"}


@pytest.fixture
def fakes(fake_provider, fake_stt, fake_diarization, fake_embeddings, fake_rerank, fake_images):
    fake_provider.result = ProviderResult(text='{"greeting": "Hallo"}', input_tokens=3, output_tokens=4)
    return fake_provider


def _post(client, path, body):
    return client.post(f"{V1}/{path}", body, format="json", **KEY)


#: ``path -> request body`` of the success path.
BODIES = {
    f"{V1}/complete": ("complete", {"prompt": "give me json", "model": "small"}),
    f"{V1}/translate": ("translate", {"from": "auto", "to": "de", "entries": {"greeting": "Hello"}}),
    f"{V1}/transcribe": ("transcribe", {"audio_url": "https://minio.test/rec.mp3"}),
    f"{V1}/diarize": ("diarize", {"audio_url": "https://minio.test/rec.mp3"}),
    f"{V1}/embed": ("embed", {"texts": ["hello", "world"]}),
    f"{V1}/rerank": ("rerank", {"query": "which?", "documents": ["aa", "aaaa"]}),
    f"{V1}/summarize": ("summarize", {"text": "meeting notes to summarize"}),
    f"{V1}/generate-image": ("generate-image", {"prompt": "a cat"}),
}


def _json_schema(node):
    if isinstance(node, list):
        return [_json_schema(item) for item in node]
    if not isinstance(node, dict):
        return node
    rebuilt = {k: _json_schema(v) for k, v in node.items() if k != "nullable"}
    if node.get("nullable"):
        return {"anyOf": [rebuilt, {"type": "null"}]}
    return rebuilt


def _operations():
    for path, item in SCHEMA["paths"].items():
        for method, op in item.items():
            if not isinstance(op, dict):
                continue
            for code, response in op.get("responses", {}).items():
                body = response.get("content", {}).get("application/json", {}).get("schema")
                if code.startswith("2") and body is not None:
                    yield method.upper(), path, int(code), body


OPERATIONS = list(_operations())


def _errors(body_schema, body):
    root = copy.deepcopy(body_schema)
    root["components"] = copy.deepcopy(SCHEMA["components"])
    validator = jsonschema.Draft202012Validator(_json_schema(root))
    return sorted(validator.iter_errors(body), key=lambda e: list(e.path))


def test_the_gate_sees_its_subject():
    assert {p for m, p, _c, _b in OPERATIONS if m == "POST"} == set(BODIES)
    assert len(OPERATIONS) == len(BODIES)


@pytest.mark.parametrize(
    "method,path,code,body_schema", OPERATIONS, ids=[f"{m} {p}" for m, p, _c, _b in OPERATIONS]
)
def test_the_wire_matches_the_declared_response(api_client, fakes, method, path, code, body_schema):
    route, request_body = BODIES[path]
    response = _post(api_client, route, request_body)
    assert response.status_code == code, response.content
    body = response.json()
    assert body.get("status") == "ok", body
    errors = _errors(body_schema, body)
    assert not errors, "\n".join(f"at {list(e.path)}: {e.message}" for e in errors[:10])


@pytest.mark.parametrize("route", ["translate", "summarize"])
def test_the_failure_body_matches_the_declared_response(api_client, fakes, route):
    """LLM failures answer HTTP 200 with ``status: "failure"`` — the typed
    bodies must describe that shape too, not only the success one."""
    fakes.error = ProviderError("boom")
    path = f"{V1}/{route}"
    response = _post(api_client, route, BODIES[path][1])
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["status"] == "failure", body
    body_schema = SCHEMA["paths"][path]["post"]["responses"]["200"]["content"]["application/json"]["schema"]
    errors = _errors(body_schema, body)
    assert not errors, "\n".join(f"at {list(e.path)}: {e.message}" for e in errors[:10])


def test_inversion_a_wrong_declaration_is_caught(api_client, fakes):
    """A failure body checked against a declaration that requires the success
    field must be refused — the validator sees what it is given."""
    fakes.error = ProviderError("boom")
    body = _post(api_client, "translate", BODIES[f"{V1}/translate"][1]).json()
    wrong = copy.deepcopy(SCHEMA["components"]["schemas"]["TranslateResponse"])
    wrong["required"] = ["status", "result"]
    root = {"$ref": "#/components/schemas/Wrong", "components": {"schemas": {"Wrong": wrong}}}
    assert list(jsonschema.Draft202012Validator(_json_schema(root)).iter_errors(body))
