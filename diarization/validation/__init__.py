"""Provider-response validators for diarization providers — structural +
timestamp sanity checks run on the raw provider payload BEFORE it is mapped
into the normalised diarization result.

Ported verbatim from the iron-benchmark research harness
(``pipeline/adapters/pyannote_diar_validate.py``).

Public API (lazily resolved, PEP 562 — same pattern as the package root and
the other registry packages: importing this package does not import the
``pyannote`` submodule until an attribute is actually accessed, so a
module-level ``from .pyannote import ...`` here cannot re-open the 3.14
import-lock deadlock described in ``stapel_agent/__init__.py``).
"""

from __future__ import annotations

__all__ = [
    "PyannoteDiarValidationIssue",
    "validate_response",
]

# name -> (relative module, attribute)
_EXPORTS = {
    "PyannoteDiarValidationIssue": (".pyannote", "PyannoteDiarValidationIssue"),
    "validate_response": (".pyannote", "validate_response"),
}


def __getattr__(name):
    try:
        module_path, attr = _EXPORTS[name]
    except KeyError:
        raise AttributeError(
            f"module {__name__!r} has no attribute {name!r}"
        ) from None
    from importlib import import_module

    value = import_module(module_path, __name__)
    if attr is not None:
        value = getattr(value, attr)
    globals()[name] = value  # cache: subsequent lookups skip __getattr__
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))
