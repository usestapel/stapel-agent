"""Injection-marker detection + context-specific sanitizers.

P108 invariant: ``notes/narrative = UNTRUSTED OUTPUT SURFACE`` — grok on the
PWNED-injection probe reproduced the marker inside ``extraction_notes``, not
executed, but visible in artifacts. The UI/RAG layer must therefore treat any
LLM-authored free-text field as untrusted.

Design (Spark Q4, updated P129b.1 + P129b-deploy-smoke 2026-07-24):

- ``detect_pwned_markers(text)`` returns a ``SafetyReport``: which markers
  were seen (by NAME, never the marker text), whether the text is untrusted
  (LLM-authored surfaces always are). Reader attaches this to a
  ``SafetyFlags`` on ``ProductSummaryResult`` — raw text stays for audit.
- ``redact_markers(text)`` replaces marker hits with ``_REDACTED_STUB`` and
  performs NO entity encoding. Use this for text rendered through
  auto-escaping template engines (Jinja2) -- encoding is the template
  engine's job. Pre-encoding here caused double-escaping (P129b bug:
  ``'`` -> ``&#x27;`` -> ``&amp;#x27;``).
- ``sanitize_for_rag(text)`` returns text safe to embed into a downstream
  LLM prompt — strips markers ENTIRELY (no residual footer), collapses
  whitespace. A follow-up summary MUST use this, never raw.

Historical: an ``sanitize_for_html(text)`` helper (redact + entity-encode)
lived here through P129b.1 but had zero callers (P129b caused double-escape
when it was used pre-Jinja). REMOVED in P129b-deploy-smoke (2026-07-24) —
resurrect from git log ONLY when a non-Jinja consumer (e.g. a JS client that
innerHTMLs a JSON field) appears. Never re-introduce pre-Jinja HTML encoding.

Non-goal: this module does NOT try to be a general prompt-injection
detector. The marker list is anchored to concrete strings seen in P108/P109
red-team probes; a smarter attacker will bypass. The right defense is
architectural (context section vs instruction section — inv #2), not lexical.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


#: Concrete injection markers observed in P108 red-team probes. Case-insensitive.
#: The KEY is the human-readable label surfaced through ``markers`` field; the
#: VALUE is the regex pattern.
INJECTION_MARKERS: dict[str, re.Pattern[str]] = {
    "PWNED": re.compile(r"\bpwned\b", re.IGNORECASE),
    "SYSTEM_PWNED": re.compile(r"\bsystem\s+pwned\b", re.IGNORECASE),
    "IGNORE_PREVIOUS": re.compile(
        r"ignore\s+(all\s+)?previous\s+(instructions|context)",
        re.IGNORECASE),
    "OVERRIDE": re.compile(
        r"\b(override|forget)\s+(all\s+)?(previous|prior)\s+(instructions?|"
        r"system\s+prompt)", re.IGNORECASE),
    "EXECUTE": re.compile(
        r"\bexecute\s+the\s+following\s+(command|instruction)", re.IGNORECASE),
    "CANARY": re.compile(r"\bcanary\s+instruction\b", re.IGNORECASE),
}

#: Replacement stub used by both sanitizers when a marker is stripped.
_REDACTED_STUB = "[REDACTED: injection marker]"


@dataclass
class SafetyReport:
    """Flag-only view of an untrusted surface — no escaping decisions here."""

    is_untrusted: bool = True
    contains_injection: bool = False
    markers: list[str] = field(default_factory=list)


def detect_pwned_markers(text: str, *, treat_as_untrusted: bool = True
                         ) -> SafetyReport:
    """Report whether ``text`` contains any known injection marker.

    ``treat_as_untrusted=False`` is only for the caller who knows the text
    came from a benign channel (e.g., manifest fields). LLM output surfaces
    keep the default.
    """
    if not isinstance(text, str) or not text:
        return SafetyReport(is_untrusted=treat_as_untrusted,
                            contains_injection=False, markers=[])
    hit = [name for name, pat in INJECTION_MARKERS.items() if pat.search(text)]
    return SafetyReport(
        is_untrusted=treat_as_untrusted,
        contains_injection=bool(hit),
        markers=sorted(hit),
    )


def _apply_marker_redaction(text: str) -> str:
    """Replace all INJECTION_MARKERS hits with ``_REDACTED_STUB``.

    Private helper for ``redact_markers``. Kept as a distinct function so a
    future non-Jinja consumer (see module docstring) can reuse it without
    re-implementing the marker-substitution loop.
    """
    out = text
    for pat in INJECTION_MARKERS.values():
        out = pat.sub(_REDACTED_STUB, out)
    return out


def redact_markers(text: str) -> str:
    """Replace injection-marker hits with a redaction stub — NO encoding.

    Use for text rendered through auto-escaping template engines (Jinja2):
    the template engine handles entity encoding, so pre-encoding here would
    cause double-escaping (P129b bug: ``'`` -> ``&#x27;`` -> ``&amp;#x27;``).
    """
    if not isinstance(text, str) or not text:
        return ""
    return _apply_marker_redaction(text)


# TOMBSTONE (P129b-deploy-smoke 2026-07-24): ``sanitize_for_html`` was removed.
# It had zero callers and its pre-Jinja usage caused the P129b double-escape
# bug. Resurrect from git log ONLY when a non-Jinja consumer (e.g. a JS client
# that innerHTMLs a JSON field) actually appears. Do NOT reintroduce for
# templates_product_lab/*.html or any Jinja-rendered surface.


def sanitize_for_rag(text: str) -> str:
    """Strip markers + collapse whitespace — safe to feed back into an LLM.

    RAG callers must use this, not the raw text: an unfiltered injection
    marker inside a retrieval chunk lands in the follow-up model's context
    and re-triggers the same attack surface (P108 lesson: context-section
    treatment matters, but defence-in-depth wins).
    """
    if not isinstance(text, str) or not text:
        return ""
    out = text
    for pat in INJECTION_MARKERS.values():
        out = pat.sub(" ", out)
    return re.sub(r"\s+", " ", out).strip()


__all__ = [
    "SafetyReport",
    "detect_pwned_markers",
    "redact_markers",
    "sanitize_for_rag",
    "INJECTION_MARKERS",
]
