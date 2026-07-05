"""HTTP surface tests — paths, auth and the the legacy agent service response contract."""
import pytest

from stapel_agent.models import PromptLog, PromptStatus
from stapel_agent.providers.base import ProviderError, ProviderResult
from stapel_agent.services import JSON_API_SYSTEM_PROMPT

COMPLETE_URL = "/agent/api/llm/complete"
TRANSLATE_URL = "/agent/api/llm/translate"
TRANSCRIBE_URL = "/agent/api/llm/transcribe"
SUMMARIZE_URL = "/agent/api/llm/summarize"


def _complete(client, body=None, **kwargs):
    body = body or {"prompt": "give me json", "model": "small"}
    return client.post(COMPLETE_URL, body, format="json", **kwargs)


@pytest.mark.django_db
class TestAuth:
    def test_anonymous_complete_rejected(self, api_client):
        assert _complete(api_client).status_code in (401, 403)

    def test_anonymous_translate_rejected(self, api_client):
        resp = api_client.post(
            TRANSLATE_URL,
            {"from": "auto", "to": "de", "entries": {}},
            format="json",
        )
        assert resp.status_code in (401, 403)

    def test_wrong_api_key_rejected(self, api_client, fake_provider):
        resp = _complete(api_client, HTTP_X_API_KEY="wrong-key")
        assert resp.status_code in (401, 403)

    def test_service_api_key_accepted(self, api_client, fake_provider):
        resp = _complete(api_client, HTTP_X_API_KEY="test-service-key")
        assert resp.status_code == 200, resp.content

    def test_staff_user_accepted(self, staff_client, fake_provider):
        assert _complete(staff_client).status_code == 200

    def test_plain_user_rejected(self, user, fake_provider):
        from rest_framework.test import APIClient

        client = APIClient()
        client.force_authenticate(user=user)
        assert _complete(client).status_code == 403


