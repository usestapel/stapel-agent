"""Recording fake provider, wired via the STAPEL_AGENT PROVIDERS override.

``services.get_provider`` instantiates a fresh object per request
(dotted-path + import_string), so calls and canned results live on the
class — reset via ``FakeProvider.reset()`` (the ``fake_provider`` fixture
does this automatically).
"""
from __future__ import annotations

import base64

from stapel_agent.cache import CachePolicy
from stapel_agent.diarization.base import (
    DiarizationError,
    DiarizationProvider,
    DiarTurn,
    NormalizedDiarization,
)
from stapel_agent.embeddings.base import (
    EmbeddingError,
    EmbeddingProvider,
    NormalizedEmbeddings,
    require_texts,
)
from stapel_agent.images.base import GeneratedImage, ImageGenProvider
from stapel_agent.rerank.base import (
    NormalizedRerank,
    RerankError,
    RerankProvider,
    RerankResult,
    rank_results,
    require_rerank_inputs,
)
from stapel_agent.providers.base import LlmProvider, ProviderResult, status_error
from stapel_agent.stt.base import (
    NormalizedTranscript,
    NormalizedUtterance,
    ProviderQuota,
    RetryableTranscriptionError,
    SttProvider,
    TranscriptionError,
)


class FakeProvider(LlmProvider):
    name = "fake"
    supports_images = True  # vision tests route ImageRefs through it
    supports_max_tokens = True  # per-call cap tests route max_tokens through it
    supports_schema = True  # structured-output tests route a schema through it

    calls: list[dict] = []
    result = ProviderResult(text='{"answer": 42}')
    error: Exception | None = None
    #: Optional per-call hook, for the tests where the SECOND answer has to
    #: differ from the first (a rejected answer and its revision). Takes the
    #: same kwargs as ``complete`` and returns a ``ProviderResult``; when it
    #: is None the flat ``result``/``error`` pair answers every call.
    responder = None

    @classmethod
    def reset(cls):
        cls.calls = []
        cls.result = ProviderResult(
            text='{"answer": 42}',
            input_tokens=10,
            output_tokens=5,
            thinking_tokens=2,
            cache_read_tokens=1,
            cache_write_tokens=3,
        )
        cls.error = None
        cls.responder = None

    def complete(self, *, prompt, model, system_prompt=None, images=None,
                 max_tokens=None, schema=None):
        cls = type(self)
        cls.calls.append(
            {
                "prompt": prompt,
                "model": model,
                "system_prompt": system_prompt,
                "images": images,
                "max_tokens": max_tokens,
                "schema": schema,
            }
        )
        if cls.error is not None:
            raise cls.error
        if cls.responder is not None:
            return cls.responder(
                prompt=prompt,
                model=model,
                system_prompt=system_prompt,
                images=images,
                max_tokens=max_tokens,
                schema=schema,
            )
        return cls.result


class CustomProvider(FakeProvider):
    """A second provider class so tests can tell registrations apart."""

    name = "custom"


class OutOfCreditsProvider(FakeProvider):
    """A provider whose team has spent its allowance.

    The body is the one a client's endpoint actually returned on
    2026-09-20, because the classification hangs on the words in it.
    """

    name = "out-of-credits"
    body = (
        '{"code": "The model is not available for your team. Your team has '
        'either used all available credits or reached its monthly spending '
        'limit."}'
    )

    def complete(self, *, prompt, model, system_prompt=None, images=None,
                 max_tokens=None, schema=None):
        cls = type(self)
        cls.calls.append({"prompt": prompt, "model": model})
        raise status_error(
            403, cls.body, provider=cls.name, label="OpenAI-compatible endpoint"
        )


class BadRequestProvider(FakeProvider):
    """A provider that refuses the REQUEST — the one thing a chain must
    not walk, because the next provider refuses it identically."""

    name = "bad-request"

    def complete(self, *, prompt, model, system_prompt=None, images=None,
                 max_tokens=None, schema=None):
        cls = type(self)
        cls.calls.append({"prompt": prompt, "model": model})
        raise status_error(
            422,
            '{"error": "prompt contains an unsupported content block"}',
            provider=cls.name,
        )


class UnreachableProvider(FakeProvider):
    """The endpoint is down — retryable, and the chain walks on."""

    name = "unreachable"

    def complete(self, *, prompt, model, system_prompt=None, images=None,
                 max_tokens=None, schema=None):
        cls = type(self)
        cls.calls.append({"prompt": prompt, "model": model})
        raise status_error(503, "upstream connect error", provider=cls.name)


