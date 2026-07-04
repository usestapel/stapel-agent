"""Language-aware STT provider routing (ported from the legacy recordings service
``recordings/stt/router.py``, with the hardcoded language matrix replaced
by the ``STT_LANGUAGE_ROUTES`` setting).

``select_chain`` returns provider *names* in try-order. The service walks
the chain on failure — fallback kicks in only on
``RetryableTranscriptionError``, never on fatal ``TranscriptionError``.

Precedence:

1. explicit ``provider`` in the request → single-name chain, NO fallback
   (when QA pins a provider, masking its failures via fallback would
   defeat the purpose — ported intent);
2. ``STT_LANGUAGE_ROUTES[lang]`` (language matrix, e.g.
   ``{"ru": ["gigaam", "whisper-http"]}``);
3. ``[DEFAULT_STT_PROVIDER] + STT_FALLBACK_CHAIN``.
"""
from __future__ import annotations

from typing import Optional

from ..conf import agent_settings
from .base import normalize_language


def select_chain(
    language: Optional[str], *, provider: Optional[str] = None
) -> list[str]:
    """Ordered, de-duplicated provider names to try."""
    if provider:
        return [provider]

    lang = normalize_language(language)
    routes = agent_settings.STT_LANGUAGE_ROUTES or {}
    if lang and lang in routes:
        chain = list(routes[lang] or [])
    else:
        chain = [agent_settings.DEFAULT_STT_PROVIDER] + list(
            agent_settings.STT_FALLBACK_CHAIN or []
        )

    seen: set[str] = set()
    ordered: list[str] = []
    for name in chain:
        if name and name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered


__all__ = ["select_chain"]