@pytest.mark.django_db
class TestComplete:
    def test_direct_json_happy_path(self, api_client, fake_provider):
        resp = _complete(api_client, HTTP_X_API_KEY="test-service-key")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["result"] == {"answer": 42}
        assert "comment" not in data
        assert data["usage"] == {"input_tokens": 10, "output_tokens": 5}

    def test_json_block_with_comment(self, api_client, fake_provider):
        fake_provider.result = ProviderResult(
            text='Here it is:\n```json\n{"a": 1}\n```\nEnjoy.',
            input_tokens=1,
            output_tokens=2,
        )
        data = _complete(api_client, HTTP_X_API_KEY="test-service-key").json()
        assert data["status"] == "ok"
        assert data["result"] == {"a": 1}
        assert data["comment"] == "Here it is:\nEnjoy."

    def test_garbage_is_parse_failure(self, api_client, fake_provider):
        fake_provider.result = ProviderResult(text="sorry, no json here")
        resp = _complete(api_client, HTTP_X_API_KEY="test-service-key")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "failure"
        assert data["reason"] == "Failed to parse JSON from LLM response"
        assert data["comment"] == "sorry, no json here"

    def test_provider_failure_is_http_200(self, api_client, fake_provider):
        fake_provider.error = ProviderError("boom")
        resp = _complete(api_client, HTTP_X_API_KEY="test-service-key")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "failure"
        assert data["reason"] == "boom"

    def test_unknown_provider_name(self, api_client, fake_provider):
        resp = _complete(
            api_client,
            {"prompt": "x", "model": "small", "provider": "nope"},
            HTTP_X_API_KEY="test-service-key",
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "failure"
        assert "nope" in data["reason"]
        assert fake_provider.calls == []
        assert PromptLog.objects.count() == 0

    def test_unknown_model_size_is_400(self, api_client, fake_provider):
        resp = _complete(
            api_client,
            {"prompt": "x", "model": "xl"},
            HTTP_X_API_KEY="test-service-key",
        )
        assert resp.status_code == 400
        assert fake_provider.calls == []

    def test_missing_prompt_is_400(self, api_client, fake_provider):
        resp = _complete(
            api_client, {"model": "small"}, HTTP_X_API_KEY="test-service-key"
        )
        assert resp.status_code == 400

    def test_json_api_system_prompt_prepended(self, api_client, fake_provider):
        _complete(api_client, HTTP_X_API_KEY="test-service-key")
        assert fake_provider.calls[0]["system_prompt"] == JSON_API_SYSTEM_PROMPT

    def test_custom_system_prompt_wins(self, api_client, fake_provider):
        _complete(
            api_client,
            {"prompt": "x", "model": "small", "system_prompt": "You are a pirate."},
            HTTP_X_API_KEY="test-service-key",
        )
        assert fake_provider.calls[0]["system_prompt"] == "You are a pirate."

    def test_model_size_resolved_via_models_map(self, api_client, fake_provider):
        _complete(api_client, HTTP_X_API_KEY="test-service-key")
        assert fake_provider.calls[0]["model"] == "claude-haiku-4-5-20251001"

    def test_staff_user_id_logged(self, staff_client, staff_user, fake_provider):
        _complete(staff_client)
        log = PromptLog.objects.get()
        assert log.user_id == str(staff_user.pk)
        assert log.status == PromptStatus.SUCCESS


@pytest.mark.django_db
class TestTranslate:
    def _post(self, client, body):
        return client.post(
            TRANSLATE_URL, body, format="json", HTTP_X_API_KEY="test-service-key"
        )

    def test_happy_path_with_from_key(self, api_client, fake_provider):
        fake_provider.result = ProviderResult(
            text='{"greeting": "Hallo"}', input_tokens=3, output_tokens=4
        )
        resp = self._post(
            api_client,
            {"from": "auto", "to": "de", "entries": {"greeting": "Hello"}},
        )
        assert resp.status_code == 200, resp.content
        assert resp.json() == {"status": "ok", "result": {"greeting": "Hallo"}}
        # auto → the auto-detect wording of the ported system prompt
        assert (
            "the source language (auto-detect)"
            in fake_provider.calls[0]["system_prompt"]
        )
        assert '"greeting": "Hello"' in fake_provider.calls[0]["prompt"]

    def test_from_lang_key_also_accepted(self, api_client, fake_provider):
        fake_provider.result = ProviderResult(text='{"k": "Hallo"}')
        resp = self._post(
            api_client,
            {"from_lang": "en", "to": "de", "entries": {"k": "Hello"}},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        assert "from en to de" in fake_provider.calls[0]["system_prompt"]

    def test_missing_from_is_400(self, api_client, fake_provider):
        resp = self._post(api_client, {"to": "de", "entries": {"k": "v"}})
        assert resp.status_code == 400

    def test_empty_entries_short_circuits(self, api_client, fake_provider):
        resp = self._post(api_client, {"from": "auto", "to": "de", "entries": {}})
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "result": {}}
        assert fake_provider.calls == []
        assert PromptLog.objects.count() == 0

    def test_parse_failure(self, api_client, fake_provider):
        fake_provider.result = ProviderResult(text="I will not translate that.")
        resp = self._post(
            api_client, {"from": "auto", "to": "de", "entries": {"k": "Hello"}}
        )
        assert resp.status_code == 200
        assert resp.json() == {
            "status": "failure",
            "reason": "Failed to parse translation response",
        }

    def test_provider_failure(self, api_client, fake_provider):
        fake_provider.error = ProviderError("no tokens left")
        resp = self._post(
            api_client, {"from": "auto", "to": "de", "entries": {"k": "Hello"}}
        )
        assert resp.status_code == 200
        assert resp.json() == {"status": "failure", "reason": "no tokens left"}


@pytest.mark.django_db
class TestTranscribeEndpoint:
    def _post(self, client, body=None, **kwargs):
        body = body or {"audio_url": "https://minio.test/rec.mp3"}
        return client.post(TRANSCRIBE_URL, body, format="json", **kwargs)

    def test_anonymous_rejected(self, api_client, fake_stt):
        assert self._post(api_client).status_code in (401, 403)
        assert fake_stt.calls == []

    def test_wrong_api_key_rejected(self, api_client, fake_stt):
        resp = self._post(api_client, HTTP_X_API_KEY="wrong-key")
        assert resp.status_code in (401, 403)

    def test_plain_user_rejected(self, user, fake_stt):
        from rest_framework.test import APIClient

        client = APIClient()
        client.force_authenticate(user=user)
        assert self._post(client).status_code == 403

    def test_service_key_happy_path(self, api_client, fake_stt):
        resp = self._post(
            api_client,
            {
                "audio_url": "https://minio.test/rec.mp3",
                "language": "en",
                "diarization": True,
                "provider": "fake-stt",
                "timeout_seconds": 120,
            },
            HTTP_X_API_KEY="test-service-key",
        )
        assert resp.status_code == 200, resp.content
        data = resp.json()
        assert data["status"] == "ok"
        assert data["provider_used"] == "fake-stt"
        assert data["fallback_used"] is False
        assert data["transcript"]["utterances"][0]["text"] == "hello world"
        call = fake_stt.calls[0]
        assert call["audio"].url == "https://minio.test/rec.mp3"
        assert call["language"] == "en"
        assert call["diarization"] is True
        assert call["timeout_seconds"] == 120

    def test_staff_user_accepted_and_logged(self, staff_client, staff_user, fake_stt):
        resp = self._post(staff_client)
        assert resp.status_code == 200
        log = PromptLog.objects.get()
        assert log.source == "transcribe"
        assert log.user_id == str(staff_user.pk)

    def test_stt_failure_is_http_200(self, api_client, fake_stt):
        resp = self._post(
            api_client,
            {"audio_url": "https://x/a.mp3", "provider": "fatal-stt"},
            HTTP_X_API_KEY="test-service-key",
        )
        assert resp.status_code == 200
        assert resp.json() == {
            "status": "failure",
            "reason": "audio is not decodable",
        }

    def test_missing_audio_url_is_400(self, api_client, fake_stt):
        resp = self._post(
            api_client, {"language": "en"}, HTTP_X_API_KEY="test-service-key"
        )
        assert resp.status_code == 400
        assert fake_stt.calls == []

    def test_non_positive_timeout_is_400_not_500(self, api_client, fake_stt):
        # 0 and negatives are unexpressible to `requests` — reject at the
        # boundary so it stays a 400, never a 500 down in urllib3.
        for bad in (0, -1):
            resp = self._post(
                api_client,
                {"audio_url": "https://x/a.mp3", "timeout_seconds": bad},
                HTTP_X_API_KEY="test-service-key",
            )
            assert resp.status_code == 400, bad
        assert fake_stt.calls == []


@pytest.mark.django_db
class TestSummarizeEndpoint:
    def _post(self, client, body=None, **kwargs):
        body = body or {"text": "meeting notes to summarize"}
        return client.post(SUMMARIZE_URL, body, format="json", **kwargs)

    def test_anonymous_rejected(self, api_client, fake_provider):
        assert self._post(api_client).status_code in (401, 403)
        assert fake_provider.calls == []

    def test_wrong_api_key_rejected(self, api_client, fake_provider):
        resp = self._post(api_client, HTTP_X_API_KEY="wrong-key")
        assert resp.status_code in (401, 403)

    def test_service_key_happy_path_drops_none_keys(self, api_client, fake_provider):
        fake_provider.result = ProviderResult(
            text="## Summary", input_tokens=7, output_tokens=3
        )
        resp = self._post(api_client, HTTP_X_API_KEY="test-service-key")
        assert resp.status_code == 200, resp.content
        # None keys (reason) are dropped after serialization — absent on
        # the wire, per the iron contract.
        assert resp.json() == {
            "status": "ok",
            "summary": "## Summary",
            "usage": {"input_tokens": 7, "output_tokens": 3},
        }

    def test_staff_user_accepted_and_logged(
        self, staff_client, staff_user, fake_provider
    ):
        resp = self._post(staff_client)
        assert resp.status_code == 200
        log = PromptLog.objects.get()
        assert log.source == "summarize"
        assert log.user_id == str(staff_user.pk)

    def test_transcript_input(self, api_client, fake_provider):
        transcript = {
            "provider": "fake-stt",
            "language": "en",
            "duration_seconds": 2.0,
            "utterances": [
                {"text": "hello world", "start": 0.0, "end": 2.0, "speaker": "A"}
            ],
        }
        resp = self._post(
            api_client,
            {"transcript": transcript, "model": "small", "language": "de"},
            HTTP_X_API_KEY="test-service-key",
        )
        assert resp.status_code == 200, resp.content
        assert resp.json()["status"] == "ok"
        assert "[00:00] A: hello world" in fake_provider.calls[0]["prompt"]

    def test_both_text_and_transcript_is_400(self, api_client, fake_provider):
        resp = self._post(
            api_client,
            {"text": "t", "transcript": {"provider": "x"}},
            HTTP_X_API_KEY="test-service-key",
        )
        assert resp.status_code == 400
        assert fake_provider.calls == []

    def test_neither_input_is_400(self, api_client, fake_provider):
        resp = self._post(
            api_client, {"language": "en"}, HTTP_X_API_KEY="test-service-key"
        )
        assert resp.status_code == 400

    def test_unknown_model_size_is_400(self, api_client, fake_provider):
        resp = self._post(
            api_client,
            {"text": "t", "model": "xl"},
            HTTP_X_API_KEY="test-service-key",
        )
        assert resp.status_code == 400
        assert fake_provider.calls == []

    def test_llm_failure_is_http_200(self, api_client, fake_provider):
        fake_provider.error = ProviderError("llm down")
        resp = self._post(api_client, HTTP_X_API_KEY="test-service-key")
        assert resp.status_code == 200
        assert resp.json() == {"status": "failure", "reason": "llm down"}