class SecondaryProvider(FakeProvider):
    """The fallback that answers when the primary cannot."""

    name = "secondary"

    @classmethod
    def reset(cls):
        super().reset()
        cls.result = ProviderResult(
            text="the fallback's answer", input_tokens=7, output_tokens=3
        )


class NoVisionProvider(FakeProvider):
    """Text-only backend with the pre-vision three-argument signature —
    proves the service never forwards images (or a max_tokens cap, or a
    schema) to a provider that can't take them (and that old signatures
    stay compatible)."""

    name = "no-vision"
    supports_images = False
    supports_max_tokens = False
    supports_schema = False

    def complete(self, *, prompt, model, system_prompt=None):
        return super().complete(prompt=prompt, model=model, system_prompt=system_prompt)


class NotAProvider:
    """Deliberately not an LlmProvider subclass — for the W002 check."""


class FakeImageProvider(ImageGenProvider):
    """Recording image-generation fake — same class-level-state pattern."""

    name = "fake-images"
    supported_sizes = None

    calls: list[dict] = []
    result: list[GeneratedImage] = []
    error: Exception | None = None

    @classmethod
    def reset(cls):
        cls.calls = []
        cls.result = [
            GeneratedImage(
                mime="image/png",
                data_b64=base64.b64encode(b"fake-png-bytes").decode(),
            )
        ]
        cls.error = None

    def generate(self, *, prompt, size="1024x1024", n=1, timeout_seconds=None):
        cls = type(self)
        cls.calls.append(
            {"prompt": prompt, "size": size, "n": n, "timeout_seconds": timeout_seconds}
        )
        if cls.error is not None:
            raise cls.error
        return cls.result


class SquareOnlyImageProvider(FakeImageProvider):
    """Declares supported_sizes — for the size-validation path."""

    name = "square-images"
    supported_sizes = frozenset({"1024x1024"})


class NotAnImageProvider:
    """Deliberately not an ImageGenProvider subclass — for the W005 check."""


class RecordingCachePolicy(CachePolicy):
    """Dict-backed CachePolicy for the CACHE_POLICY seam tests.

    Class-level state for the same reason as FakeProvider: the policy is
    instantiated per call through the dotted-path setting.
    """

    entries: dict = {}
    lookups: list = []
    stores: list = []
    cache_all = True

    @classmethod
    def reset(cls):
        cls.entries = {}
        cls.lookups = []
        cls.stores = []
        cls.cache_all = True

    def should_cache(self, source):
        return type(self).cache_all

    def lookup(self, prompt, system_prompt, source, *, provider, model,
               model_size, user_id=None):
        cls = type(self)
        cls.lookups.append(
            (prompt, system_prompt, source, provider, model, model_size, user_id)
        )
        return cls.entries.get((user_id, prompt, system_prompt, source))

    def store(self, prompt, system_prompt, source, response, *, provider, model,
              model_size, user_id=None):
        cls = type(self)
        cls.stores.append(
            (prompt, system_prompt, source, response, provider, model, model_size,
             user_id)
        )
        cls.entries[(user_id, prompt, system_prompt, source)] = response


class FakeSttProvider(SttProvider):
    """Recording STT fake — same class-level-state pattern as FakeProvider
    (``get_stt_provider`` instantiates a fresh object per request)."""

    name = "fake-stt"
    supports_diarization = True

    calls: list[dict] = []
    result: NormalizedTranscript | None = None
    error: Exception | None = None

    @classmethod
    def reset(cls):
        cls.calls = []
        cls.result = NormalizedTranscript(
            provider=cls.name,
            language="en",
            duration_seconds=2.0,
            utterances=[
                NormalizedUtterance(
                    text="hello world", start=0.0, end=2.0, speaker="A"
                )
            ],
            speakers_detected=["A"],
        )
        cls.error = None

    def transcribe(
        self,
        *,
        audio,
        language=None,
        diarization=False,
        timeout_seconds=None,
        keyterms=None,
        provider_options=None,
    ):
        cls = type(self)
        cls.calls.append(
            {
                "audio": audio,
                "language": language,
                "diarization": diarization,
                "timeout_seconds": timeout_seconds,
                "keyterms": keyterms,
                "provider_options": provider_options,
            }
        )
        if cls.error is not None:
            raise cls.error
        if keyterms and not cls.supports_keyterms:
            # The house contract for non-supporting adapters: report the
            # request as not applied instead of failing.
            from stapel_agent.stt.base import unsupported_biasing

            cls.result.biasing = unsupported_biasing(keyterms)
        return cls.result


