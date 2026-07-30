"""The catalog against the wire: every declared param must be a sent param.

``ModelConfig.provider_params`` says "what the adapter actually sends". That
sentence is a promise no reader can check and no reviewer will re-derive, so
it rots: the shipped 0.7.1 catalog advertised Deepgram ``paragraphs`` /
``filler_words``, xAI ``filler_words`` and Soniox
``enable_language_identification`` that no adapter ever put on the wire — and
the xAI one mattered, because the provider's default for it DELETES um/uh
from the transcript AND from ``words[]``. The catalog promised a verbatim
transcript while the adapter asked for an edited one.

This module makes the sentence executable. For every shipped config it drives
the config's OWN adapter — pinned through ``adapter_kwargs``, resolved through
the same provider registry a caller uses — against a stub transport, collects
every parameter that reached a request body/query, and compares that with
``provider_params`` in BOTH directions:

- declared but not sent  → the catalog is lying to whoever reads it;
- sent but not declared  → the catalog is incomplete (only genuinely
  per-run keys are exempt, see ``RUN_SCOPED``).

A NEW catalog entry cannot pass silently: its provider needs a stub in
``_PROVIDER_RESPONSES`` (missing → ``test_every_config_can_be_driven`` fails)
and every key it declares has to appear on the wire.

WHAT THIS DOES NOT CHECK
------------------------
Only that we ASK for what we advertise. It says nothing about what the
provider does with the request: that ``filler_words=true`` really preserves
fillers, that ``diarize_model=latest`` really selects the v2 diarizer, that a
parameter still exists in this month's API. A provider can rename, ignore or
silently invert any of these and this file stays green — only a live call
against the provider can catch that. Nor does it check that the config's
prices, warnings or model ids are true.
"""
import json

import pytest
import requests
from django.utils.module_loading import import_string

from stapel_agent.stt import registered_stt_providers
from stapel_agent.stt.base import AudioRef
from stapel_agent.stt.model_configs import (
    BUILTIN_STT_MODEL_CONFIGS,
    ModelConfig,
    get_config,
    register_stt_model_config,
    registered_stt_model_configs,
)
from stapel_agent.stt.model_configs import _reset_runtime_stt_model_configs

#: Keys a config must NOT have to declare: they carry the per-RUN inputs
#: (which audio, which language, which bias terms), not the profile. A
#: config that DOES declare one (``deepgram_nova3_multi`` declares
#: ``language: "multi"``) is still checked for it — the exemption only
#: silences the "sent but not declared" direction.
RUN_SCOPED = frozenset({
    "audio_url", "file_id",                       # where the audio is
    "language", "language_code", "language_hints",  # what was routed
    "languages", "code_switching", "language_detection",
    "keyterm", "keyterms", "keyterms_prompt",     # what was biased
})

AUDIO_URL = "https://audio.example/meeting.wav"

ALL_KEYS = {
    "ELEVENLABS_API_KEY": "el-test",
    "ASSEMBLYAI_API_KEY": "aai-test",
    "DEEPGRAM_API_KEY": "dg-test",
    "GLADIA_API_KEY": "gl-test",
    "SONIOX_API_KEY": "sx-test",
    "SPEECHMATICS_API_KEY": "sm-test",
    "XAI_API_KEY": "xai-test",
}


class FakeResponse:
    """Enough of a ``requests.Response`` for every adapter path here."""

    def __init__(self, payload=None, content=b""):
        self._payload = payload
        self.status_code = 200
        self.content = content
        self.text = json.dumps(payload or {})

    def json(self):
        return self._payload if self._payload is not None else {}

    def raise_for_status(self):
        return None


# ── Per-provider stub responses ───────────────────────────────────────
#
# The ONLY per-provider knowledge in this file: what a happy flow returns,
# keyed by (method, url). Payloads are minimal — the mapping is tested
# elsewhere; here they exist only to let ``transcribe`` reach its end.


def _elevenlabs(method, url):
    return {"language_code": "en", "text": "hi", "words": []}


def _assemblyai(method, url):
    if method == "POST":
        return {"id": "aai_1", "status": "queued"}
    return {"status": "completed", "text": "hi", "words": [], "utterances": []}


