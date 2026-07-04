"""Recording fake provider, wired via the STAPEL_AGENT PROVIDERS override.

``services.get_provider`` instantiates a fresh object per request
(dotted-path + import_string), so calls and canned results live on the
class — reset via ``FakeProvider.reset()`` (the ``fake_provider`` fixture
does this automatically).
"""
from __future__ import annotations

from stapel_agent.cache import CachePolicy
from stapel_agent.providers.base import LlmProvider, ProviderResult
from stapel_agent.stt.base import (
    NormalizedTranscript,
    NormalizedUtterance,
    RetryableTranscriptionError,
    SttProvider,
    TranscriptionError,
)


class FakeProvider(LlmProvider):
    name = "fake"

    calls: list[dict] = []
    result = ProviderResult(text='{"answer": 42}')
    error: Exception | None = None

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

    def complete(self, *, prompt, model, system_prompt=None):
        cls = type(self)
        cls.calls.append(
            {"prompt": prompt, "model": model, "system_prompt": system_prompt}
        )
        if cls.error is not None:
            raise cls.error
        return cls.result


class CustomProvider(FakeProvider):
    """A second provider class so tests can tell registrations apart."""

    name = "custom"


class NotAProvider:
    """Deliberately not an LlmProvider subclass — for the W002 check."""


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

    def lookup(self, prompt, system_prompt, source):
        cls = type(self)
        cls.lookups.append((prompt, system_prompt, source))
        return cls.entries.get((prompt, system_prompt, source))

    def store(self, prompt, system_prompt, source, response):
        cls = type(self)
        cls.stores.append((prompt, system_prompt, source, response))
        cls.entries[(prompt, system_prompt, source)] = response


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

    def transcribe(self, *, audio, language=None, diarization=False, timeout_seconds=None):
        cls = type(self)
        cls.calls.append(
            {
                "audio": audio,
                "language": language,
                "diarization": diarization,
                "timeout_seconds": timeout_seconds,
            }
        )
        if cls.error is not None:
            raise cls.error
        return cls.result


class SecondSttProvider(FakeSttProvider):
    """A second STT class so fallback tests can tell providers apart."""

    name = "fake-stt-2"


class RetryableSttProvider(FakeSttProvider):
    """Always fails transiently — the service must walk the chain."""

    name = "retry-stt"

    @classmethod
    def reset(cls):
        super().reset()
        cls.error = RetryableTranscriptionError(
            "stt rate limited", provider=cls.name, status_code=429
        )


class FatalSttProvider(FakeSttProvider):
    """Always fails permanently — the service must NOT fall back."""

    name = "fatal-stt"

    @classmethod
    def reset(cls):
        super().reset()
        cls.error = TranscriptionError(
            "audio is not decodable", provider=cls.name, status_code=400
        )


class NotAnSttProvider:
    """Deliberately not an SttProvider subclass — for the W003 check."""