class SecondSttProvider(FakeSttProvider):
    """A second STT class so fallback tests can tell providers apart."""

    name = "fake-stt-2"


class PinnedSttProvider(FakeSttProvider):
    """A registration with a pinned ``speech_model`` and a settings-backed
    default — exercises the per-registration model pin (G6) and its
    surfacing through ``llm.stt_catalog`` (G7)."""

    name = "pinned-stt"
    supported_languages = frozenset({"en", "ru"})
    cost_per_hour = 0.10
    speech_model = "pinned-model-x"

    def default_speech_model(self):
        return "configured-default"


class RetryableSttProvider(FakeSttProvider):
    """Always fails transiently — the service must walk the chain."""

    name = "retry-stt"

    @classmethod
    def reset(cls):
        super().reset()
        cls.error = RetryableTranscriptionError(
            "stt rate limited", provider=cls.name, status_code=429
        )


class QuotaSttProvider(FakeSttProvider):
    """Always fails on the ACCOUNT (out of credits) — the condition that
    used to be read as bad input and stopped the chain dead."""

    name = "quota-stt"

    @classmethod
    def reset(cls):
        super().reset()
        cls.error = RetryableTranscriptionError(
            "stt account out of credits",
            provider=cls.name,
            status_code=401,
            reason="quota",
        )


class FatalSttProvider(FakeSttProvider):
    """Always fails permanently — the service must NOT fall back."""

    name = "fatal-stt"

    @classmethod
    def reset(cls):
        super().reset()
        cls.error = TranscriptionError(
            "audio is not decodable",
            provider=cls.name,
            status_code=400,
            reason="media",
        )


class LoopedFixtureSttProvider(FakeSttProvider):
    """One distinct answer per CALL, so a collapsed call is visible.

    The production incident this serves (see
    ``tests/test_stt_chunk_integrity.py``) came from audio whose chunks
    were byte-identical — a looped fixture. A fake whose answer is the
    same every time cannot tell "the provider was called twice" from "the
    second answer was served from the first call", which is exactly the
    distinction under test. So each call returns a transcript stamped
    with its own ordinal.
    """

    name = "looped-stt"

    #: Seconds of audio each call is told it transcribed.
    chunk_seconds = 74.31

    @classmethod
    def reset(cls):
        super().reset()
        cls.result = None

    def transcribe(self, *, audio, language=None, diarization=False,
                   timeout_seconds=None, keyterms=None, provider_options=None):
        cls = type(self)
        index = len(cls.calls)
        cls.calls.append({"audio": audio, "language": language,
                          "diarization": diarization,
                          "timeout_seconds": timeout_seconds,
                          "keyterms": keyterms,
                          "provider_options": provider_options})
        if cls.error is not None:
            raise cls.error
        # Local times, as a real chunked call returns them: every chunk
        # starts at zero and the caller re-bases them onto the timeline.
        return NormalizedTranscript(
            provider=cls.name,
            language=language or "en",
            duration_seconds=cls.chunk_seconds,
            utterances=[
                NormalizedUtterance(
                    text=f"chunk {index}", start=0.0,
                    end=cls.chunk_seconds, speaker="A",
                )
            ],
            speakers_detected=["A"],
        )


class QuotaProbeSttProvider(FakeSttProvider):
    """Answers ``quota_status`` from class state — the watchdog's fake.

    ``quota`` is what the balance endpoint "returns"; ``quota_error``
    makes the probe raise, which the sweep must survive. ``probes``
    counts the calls, so a test can prove the watchdog asked rather than
    guessed.
    """

    name = "quota-probe-stt"

    quota = None
    quota_error: Exception | None = None
    probes: list[dict] = []

    @classmethod
    def reset(cls):
        super().reset()
        cls.quota = ProviderQuota(
            provider=cls.name, used=100.0, limit=1000.0, unit="characters"
        )
        cls.quota_error = None
        cls.probes = []

    def quota_status(self, *, timeout_seconds=None):
        cls = type(self)
        cls.probes.append({"timeout_seconds": timeout_seconds})
        if cls.quota_error is not None:
            raise cls.quota_error
        return cls.quota