def _deepgram(method, url):
    return {"metadata": {"duration": 1.0},
            "results": {"channels": [{"alternatives": [{"words": []}]}],
                        "utterances": []}}


def _gladia(method, url):
    if url.endswith("/v2/upload"):
        return {"audio_url": "https://gladia.cdn/a"}
    if method == "POST":
        return {"id": "job_1"}
    return {"status": "done",
            "result": {"transcription": {"utterances": []},
                       "metadata": {"audio_duration": 1.0}}}


def _soniox(method, url):
    if url.endswith("/v1/files"):
        return {"id": "file_1"}
    if url.endswith("/v1/transcriptions"):
        return {"id": "tr_1"}
    if url.endswith("/transcript"):
        return {"id": "tr_1", "text": "hi", "tokens": []}
    return {"status": "completed", "audio_duration_ms": 1000}


def _speechmatics(method, url):
    if method == "POST":
        return {"id": "sm_1"}
    if url.endswith("/transcript"):
        return {"job": {"duration": 1}, "results": []}
    return {"job": {"status": "done"}}


def _xai_stt(method, url):
    return {"text": "hi", "language": "", "duration": 1.0, "words": []}


_PROVIDER_RESPONSES = {
    "elevenlabs": _elevenlabs,
    "assemblyai": _assemblyai,
    "deepgram": _deepgram,
    "gladia": _gladia,
    "soniox": _soniox,
    "speechmatics": _speechmatics,
    "xai-stt": _xai_stt,
}


# ── The harness ───────────────────────────────────────────────────────


def _install(monkeypatch, respond):
    """Patch the ``requests`` verbs every adapter reaches for; capture calls.

    Patched on the ``requests`` module itself, not per adapter module: the
    adapters all hold the same module object, and so does ``base._download``
    (the audio fetch). One patch therefore covers every request a run makes —
    including ones a per-module patch would miss.
    """
    calls = []

    def _handler(fixed_method):
        def _fn(*args, **kwargs):
            if fixed_method is None:            # requests.request(method, url)
                method, url = str(args[0]).upper(), args[1]
            else:
                method = fixed_method
                url = args[0] if args else kwargs.get("url")
            calls.append({"method": method, "url": url, **kwargs})
            if url == AUDIO_URL:
                return FakeResponse(content=b"WAVDATA")
            return FakeResponse(payload=respond(method, url))
        return _fn

    monkeypatch.setattr(requests, "post", _handler("POST"))
    monkeypatch.setattr(requests, "get", _handler("GET"))
    monkeypatch.setattr(requests, "delete", _handler("DELETE"))
    monkeypatch.setattr(requests, "request", _handler(None))
    monkeypatch.setattr("time.sleep", lambda s: None)
    return calls


def _norm(value):
    """Wire booleans are the strings "true"/"false" for some providers and
    real bools for others — one shape for comparison."""
    if value == "true":
        return True
    if value == "false":
        return False
    return value


def _absorb(sent: dict, value) -> None:
    """Flatten one request container into ``key → value``.

    Nested config objects are flattened into the same namespace: a param is
    a param whether it rides at the top level (Deepgram query), inside
    ``language_config`` (Gladia) or inside a JSON STRING under a multipart
    field (Speechmatics packs its whole ``transcription_config`` that way).
    """
    if not isinstance(value, dict):
        return
    for key, item in value.items():
        if isinstance(item, str):
            try:
                parsed = json.loads(item)
            except (TypeError, ValueError):
                parsed = None
            if isinstance(parsed, dict):
                _absorb(sent, parsed)
                continue
        if isinstance(item, dict):
            _absorb(sent, item)
            continue
        sent[key] = _norm(item)


def _sent_params(calls: list[dict]) -> dict:
    sent: dict = {}
    for call in calls:
        for container in ("params", "json", "data"):
            _absorb(sent, call.get(container))
    return sent


