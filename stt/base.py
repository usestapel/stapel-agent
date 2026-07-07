"""STT provider seam — normalized transcript schema, AudioRef, ABC, errors.

Battle-tested in the convert→transcribe→diarize pipeline, generalized in
two ways:

- **AudioRef** replaces the bare ``audio_url``: exactly one of ``url`` /
  ``path`` / ``data`` (bytes). Cloud adapters that need a fetchable URL
  call ``require_url()``; upload-style adapters (whisper-http) accept any
  ref via ``read_bytes()``.
- Errors join the house hierarchy: ``TranscriptionError(ProviderError)``
  (the source's ``STTFatal`` — bad input/auth, do NOT fall back) and
  ``RetryableTranscriptionError`` (``STTRetryable`` — 429/5xx/timeouts,
  the service walks the fallback chain).

This module is deliberately Django-free.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Optional

from ..providers.base import ProviderError


class TranscriptionError(ProviderError):
    """Permanent STT failure (bad audio, unsupported language, auth, ...).

    The service reports ``status: "failure"`` immediately — no fallback,
    the next provider would fail on the same input.
    """

    def __init__(self, message: str, *, provider: str = "", status_code: int | None = None):
        super().__init__(message)
        self.provider = provider
        self.status_code = status_code


class RetryableTranscriptionError(TranscriptionError):
    """Transient STT failure (network, 429, 5xx, poll timeout) — the
    service tries the next provider in the fallback chain."""


# ─── Normalized transcript schema ──────────────────────────────────────


@dataclass
class NormalizedWord:
    """Word-level token. Times are seconds from audio start."""

    text: str
    start: float
    end: float
    confidence: Optional[float] = None
    speaker: Optional[str] = None


@dataclass
class NormalizedUtterance:
    """Sentence/phrase-level grouping (one speaker turn)."""

    text: str
    start: float
    end: float
    speaker: Optional[str] = None
    confidence: Optional[float] = None
    word_indexes: list[int] = field(default_factory=list)


@dataclass
class NormalizedTranscript:
    """Output every STT provider must return.

    Attributes:
        provider: Adapter id (e.g. ``elevenlabs``). Recorded on the
            PromptLog row for billing and observability.
        language: BCP-47 tag the provider detected/used; None if unknown.
        duration_seconds: Total audio duration as reported/inferred.
        words: Word-level segmentation (may be empty for text-only APIs).
        utterances: Sentence/phrase grouping (optional; derived from words
            when the provider doesn't ship it).
        speakers_detected: Distinct speaker labels seen in ``words``.
        raw: Untouched provider response for debugging / re-parsing.
    """

    provider: str
    language: Optional[str]
    duration_seconds: Optional[float]
    words: list[NormalizedWord] = field(default_factory=list)
    utterances: list[NormalizedUtterance] = field(default_factory=list)
    speakers_detected: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def text(self) -> str:
        """Plain-text rendering (utterances joined, else words joined)."""
        if self.utterances:
            return "\n".join(u.text for u in self.utterances if u.text)
        return " ".join(w.text for w in self.words)


def transcript_from_dict(data: dict) -> NormalizedTranscript:
    """Inverse of ``NormalizedTranscript.to_dict()`` — used by the comm/HTTP
    summarize surfaces, which receive the transcript as plain JSON."""
    return NormalizedTranscript(
        provider=data.get("provider", ""),
        language=data.get("language"),
        duration_seconds=data.get("duration_seconds"),
        words=[NormalizedWord(**w) for w in data.get("words") or []],
        utterances=[NormalizedUtterance(**u) for u in data.get("utterances") or []],
        speakers_detected=list(data.get("speakers_detected") or []),
        raw=data.get("raw") or {},
    )


def utterances_from_words(words: list[NormalizedWord]) -> list[NormalizedUtterance]:
    """Group consecutive same-speaker words into utterances."""
    if not words:
        return []
    grouped: list[NormalizedUtterance] = []
    buf_text: list[str] = []
    buf_indexes: list[int] = []
    buf_start = words[0].start
    buf_end = words[0].end
    buf_speaker = words[0].speaker

    def flush():
        grouped.append(
            NormalizedUtterance(
                text=" ".join(buf_text).strip(),
                start=buf_start,
                end=buf_end,
                speaker=buf_speaker,
                word_indexes=list(buf_indexes),
            )
        )

    for idx, w in enumerate(words):
        if idx and w.speaker != buf_speaker:
            flush()
            buf_text = [w.text]
            buf_indexes = [idx]
            buf_start = w.start
            buf_end = w.end
            buf_speaker = w.speaker
        else:
            buf_text.append(w.text)
            buf_indexes.append(idx)
            buf_end = w.end
    flush()
    return grouped


# ─── AudioRef ──────────────────────────────────────────────────────────


@dataclass
class AudioRef:
    """Reference to the audio to transcribe — exactly one of url/path/data.

    ``url`` is a fetchable HTTP(S) URL (presigned S3/MinIO GET), ``path``
    a local filesystem path, ``data`` raw bytes. ``mime`` is an optional
    content-type hint for upload-style adapters.
    """

    url: Optional[str] = None
    path: Optional[str] = None
    data: Optional[bytes] = None
    mime: Optional[str] = None

    def __post_init__(self):
        provided = [k for k in ("url", "path", "data") if getattr(self, k)]
        if len(provided) != 1:
            raise ValueError(
                "AudioRef needs exactly one of url/path/data, got "
                + (", ".join(provided) or "none")
            )

    @classmethod
    def from_payload(cls, payload: dict) -> "AudioRef":
        """Build from a wire payload (accepts ``audio_url`` or ``url``)."""
        return cls(
            url=payload.get("audio_url") or payload.get("url"),
            path=payload.get("path"),
            data=payload.get("data"),
            mime=payload.get("mime"),
        )

    @property
    def kind(self) -> str:
        if self.url:
            return "url"
        if self.path:
            return "path"
        return "data"

    def require_url(self, *, provider: str) -> str:
        """Cloud adapters need a fetchable URL — clear error otherwise."""
        if not self.url:
            raise TranscriptionError(
                f"provider '{provider}' requires an audio URL "
                f"(got a {self.kind} ref) — upload the file and pass a "
                "presigned URL, or use an upload-capable provider like "
                "whisper-http",
                provider=provider,
            )
        return self.url

    def read_bytes(self, *, provider: str, timeout: int = 600) -> bytes:
        """Materialize the audio bytes from any ref kind (upload adapters)."""
        if self.data is not None:
            return self.data
        if self.path:
            try:
                with open(self.path, "rb") as fh:
                    return fh.read()
            except OSError as exc:
                raise TranscriptionError(
                    f"audio path not readable: {exc}", provider=provider
                ) from exc
        return _download(self.url or "", provider=provider, timeout=timeout)

    def describe(self) -> str:
        """Short PII-safe descriptor for the PromptLog row (no signed
        query strings, no raw bytes)."""
        if self.url:
            from urllib.parse import urlparse

            host = urlparse(self.url).netloc or "?"
            return f"url:{host}"
        if self.path:
            import os.path

            return f"path:{os.path.basename(self.path)}"
        return f"data:{len(self.data or b'')}b"


def _download(url: str, *, provider: str, timeout: int) -> bytes:
    """Fetch audio bytes once; multipart upload needs a buffer anyway.
    4xx on the audio URL is fatal (the ref itself is bad); network/5xx
    are retryable — ported from the source's ``_stream_audio``."""
    import requests

    try:
        resp = requests.get(url, timeout=timeout, stream=True)
        resp.raise_for_status()
        return resp.content
    except requests.Timeout as exc:
        raise RetryableTranscriptionError(
            f"audio download timed out: {exc}", provider=provider
        ) from exc
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else None
        if status is not None and status < 500:
            raise TranscriptionError(
                f"audio URL not retrievable: {status}",
                provider=provider,
                status_code=status,
            ) from exc
        raise RetryableTranscriptionError(
            f"audio download failed: {exc}", provider=provider, status_code=status
        ) from exc
    except requests.RequestException as exc:
        raise RetryableTranscriptionError(
            f"audio download error: {exc}", provider=provider
        ) from exc


# ─── Provider ABC ──────────────────────────────────────────────────────


class SttProvider(ABC):
    """Adapter for a single STT engine.

    ``name`` is the stable id stored on the PromptLog row and used by the
    router; ``supported_languages`` is a set of ISO-639-1 codes or None
    for "any"; ``cost_per_hour`` (USD, optional) lets hosts compute
    billing debits without a separate catalog.
    """

    name: str = ""
    supports_diarization: bool = False
    supported_languages: Optional[frozenset[str]] = None
    cost_per_hour: Optional[float] = None

    @abstractmethod
    def transcribe(
        self,
        *,
        audio: AudioRef,
        language: Optional[str] = None,
        diarization: bool = False,
        timeout_seconds: Optional[int] = None,
    ) -> NormalizedTranscript:
        """Run a synchronous (polling-based) batch transcription.

        Raises RetryableTranscriptionError on transient failure (network,
        429, 5xx, poll timeout — the service falls back to the next
        provider) and TranscriptionError on permanent failure (bad input,
        auth — no fallback).
        """
        raise NotImplementedError


def normalize_language(language: Optional[str]) -> Optional[str]:
    """BCP-47 → bare ISO-639-1 (``en-US``/``en_us`` → ``en``)."""
    if not language:
        return None
    return language.lower().split("-")[0].split("_")[0]


__all__ = [
    "AudioRef",
    "NormalizedTranscript",
    "NormalizedUtterance",
    "NormalizedWord",
    "RetryableTranscriptionError",
    "SttProvider",
    "TranscriptionError",
    "normalize_language",
    "transcript_from_dict",
    "utterances_from_words",
]