class SilentQuotaSttProvider(FakeSttProvider):
    """Exposes no balance endpoint — the majority case, and the one the
    watchdog must skip rather than report as empty."""

    name = "silent-quota-stt"


class NotAnSttProvider:
    """Deliberately not an SttProvider subclass — for the W003 check."""


class FakeDiarizationProvider(DiarizationProvider):
    """Recording diarization fake — same class-level-state pattern
    (``get_diarization_provider`` instantiates a fresh object per request)."""

    name = "fake-diar"

    calls: list[dict] = []
    result: NormalizedDiarization | None = None
    error: Exception | None = None

    @classmethod
    def reset(cls):
        cls.calls = []
        cls.result = NormalizedDiarization(
            provider=cls.name,
            duration_seconds=4.0,
            turns=[
                DiarTurn(speaker="SPEAKER_00", start=0.0, end=2.0),
                DiarTurn(speaker="SPEAKER_01", start=2.0, end=4.0, confidence=0.9),
            ],
            speakers_detected=["SPEAKER_00", "SPEAKER_01"],
        )
        cls.error = None

    def diarize(
        self,
        *,
        audio,
        num_speakers=None,
        timeout_seconds=None,
        provider_options=None,
    ):
        cls = type(self)
        cls.calls.append(
            {
                "audio": audio,
                "num_speakers": num_speakers,
                "timeout_seconds": timeout_seconds,
                "provider_options": provider_options,
            }
        )
        if cls.error is not None:
            raise cls.error
        return cls.result


class FatalDiarizationProvider(FakeDiarizationProvider):
    """Always fails permanently — for the failure-envelope path."""

    name = "fatal-diar"

    @classmethod
    def reset(cls):
        super().reset()
        cls.error = DiarizationError(
            "audio is not decodable", provider=cls.name, status_code=400
        )


class NotADiarizationProvider:
    """Deliberately not a DiarizationProvider subclass — for W007."""


class FakeEmbeddingProvider(EmbeddingProvider):
    """Recording embedding fake — one deterministic vector per text, in
    input order (so order-preservation is assertable end-to-end)."""

    name = "fake-embed"

    calls: list[dict] = []
    error: Exception | None = None

    @classmethod
    def reset(cls):
        cls.calls = []
        cls.error = None

    def embed(self, *, texts, model=None, timeout_seconds=None, provider_options=None):
        cls = type(self)
        cls.calls.append(
            {
                "texts": texts,
                "model": model,
                "timeout_seconds": timeout_seconds,
                "provider_options": provider_options,
            }
        )
        if cls.error is not None:
            raise cls.error
        batch = require_texts(texts, provider=self.name)
        return NormalizedEmbeddings(
            provider=self.name,
            # Attribution = what actually ran: the caller's pin when it
            # was honored, else this fake's own model.
            model=model or "fake-embed-1",
            dim=2,
            # Positional fingerprint: vectors[i] encodes i, so a reorder
            # anywhere in the pipeline is machine-visible.
            vectors=[[float(i), float(len(t))] for i, t in enumerate(batch)],
            usage={"prompt_tokens": sum(len(t) for t in batch)},
        )


class FatalEmbeddingProvider(FakeEmbeddingProvider):
    """Always fails permanently — for the failure-envelope path."""

    name = "fatal-embed"

    @classmethod
    def reset(cls):
        super().reset()
        cls.error = EmbeddingError(
            "auth rejected", provider=cls.name, status_code=401
        )


class NotAnEmbeddingProvider:
    """Deliberately not an EmbeddingProvider subclass — for W009."""


class FakeRerankProvider(RerankProvider):
    """Recording rerank fake — deterministic length-as-score ranking
    (score = len(document)), so the sort order and the index-join are
    assertable end-to-end from the document texts alone."""

    name = "fake-rerank"

    calls: list[dict] = []
    error: Exception | None = None

    @classmethod
    def reset(cls):
        cls.calls = []
        cls.error = None

    def rerank(
        self,
        *,
        query,
        documents,
        top_n=None,
        timeout_seconds=None,
        provider_options=None,
    ):
        cls = type(self)
        cls.calls.append(
            {
                "query": query,
                "documents": documents,
                "top_n": top_n,
                "timeout_seconds": timeout_seconds,
                "provider_options": provider_options,
            }
        )
        if cls.error is not None:
            raise cls.error
        query, docs = require_rerank_inputs(
            query, documents, top_n=top_n, provider=self.name
        )
        results = [
            RerankResult(index=i, score=float(len(doc)))
            for i, doc in enumerate(docs)
        ]
        return NormalizedRerank(
            provider=self.name,
            model="fake-rerank-1",
            results=rank_results(
                results, n_documents=len(docs), top_n=top_n, provider=self.name
            ),
            usage={"input_tokens": len(query) + sum(len(d) for d in docs)},
        )