def drive(config: ModelConfig, monkeypatch, settings) -> dict:
    """Run ``config``'s adapter over a stub transport; return what it sent.

    The adapter is built the way a host would register this config: the
    class resolved from the provider registry under ``provider_id``, with
    ``adapter_kwargs`` applied as the class attributes that pin it
    (``SttProvider.speech_model``). Nothing about the request is hand-fed —
    the params come out of the adapter's own code path.
    """
    settings.STAPEL_AGENT = dict(ALL_KEYS)
    cls = registered_stt_providers()[config.provider_id]
    cls = import_string(cls) if isinstance(cls, str) else cls
    if config.adapter_kwargs:
        cls = type(f"Pinned{cls.__name__}", (cls,), dict(config.adapter_kwargs))
    calls = _install(monkeypatch, _PROVIDER_RESPONSES[config.provider_id])
    cls().transcribe(
        audio=AudioRef(url=AUDIO_URL, mime="audio/wav"),
        language=config.default_language,
        diarization=True,
    )
    return _sent_params(calls)


# ── The gate ──────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_registry():
    _reset_runtime_stt_model_configs()
    yield
    _reset_runtime_stt_model_configs()


class TestRegistryConformance:
    def test_every_config_can_be_driven(self):
        # The gate on NEW entries: a config whose provider has no stub here
        # cannot be checked at all, so it fails rather than passes quietly.
        missing = sorted({
            c.provider_id for c in registered_stt_model_configs().values()
            if c.provider_id not in _PROVIDER_RESPONSES
        })
        assert not missing, (
            f"STT model configs name provider(s) {missing} with no stub in "
            "_PROVIDER_RESPONSES — add one so their wire form is checked"
        )

    @pytest.mark.parametrize("config_id", sorted(BUILTIN_STT_MODEL_CONFIGS))
    def test_declared_params_reach_the_wire(self, config_id, monkeypatch, settings):
        config = get_config(config_id)
        sent = drive(config, monkeypatch, settings)
        wrong = {
            key: (declared, sent.get(key, "<not sent>"))
            for key, declared in config.provider_params.items()
            if _norm(declared) != sent.get(key, "<not sent>")
        }
        assert not wrong, (
            f"{config_id}: provider_params claims parameters the adapter does "
            f"not send with these values {{key: (declared, sent)}}: {wrong}"
        )

    @pytest.mark.parametrize("config_id", sorted(BUILTIN_STT_MODEL_CONFIGS))
    def test_sent_params_are_declared(self, config_id, monkeypatch, settings):
        config = get_config(config_id)
        sent = drive(config, monkeypatch, settings)
        undeclared = sorted(
            key for key in sent
            if key not in config.provider_params and key not in RUN_SCOPED
        )
        assert not undeclared, (
            f"{config_id}: the adapter sends {undeclared}, which "
            "provider_params does not document — add them there (or to "
            "RUN_SCOPED if they carry per-run input, not the profile)"
        )

    def test_a_declared_param_that_is_never_sent_fails(self, monkeypatch, settings):
        # The gate's own red: this is exactly the shape of the 0.7.1 defect —
        # a plausible knob written into the catalog and into no request.
        config = ModelConfig(
            model_config_id="conformance_probe",
            display_name="probe",
            provider_id="deepgram",
            model_id="nova-3",
            default_language="en",
            provider_params={"model": "nova-3", "filler_words": True,
                             "profanity_filter": True},
        )
        register_stt_model_config(config)
        sent = drive(get_config("conformance_probe"), monkeypatch, settings)
        assert sent["filler_words"] is True
        assert "profanity_filter" not in sent

    def test_an_undeclared_param_on_the_wire_is_visible(self, monkeypatch, settings):
        # The other direction: an adapter param no config documents.
        config = ModelConfig(
            model_config_id="conformance_probe_thin",
            display_name="probe",
            provider_id="deepgram",
            model_id="nova-3",
            default_language="en",
            provider_params={"model": "nova-3"},
        )
        register_stt_model_config(config)
        sent = drive(get_config("conformance_probe_thin"), monkeypatch, settings)
        undeclared = {k for k in sent
                      if k not in config.provider_params and k not in RUN_SCOPED}
        assert undeclared == {"smart_format", "utterances", "diarize_model",
                              "filler_words"}
