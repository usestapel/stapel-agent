"""Validate a raw ElevenLabs Scribe v2 response BEFORE mapping.

Structural + timestamp sanity checks on the provider payload. Nothing raises;
every problem is returned as an ``ELValidationIssue`` (severity ``error`` |
``warning``) so the runner can gate on errors and surface warnings. Mirrors the
pattern of ``pipeline.summarize.validate``.

Version: 1.0 · Date: 04 Jul 2026
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class ELValidationIssue(BaseModel):
    """One problem found in a raw ElevenLabs response (collected, never raised)."""

    severity: Literal["warning", "error"]
    code: str
    message: str


def validate_response(
    raw: dict, *, expect_diarization: bool = True
) -> list[ELValidationIssue]:
    """Check the structure and timestamps of a raw ElevenLabs STT response.

    Checks:
    - EMPTY_TEXT (error): ``text`` missing/blank.
    - NO_WORDS (error): ``words`` absent or empty.
    - NO_WORD_TOKENS (error): no ``type='word'`` tokens.
    - NEGATIVE_TIMESTAMP (error): a word start/end < 0.
    - TIMESTAMP_ORDER (error): a word end < start.
    - UNTIMED_WORD (warning): a word token with null start/end (dropped by mapper).
    - NON_MONOTONIC (warning): a word starts before the previous word.
    - NO_LANGUAGE_CODE (warning): ``language_code`` missing.
    - NO_SPEAKER_ID (warning): diarization requested but no ``speaker_id`` present.

    Returns an empty list when the response is clean.
    """
    issues: list[ELValidationIssue] = []

    text = raw.get("text")
    if not text or not str(text).strip():
        issues.append(ELValidationIssue(
            severity="error", code="EMPTY_TEXT",
            message="response 'text' is missing or blank"))

    words = raw.get("words")
    if not isinstance(words, list) or not words:
        issues.append(ELValidationIssue(
            severity="error", code="NO_WORDS",
            message="response has no non-empty 'words' array"))
        return issues  # nothing further to check

    word_toks = [w for w in words if isinstance(w, dict) and w.get("type") == "word"]
    if not word_toks:
        issues.append(ELValidationIssue(
            severity="error", code="NO_WORD_TOKENS",
            message="'words' has no type='word' tokens"))
        return issues

    untimed = negative = inverted = 0
    for w in word_toks:
        s, e = w.get("start"), w.get("end")
        if s is None or e is None:
            untimed += 1
            continue
        if s < 0 or e < 0:
            negative += 1
        if e < s:
            inverted += 1
    if negative:
        issues.append(ELValidationIssue(
            severity="error", code="NEGATIVE_TIMESTAMP",
            message=f"{negative} word(s) with negative start/end"))
    if inverted:
        issues.append(ELValidationIssue(
            severity="error", code="TIMESTAMP_ORDER",
            message=f"{inverted} word(s) with end < start"))
    if untimed:
        issues.append(ELValidationIssue(
            severity="warning", code="UNTIMED_WORD",
            message=f"{untimed} word token(s) missing start/end (dropped by mapper)"))

    timed = [
        (w["start"], w["end"]) for w in word_toks
        if w.get("start") is not None and w.get("end") is not None
    ]
    non_mono = sum(1 for i in range(1, len(timed)) if timed[i][0] < timed[i - 1][0])
    if non_mono:
        issues.append(ELValidationIssue(
            severity="warning", code="NON_MONOTONIC",
            message=f"{non_mono} word(s) start before the previous word"))

    if not raw.get("language_code"):
        issues.append(ELValidationIssue(
            severity="warning", code="NO_LANGUAGE_CODE",
            message="response missing 'language_code'"))

    if expect_diarization:
        speakers = {w.get("speaker_id") for w in word_toks if w.get("speaker_id")}
        if not speakers:
            issues.append(ELValidationIssue(
                severity="warning", code="NO_SPEAKER_ID",
                message="diarize requested but no speaker_id present"))

    return issues
