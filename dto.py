"""DTOs for the agent API."""

from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class CompleteRequest:
    """JSON LLM completion request.

    Attributes:
        prompt: The user prompt sent to the model.
        model: Model size — small, medium or large. Example: small
        provider: Provider name from STAPEL_AGENT["PROVIDERS"]; defaults
            to DEFAULT_PROVIDER.
        system_prompt: Replaces the built-in JSON-API system prompt.
    """

    prompt: str
    model: str
    provider: Optional[str] = None
    system_prompt: Optional[str] = None


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
