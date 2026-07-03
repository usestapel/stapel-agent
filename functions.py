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
