"""Language-aware STT provider routing (the hardcoded language matrix is
replaced by the ``STT_LANGUAGE_ROUTES`` setting).

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
from .languages import canonical_language, route_keys


def select_chain(
    language: Optional[str], *, provider: Optional[str] = None
) -> list[str]:
    """Ordered, de-duplicated provider names to try.

    The route lookup is done on the CANONICAL language (see
    :mod:`stapel_agent.stt.languages`): a route stated as ``{"ru": [...]}``
    fires for ``ru``, ``rus`` and ``RUS`` alike, which it did not before
    0.25.0 — the three-letter spelling missed the matrix and was quietly
    transcribed by the default chain. Keys are tried most-specific first,
    so ``{"pt-BR": [...], "pt": [...]}`` is now expressible; the routes
    dict itself is canonicalised too, so a host that wrote ``{"eng": ...}``
    is matched by ``en``.
    """
    if provider:
        return [provider]

    try:
        lang = canonical_language(language)
    except Exception:
        # An unresolvable code is refused at the boundary; here — where a
        # raise would turn a routing question into a crash — it simply
        # selects no route and falls through to the default chain.
        lang = None
    routes = _canonical_routes(agent_settings.STT_LANGUAGE_ROUTES or {})
    chain: list[str] = []
    for key in route_keys(lang):
        if key in routes:
            chain = list(routes[key] or [])
            break
    if not chain:
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


def _canonical_routes(routes: dict) -> dict:
    """``STT_LANGUAGE_ROUTES`` re-keyed to canonical codes.

    A host's spelling is a host's business: ``{"eng": [...]}`` and
    ``{"pt_BR": [...]}`` are the same routes as ``{"en": ...}`` and
    ``{"pt-BR": ...}``, and a matrix that only fires when the caller and
    the settings file happen to agree on a spelling is the defect this
    release closes, not half of it. A key that cannot be canonicalised is
    kept verbatim — a refusal here would take a deployment's whole STT
    surface down over one unused row.
    """
    out: dict = {}
    for key, value in routes.items():
        try:
            canonical = canonical_language(key)
        except Exception:
            canonical = None
        out.setdefault(canonical or str(key), value)
    return out


__all__ = ["select_chain"]
