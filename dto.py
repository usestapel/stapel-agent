"""DTOs for the agent API."""

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class CompleteRequest:
    """JSON LLM completion request.

    Attributes:
        prompt: The user prompt sent to the model.
        model: Model size — small, medium or large. Example: small
        provider: Provider name from STAPEL_AGENT["PROVIDERS"]; defaults
            to DEFAULT_PROVIDER.
        system_prompt: Replaces the built-in JSON-API system prompt.
        images: Optional vision input — each entry is {"url": ...} or
            {"data_b64": ..., "mime"?: ...}. The wire never carries raw
            bytes.
    """

    prompt: str
    model: str
    provider: Optional[str] = None
    system_prompt: Optional[str] = None
    images: Optional[List[dict]] = None


@dataclass
class TranslateRequest:
    """Key-value translation request.

    The wire key for the source language is ``from`` (a Python keyword) —
    the serializer maps it onto ``from_lang`` explicitly.

    Attributes:
        from_lang: Source language code, or "auto" to auto-detect.
        to: Target language code. Example: de
        entries: Mapping of keys to source-language strings.
    """

    from_lang: str
    to: str
    entries: Dict[str, str] = field(default_factory=dict)


@dataclass
class TranslateResponse:
    """Key-value translation response (the legacy agent service contract).

    Attributes:
        status: "ok" or "failure".
        result: Mapping of keys to translated strings (on success).
        reason: Failure reason (on failure).
    """

    status: str
    result: Optional[Dict[str, str]] = None
    reason: Optional[str] = None


@dataclass
class TranscribeRequest:
    """Speech-to-text request.

    Attributes:
        audio_url: Fetchable audio URL (presigned S3/MinIO GET).
        language: BCP-47 hint; omit for auto-detect.
        diarization: Ask the provider for speaker labels.
        provider: Pin one STT provider (no fallback).
        timeout_seconds: Hard cap on one provider's submit+poll cycle.
    """

    audio_url: str
    language: Optional[str] = None
    diarization: bool = False
    provider: Optional[str] = None
    timeout_seconds: Optional[int] = None


@dataclass
class SummarizeRequest:
    """Summarization request — exactly one of text/transcript.

    Attributes:
        text: Plain text to summarize.
        transcript: A NormalizedTranscript dict (llm.transcribe output).
        language: Language to respond in; defaults to the input's.
        model: Model size (small/medium/large). Example: medium
        provider: LLM provider name.
    """

    text: Optional[str] = None
    transcript: Optional[dict] = None
    language: Optional[str] = None
    model: str = "medium"
    provider: Optional[str] = None


@dataclass
class GenerateImageRequest:
    """Image-generation request.

    Attributes:
        prompt: Text description of the desired image(s).
        size: "WxH" size string. Example: 1024x1024
        n: Number of images (1-10).
        provider: Image provider name from STAPEL_AGENT["IMAGE_PROVIDERS"];
            defaults to DEFAULT_IMAGE_PROVIDER.
        timeout_seconds: Hard cap on the generation request.
    """

    prompt: str
    size: str = "1024x1024"
    n: int = 1
    provider: Optional[str] = None
    timeout_seconds: Optional[int] = None


@dataclass
class SummarizeResponse:
    """Summarization response.

    Attributes:
        status: "ok" or "failure".
        summary: Markdown summary (on success).
        usage: Aggregated token usage across all LLM calls.
        reason: Failure reason (on failure).
    """

    status: str
    summary: Optional[str] = None
    usage: Optional[Dict[str, int]] = None
    reason: Optional[str] = None
