"""comm Function providers of the agent module.

Registered from ``AgentConfig.ready()`` (importing this module is enough:
re-imports are no-ops and re-registering the same handler object is
idempotent). Other modules call these by name via ``stapel_core.comm.call``
— no import of this package needed, and in a monolith the call is
in-process without HTTP:

    from stapel_core.comm import call

    call("llm.translate", {"from_lang": "auto", "to": "de", "entries": {...}})
"""
import logging

from stapel_core.comm import function

logger = logging.getLogger(__name__)

COMPLETE_SCHEMA = {
    "type": "object",
    "properties": {
        "prompt": {"type": "string", "description": "The user prompt."},
        "model": {
            "type": "string",
            "enum": ["small", "medium", "large"],
            "description": "Model size, mapped via STAPEL_AGENT['MODELS'].",
        },
        "system_prompt": {
            "type": "string",
            "description": "Replaces the built-in JSON-API system prompt.",
        },
        "provider": {
            "type": "string",
            "description": "Provider name from STAPEL_AGENT['PROVIDERS'].",
        },
    },
    "required": ["prompt", "model"],
    "additionalProperties": False,
}

TRANSLATE_SCHEMA = {
    "type": "object",
    "properties": {
        "from_lang": {
            "type": "string",
            "description": "Source language code, or 'auto' to auto-detect.",
        },
        "to": {"type": "string", "description": "Target language code."},
        "entries": {
            "type": "object",
            "additionalProperties": {"type": "string"},
            "description": "Mapping of keys to source-language strings.",
        },
    },
    "required": ["from_lang", "to", "entries"],
    "additionalProperties": False,
}


@function("llm.complete", schema=COMPLETE_SCHEMA)
def llm_complete(payload: dict) -> dict:
    """JSON LLM completion — same result dict as ``POST api/llm/complete``.

    Payload: ``{"prompt": str, "model": "small"|"medium"|"large",
    "system_prompt"?: str, "provider"?: str}``. Returns
    ``{"status": "ok"|"failure", "result"?: object, "comment"?: str,
    "reason"?: str, "usage"?: {...}}``.
    """
    from . import services

    return services.complete_json(
        payload["prompt"],
        payload["model"],
        system_prompt=payload.get("system_prompt"),
        provider=payload.get("provider"),
    )


@function("llm.translate", schema=TRANSLATE_SCHEMA)
def llm_translate(payload: dict) -> dict:
    """Key-value translation — same result dict as ``POST api/llm/translate``.

    Payload: ``{"from_lang": str, "to": str, "entries": {key: text}}``.
    Returns ``{"status": "ok", "result": {key: translated}}`` or
    ``{"status": "failure", "reason": str}``.
    """
    from . import services

    return services.translate(
        payload["from_lang"],
        payload["to"],
        payload["entries"],
    )


TRANSCRIBE_SCHEMA = {
    "type": "object",
    "properties": {
        "audio_url": {
            "type": "string",
            "description": "Fetchable audio URL (presigned S3/MinIO GET). "
            "comm carries URLs only, never raw bytes.",
        },
        "language": {
            "type": "string",
            "description": "BCP-47 hint; omit for auto-detect.",
        },
        "diarization": {
            "type": "boolean",
            "description": "Ask the provider for speaker labels.",
        },
        "provider": {
            "type": "string",
            "description": "Pin one STT provider (no fallback).",
        },
        "timeout_seconds": {
            "type": "integer",
            "description": "Hard cap on one provider's submit+poll cycle.",
        },
    },
    "required": ["audio_url"],
    "additionalProperties": False,
}

SUMMARIZE_SCHEMA = {
    "type": "object",
    "properties": {
        "text": {"type": "string", "description": "Plain text to summarize."},
        "transcript": {
            "type": "object",
            "description": "A NormalizedTranscript dict (llm.transcribe output).",
        },
        "language": {
            "type": "string",
            "description": "Language to respond in; defaults to the input's.",
        },
        "model": {
            "type": "string",
            "enum": ["small", "medium", "large"],
            "description": "Model size (default medium).",
        },
        "provider": {
            "type": "string",
            "description": "LLM provider name from STAPEL_AGENT['PROVIDERS'].",
        },
    },
    "oneOf": [{"required": ["text"]}, {"required": ["transcript"]}],
    "additionalProperties": False,
}


@function("llm.transcribe", schema=TRANSCRIBE_SCHEMA)
def llm_transcribe(payload: dict) -> dict:
    """Speech-to-text — same result dict as ``POST api/llm/transcribe``.

    Payload: ``{"audio_url": str, "language"?, "diarization"?,
    "provider"?, "timeout_seconds"?}``. Returns ``{"status": "ok",
    "transcript": {...}, "provider_used": str, "fallback_used": bool}``
    or ``{"status": "failure", "reason": str}``.
    """
    from . import services
    from .stt.base import AudioRef

    return services.transcribe(
        AudioRef(url=payload["audio_url"]),
        language=payload.get("language"),
        diarization=bool(payload.get("diarization", False)),
        provider=payload.get("provider"),
        timeout_seconds=payload.get("timeout_seconds"),
    )


@function("llm.summarize", schema=SUMMARIZE_SCHEMA)
def llm_summarize(payload: dict) -> dict:
    """Summarization — same result dict as ``POST api/llm/summarize``.

    Payload carries exactly one of ``text`` / ``transcript`` (a
    NormalizedTranscript dict). Returns ``{"status": "ok", "summary":
    str, "usage": {...}}`` or ``{"status": "failure", "reason": str}``.
    """
    from . import services

    return services.summarize(
        payload["text"] if "text" in payload else payload["transcript"],
        language=payload.get("language"),
        model_size=payload.get("model") or "medium",
        provider=payload.get("provider"),
    )
