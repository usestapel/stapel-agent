"""Validate a completed AssemblyAI transcript BEFORE mapping.

Structural + timestamp sanity checks on the provider payload. Nothing raises;
every problem is returned as an ``AAIValidationIssue`` (severity ``error`` |
``warning``) so the runner can gate on errors and surface warnings. Mirrors
``elevenlabs_validate``.

Version: 1.0 · Date: 04 Jul 2026
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class AAIValidationIssue(BaseModel):
    """One problem found in a raw AssemblyAI response (collected, never raised)."""

    severity: Literal["warning", "error"]
    code: str
    message: str


def validate_response(
    raw: dict, *, expect_diarization: bool = True
) -> list[AAIValidationIssue]:
    """Check the structure and timestamps of a completed AssemblyAI transcript.

    Checks:
    - NOT_COMPLETED (error): ``status`` is not "completed".
    - EMPTY_TEXT (error): ``text`` missing/blank.
    - NO_WORDS (error): ``words`` absent or empty.
    - NO_UTTERANCES (error, only when diarization expected): no ``utterances``.
    - NEGATIVE_TIMESTAMP (error): a word start/end < 0.
    - TIMESTAMP_ORDER (error): a word end < start.
    - UNTIMED_WORD (warning): a word with null start/end (dropped by mapper).
    - NON_MONOTONIC (warning): a word starts before the previous word.
    - NO_LANGUAGE_CODE (warning): ``language_code`` missing.
    - NO_SPEAKER (warning): diarization expected but no speaker on any word.

    Returns an empty list when the response is clean.
    """
    issues: list[AAIValidationIssue] = []

    status = raw.get("status")
    if status != "completed":
        issues.append(AAIValidationIssue(
            severity="error", code="NOT_COMPLETED",
            message=f"transcript status is {status!r}, not 'completed'"))

    text = raw.get("text")
    if not text or not str(text).strip():
        issues.append(AAIValidationIssue(
            severity="error", code="EMPTY_TEXT",
            message="response 'text' is missing or blank"))

    words = raw.get("words")
    if not isinstance(words, list) or not words:
        issues.append(AAIValidationIssue(
            severity="error", code="NO_WORDS",
            message="response has no non-empty 'words' array"))
        return issues  # nothing further to check

    if expect_diarization and not (raw.get("utterances") or []):
        issues.append(AAIValidationIssue(
            severity="error", code="NO_UTTERANCES",
            message="diarization expected but 'utterances' is absent/empty"))

    untimed = negative = inverted = 0
    for w in words:
        if not isinstance(w, dict):
            continue
        s, e = w.get("start"), w.get("end")
        if s is None or e is None:
            untimed += 1
            continue
        if s < 0 or e < 0:
            negative += 1
        if e < s:
            inverted += 1
    if negative:
        issues.append(AAIValidationIssue(
            severity="error", code="NEGATIVE_TIMESTAMP",
            message=f"{negative} word(s) with negative start/end"))
    if inverted:
        issues.append(AAIValidationIssue(
            severity="error", code="TIMESTAMP_ORDER",
            message=f"{inverted} word(s) with end < start"))
    if untimed:
        issues.append(AAIValidationIssue(
            severity="warning", code="UNTIMED_WORD",
            message=f"{untimed} word(s) missing start/end (dropped by mapper)"))

    timed = [
        (w["start"], w["end"]) for w in words
        if isinstance(w, dict)
        and w.get("start") is not None and w.get("end") is not None
    ]
    non_mono = sum(1 for i in range(1, len(timed)) if timed[i][0] < timed[i - 1][0])
    if non_mono:
        issues.append(AAIValidationIssue(
            severity="warning", code="NON_MONOTONIC",
            message=f"{non_mono} word(s) start before the previous word"))

    if not raw.get("language_code"):
        issues.append(AAIValidationIssue(
            severity="warning", code="NO_LANGUAGE_CODE",
            message="response missing 'language_code'"))

    if expect_diarization:
        speakers = {w.get("speaker") for w in words
                    if isinstance(w, dict) and w.get("speaker")}
        if not speakers:
            issues.append(AAIValidationIssue(
                severity="warning", code="NO_SPEAKER",
                message="diarization expected but no word carries a speaker"))

    return issues
