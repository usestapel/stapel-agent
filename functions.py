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

IMAGE_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "url": {"type": "string", "description": "Fetchable image URL."},
        "data_b64": {
            "type": "string",
            "description": "Base64-encoded image bytes — raw bytes never "
            "travel over comm.",
        },
        "mime": {
            "type": "string",
            "description": "Content type for data_b64 (default image/png).",
        },
    },
    "oneOf": [{"required": ["url"]}, {"required": ["data_b64"]}],
    "additionalProperties": False,
}

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
        "images": {
            "type": "array",
            "items": IMAGE_ITEM_SCHEMA,
            "description": "Vision input — the provider must support "
            "image content blocks.",
        },
        "role": {
            "type": "string",
            "description": "Opaque caller tag — e.g. the calling role in "
            "a multi-role pipeline. Carried for provider routing, override "
            "providers and observability; the default completion pipeline "
            "ignores it.",
        },
        "max_tokens": {
            "type": "integer",
            "minimum": 1,
            "description": "Per-call output-token cap overriding the "
            "configured STAPEL_AGENT['MAX_TOKENS'] — long structured "
            "outputs raise it, short ones bound cost. Ignored (with a "
            "logged warning) by providers without supports_max_tokens.",
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
    """JSON LLM completion — same result dict as ``POST api/v1/llm/complete``.

    Payload: ``{"prompt": str, "model": "small"|"medium"|"large",
    "system_prompt"?: str, "provider"?: str, "images"?: [{"url"} |
    {"data_b64", "mime"?}], "role"?: str, "max_tokens"?: int}``. ``role``
    is an opaque caller tag (multi-role pipelines address
    providers/observability by it) and is ignored here; ``max_tokens`` is
    a per-call output-token cap overriding the configured ``MAX_TOKENS``.
    Returns ``{"status": "ok"|"failure",
    "result"?: object, "comment"?: str, "reason"?: str, "usage"?: {...}}``.
    """
    from . import services
    from .images.base import ImageRef

    try:
        images = [ImageRef.from_payload(i) for i in payload.get("images") or []]
    except (ValueError, TypeError) as exc:
        # Base64 validity is beyond JSON Schema — degrade to the failure
        # envelope instead of leaking a decode traceback through comm.
        return {"status": "failure", "reason": f"Invalid image payload: {exc}"}

    return services.complete_json(
        payload["prompt"],
        payload["model"],
        system_prompt=payload.get("system_prompt"),
        provider=payload.get("provider"),
        images=images or None,
        max_tokens=payload.get("max_tokens"),
    )


@function("llm.translate", schema=TRANSLATE_SCHEMA)
def llm_translate(payload: dict) -> dict:
    """Key-value translation — same result dict as ``POST api/v1/llm/translate``.

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
            "minimum": 1,
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
    """Speech-to-text — same result dict as ``POST api/v1/llm/transcribe``.

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


STT_CATALOG_SCHEMA = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}


@function("llm.stt_catalog", schema=STT_CATALOG_SCHEMA)
def llm_stt_catalog(payload: dict) -> dict:
    """STT catalogue — the addressable speech-to-text surface.

    Takes no arguments (``{}``). Returns ``{"status": "ok", "providers":
    [{"name", "available", "model", "pinned_model", "supports_diarization",
    "supported_languages", "cost_per_hour"}...], "default_provider": str,
    "fallback_chain": [str], "language_routes": {lang: [str]}}``. ``model``
    is each registration's effective model — the per-registration
    ``speech_model`` pin when set, else the provider's configured default;
    ``pinned_model`` flags which is which. Callers use this to discover
    which ``provider`` names ``llm.transcribe`` will accept and what each
    would run, without a transcription round-trip.
    """
    from . import services

    return services.stt_catalog()


@function("llm.summarize", schema=SUMMARIZE_SCHEMA)
def llm_summarize(payload: dict) -> dict:
    """Summarization — same result dict as ``POST api/v1/llm/summarize``.

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


GENERATE_IMAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "prompt": {
            "type": "string",
            "description": "Text description of the desired image(s).",
        },
        "size": {
            "type": "string",
            "description": "\"WxH\" size string (default 1024x1024).",
        },
        "n": {
            "type": "integer",
            "minimum": 1,
            "maximum": 10,
            "description": "Number of images (default 1).",
        },
        "provider": {
            "type": "string",
            "description": "Image provider name from "
            "STAPEL_AGENT['IMAGE_PROVIDERS'].",
        },
        "timeout_seconds": {
            "type": "integer",
            "minimum": 1,
            "description": "Hard cap on the generation request.",
        },
    },
    "required": ["prompt"],
    "additionalProperties": False,
}


@function("llm.generate_image", schema=GENERATE_IMAGE_SCHEMA)
def llm_generate_image(payload: dict) -> dict:
    """Image generation — same result dict as
    ``POST api/v1/llm/generate-image``.

    Payload: ``{"prompt": str, "size"?: str, "n"?: int, "provider"?:
    str, "timeout_seconds"?: int}``. Returns ``{"status": "ok", "images":
    [{"url"? | "data_b64"?, "mime"}], "provider_used": str}`` or
    ``{"status": "failure", "reason": str}``. Storing results into
    CDN/asset libraries is the caller's job — this function returns raw
    provider output.
    """
    from . import services

    return services.generate_image(
        payload["prompt"],
        size=payload.get("size") or "1024x1024",
        n=int(payload.get("n") or 1),
        provider=payload.get("provider"),
        timeout_seconds=payload.get("timeout_seconds"),
    )
