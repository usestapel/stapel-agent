"""Provider-response validators — structural + timestamp sanity checks run on
the raw provider payload BEFORE it is mapped into the normalised transcript.

Ported verbatim from the iron-benchmark research harness
(``pipeline/adapters/*_validate.py``). Each provider's validator is its own
module because the differences between them are the measured differences
between providers — see each module's docstring for the provider-specific
notes and dated verification notes.

Public API (lazily resolved, PEP 562 — same pattern as the package root and
the other registry packages: importing this package does not import any of
the eight validator submodules until an attribute is actually accessed, so a
module-level ``from .assemblyai import ...`` here cannot re-open the 3.14
import-lock deadlock described in ``stapel_agent/__init__.py``).
"""

from __future__ import annotations

__all__ = [
    "AAIValidationIssue",
    "validate_assemblyai_response",
    "DGValidationIssue",
    "validate_deepgram_response",
    "ELValidationIssue",
    "validate_elevenlabs_response",
    "GladiaValidationIssue",
    "validate_gladia_response",
    "SonioxValidationIssue",
    "validate_soniox_response",
    "SpeechmaticsValidationIssue",
    "UNIDENTIFIED_SPEAKER",
    "validate_speechmatics_response",
    "XaiSttValidationIssue",
    "validate_xai_stt_response",
]

# name -> (relative module, attribute). Each provider module's function is
# named ``validate_response`` (verbatim, per its own docstring) — aliased
# here to a provider-qualified name since a flat package namespace cannot
# hold eight identically-named functions at once.
_EXPORTS = {
    "AAIValidationIssue": (".assemblyai", "AAIValidationIssue"),
    "validate_assemblyai_response": (".assemblyai", "validate_response"),
    "DGValidationIssue": (".deepgram", "DGValidationIssue"),
    "validate_deepgram_response": (".deepgram", "validate_response"),
    "ELValidationIssue": (".elevenlabs", "ELValidationIssue"),
    "validate_elevenlabs_response": (".elevenlabs", "validate_response"),
    "GladiaValidationIssue": (".gladia", "GladiaValidationIssue"),
    "validate_gladia_response": (".gladia", "validate_response"),
    "SonioxValidationIssue": (".soniox", "SonioxValidationIssue"),
    "validate_soniox_response": (".soniox", "validate_response"),
    "SpeechmaticsValidationIssue": (".speechmatics", "SpeechmaticsValidationIssue"),
    "UNIDENTIFIED_SPEAKER": (".speechmatics", "UNIDENTIFIED_SPEAKER"),
    "validate_speechmatics_response": (".speechmatics", "validate_response"),
    "XaiSttValidationIssue": (".xai_stt", "XaiSttValidationIssue"),
    "validate_xai_stt_response": (".xai_stt", "validate_response"),
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