class FatalRerankProvider(FakeRerankProvider):
    """Always fails permanently — for the failure-envelope path."""

    name = "fatal-rerank"

    @classmethod
    def reset(cls):
        super().reset()
        cls.error = RerankError(
            "auth rejected", provider=cls.name, status_code=401
        )


class NotARerankProvider:
    """Deliberately not a RerankProvider subclass — for W011."""


class LegacyCachePolicy(CachePolicy):
    """A policy written against the pre-AGENT-02 signature — it has no way
    to tell two tenants apart. ``services`` must refuse to use it rather
    than let it answer one tenant with another's response."""

    lookups: list = []

    @classmethod
    def reset(cls):
        cls.lookups = []

    def should_cache(self, source):
        return True

    def lookup(self, prompt, system_prompt, source, *, provider, model, model_size):
        type(self).lookups.append(prompt)
        return "answer from another tenant"


# --------------------------------------------------------------------------- #
# Guarded audio download (AudioRef.read_bytes → stapel_core.net.fetch_bytes)
# --------------------------------------------------------------------------- #
def addrinfo(ip: str, port: int = 443):
    import socket

    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    return [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, port))]


class FakeHttpResponse:
    """Minimal stand-in for ``http.client.HTTPResponse``."""

    def __init__(self, status=200, headers=None, body=b""):
        import io

        self.status = status
        self._headers = {k.lower(): v for k, v in (headers or {}).items()}
        self._buf = io.BytesIO(body)

    def getheader(self, name, default=None):
        return self._headers.get(name.lower(), default)

    def read(self, n=-1):
        return self._buf.read(n)

    def close(self):
        pass


def allow_any_audio_host(monkeypatch):
    """Stand in for a deployment that opted into any-public-host audio.

    The shipped default is closed: an empty ``STT_DOWNLOAD_ALLOWED_HOSTS``
    refuses the download instead of meaning "any host". Tests whose
    subject is a *different* guard (scheme, IP range, redirect, DNS) say
    so here in one line, rather than each carrying settings for a decision
    they are not about. The flag is patched rather than overridden through
    ``settings`` because pytest-django's fixture replaces the whole
    ``STAPEL_AGENT`` dict, and half these call sites set it afterwards.
    """
    from stapel_agent import conf

    monkeypatch.setattr(conf, "stt_download_allow_any_host", lambda: True)


def serve_audio(monkeypatch, *responses, ip="93.184.216.34", allow_any_host=True):
    """Fake the network under the guarded fetcher — one response per hop.

    Patches the two seams the fetcher owns (``socket.getaddrinfo`` and
    ``stapel_core.net.safe_fetch._open``) rather than the download function
    itself, so provider tests keep exercising the real guard chain instead
    of a stub that would hide a hole in it. Never egresses. Returns the
    list of ``(host, ip, path)`` tuples actually connected to.

    *allow_any_host* applies :func:`allow_any_audio_host` (the default),
    so a test about some other guard does not have to think about the
    allowlist. A test about the allowlist gate itself passes
    ``allow_any_host=False`` and gets the real accessor back; the
    allowlist setting wins over the flag either way, so the host-pinning
    tests are unaffected.
    """
    import socket

    from stapel_core.net import safe_fetch

    if allow_any_host:
        allow_any_audio_host(monkeypatch)

    specs = list(responses) or [b"audio-bytes"]
    seen: list[tuple] = []

    def _build(spec):
        # Bytes are shorthand for "200 with this audio body". A fresh
        # response object per hop: a body is a stream, and the last spec
        # repeats for callers that download more than once.
        if isinstance(spec, bytes):
            return FakeHttpResponse(200, {"Content-Type": "audio/mpeg"}, spec)
        return spec()

    def fake_open(host, ip_, port, path, **kwargs):
        seen.append((host, str(ip_), path))
        spec = specs.pop(0) if len(specs) > 1 else specs[0]
        return _build(spec)

    monkeypatch.setattr(
        socket, "getaddrinfo", lambda host, port, **kw: addrinfo(ip, port)
    )
    monkeypatch.setattr(safe_fetch, "_open", fake_open)
    return seen
