"""JSON extraction from raw LLM text (port of the legacy agent service's llm.service.ts).

LLMs asked for JSON still wrap it in prose or markdown fences; these
helpers recover the payload and keep the surrounding text as *comment*.
Django-free and side-effect-free — unit-tested directly.
"""
from __future__ import annotations

import json
import re

_CODE_BLOCK_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```")
_OBJECT_RE = re.compile(r"(\{[\s\S]*\})")
_ARRAY_RE = re.compile(r"(\[[\s\S]*\])")


def _try_json(text: str):
    try:
        return json.loads(text), True
    except ValueError:
        return None, False


def _comment(trimmed: str, match: re.Match) -> str | None:
    before = trimmed[: match.start()].strip()
    after = trimmed[match.end():].strip()
    return "\n".join(part for part in (before, after) if part).strip() or None


def parse_json_response(response: str) -> tuple[object | None, str | None]:
    """Return ``(result, comment)`` — parsed JSON and the text around it.

    Strategies, in order (same as the legacy agent service's ``parseJsonResponse``):
    direct JSON → ```json fenced block``` → an object anywhere → an array
    anywhere. ``result`` is None when nothing parses; the raw text then
    survives as ``comment``.
    """
    trimmed = (response or "").strip()

    # 1. Direct JSON.
    if trimmed.startswith("{") or trimmed.startswith("["):
        parsed, ok = _try_json(trimmed)
        if ok:
            return parsed, None

    # 2. ```json ... ``` or ``` ... ``` block.
    block = _CODE_BLOCK_RE.search(trimmed)
    if block:
        parsed, ok = _try_json(block.group(1).strip())
        if ok:
            return parsed, _comment(trimmed, block)

    # 3. A JSON object anywhere in the response.
    obj = _OBJECT_RE.search(trimmed)
    if obj:
        parsed, ok = _try_json(obj.group(1))
        if ok:
            return parsed, _comment(trimmed, obj)

    # 4. A JSON array anywhere in the response.
    arr = _ARRAY_RE.search(trimmed)
    if arr:
        parsed, ok = _try_json(arr.group(1))
        if ok:
            return parsed, _comment(trimmed, arr)

    return None, trimmed or None


def parse_translation_response(response: str) -> dict:
    """Parse a ``{key: translated}`` mapping out of raw LLM text.

    Port of the legacy agent service's ``parseTranslationResponse``: prefer a fenced
    block, else the first-to-last-brace object, else the whole string.
    Raises ValueError when the result is not a JSON object.
    """
    text = response or ""
    match = _CODE_BLOCK_RE.search(text) or _OBJECT_RE.search(text)
    json_str = match.group(1) if match else text
    parsed = json.loads(json_str.strip() or "{}")
    if not isinstance(parsed, dict):
        raise ValueError("translation response is not a JSON object")
    return parsed


__all__ = ["parse_json_response", "parse_translation_response"]
