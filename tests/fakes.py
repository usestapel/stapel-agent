"""Recording fake provider, wired via the STAPEL_AGENT PROVIDERS override.

``services.get_provider`` instantiates a fresh object per request
(dotted-path + import_string), so calls and canned results live on the
class — reset via ``FakeProvider.reset()`` (the ``fake_provider`` fixture
does this automatically).
"""
from __future__ import annotations

import base64

from stapel_agent.cache import CachePolicy
from stapel_agent.images.base import GeneratedImage, ImageGenProvider
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
    supports_images = True  # vision tests route ImageRefs through it
    supports_max_tokens = True  # per-call cap tests route max_tokens through it

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

    def complete(self, *, prompt, model, system_prompt=None, images=None,
                 max_tokens=None):
        cls = type(self)
        cls.calls.append(
            {
                "prompt": prompt,
                "model": model,
                "system_prompt": system_prompt,
                "images": images,
                "max_tokens": max_tokens,
            }
        )
        if cls.error is not None:
            raise cls.error
        return cls.result


class CustomProvider(FakeProvider):
    """A second provider class so tests can tell registrations apart."""

    name = "custom"


class NoVisionProvider(FakeProvider):
    """Text-only backend with the pre-vision three-argument signature —
    proves the service never forwards images (or a max_tokens cap) to a
    provider that can't take them (and that old signatures stay
    compatible)."""

    name = "no-vision"
    supports_images = False
    supports_max_tokens = False

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

    def lookup(self, prompt, system_prompt, source, *, provider, model, model_size):
        cls = type(self)
        cls.lookups.append((prompt, system_prompt, source, provider, model, model_size))
        return cls.entries.get((prompt, system_prompt, source))

    def store(self, prompt, system_prompt, source, response, *, provider, model, model_size):
        cls = type(self)
        cls.stores.append(
            (prompt, system_prompt, source, response, provider, model, model_size)
        )
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
