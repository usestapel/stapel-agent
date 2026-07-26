"""Validate a done Gladia pre-recorded payload BEFORE mapping.

Structural + timestamp sanity checks on the provider payload. Nothing raises;
every problem is returned as a ``GladiaValidationIssue`` (severity ``error`` |
``warning``) so a runner can gate on errors and surface warnings. Mirrors
``assemblyai_validate`` / ``elevenlabs_validate``.

Gladia specifics (schema verified 2026-07-09):
  - terminal statuses are "done" | "error" (NOT AssemblyAI's "completed");
  - utterances live under ``result.transcription.utterances``;
  - times are SECONDS (float); the utterance ``speaker`` is an INT present
    only when diarization was requested (0 is valid and falsy).

Version: 1.0 · Date: 09 Jul 2026 (P66)
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class GladiaValidationIssue(BaseModel):
    """One problem found in a raw Gladia response (collected, never raised)."""

    severity: Literal["warning", "error"]
    code: str
    message: str


def validate_response(
    raw: dict, *, expect_diarization: bool = True
) -> list[GladiaValidationIssue]:
    """Check the structure and timestamps of a done Gladia transcription.

    Checks:
    - NOT_DONE (error): ``status`` is not "done".
    - NO_TRANSCRIPTION (error): ``result.transcription`` missing.
    - EMPTY_TEXT (error): ``full_transcript`` missing/blank.
    - NO_UTTERANCES (error): ``utterances`` absent or empty.
    - NEGATIVE_TIMESTAMP (error): an utterance/word start/end < 0.
    - TIMESTAMP_ORDER (error): an utterance/word end < start.
    - UNTIMED_WORD (warning): a word with null start/end (dropped by mapper).
    - NO_LANGUAGE (warning): ``languages`` missing/empty.
    - NO_SPEAKER (warning): diarization expected but no utterance carries a
      speaker (speaker is an INT; 0 counts as present).

    Returns an empty list when the response is clean.
    """
    issues: list[GladiaValidationIssue] = []

    status = raw.get("status")
    if status != "done":
        issues.append(GladiaValidationIssue(
            severity="error", code="NOT_DONE",
            message=f"transcription status is {status!r}, not 'done'"))

    tr = (raw.get("result") or {}).get("transcription")
    if not isinstance(tr, dict):
        issues.append(GladiaValidationIssue(
            severity="error", code="NO_TRANSCRIPTION",
            message="response has no result.transcription object"))
        return issues  # nothing further to check

    text = tr.get("full_transcript")
    if not text or not str(text).strip():
        issues.append(GladiaValidationIssue(
            severity="error", code="EMPTY_TEXT",
            message="result.transcription.full_transcript is missing or blank"))

    utterances = tr.get("utterances")
    if not isinstance(utterances, list) or not utterances:
        issues.append(GladiaValidationIssue(
            severity="error", code="NO_UTTERANCES",
            message="result.transcription has no non-empty 'utterances' array"))
        return issues

    untimed = negative = inverted = 0
    for utt in utterances:
        if not isinstance(utt, dict):
            continue
        spans = [(utt.get("start"), utt.get("end"))]
        spans += [(w.get("start"), w.get("end"))
                  for w in (utt.get("words") or []) if isinstance(w, dict)]
        for s, e in spans:
            if s is None or e is None:
                untimed += 1
                continue
            if s < 0 or e < 0:
                negative += 1
            if e < s:
                inverted += 1
    if negative:
        issues.append(GladiaValidationIssue(
            severity="error", code="NEGATIVE_TIMESTAMP",
            message=f"{negative} span(s) with negative start/end"))
    if inverted:
        issues.append(GladiaValidationIssue(
            severity="error", code="TIMESTAMP_ORDER",
            message=f"{inverted} span(s) with end < start"))
    if untimed:
        issues.append(GladiaValidationIssue(
            severity="warning", code="UNTIMED_WORD",
            message=f"{untimed} span(s) missing start/end (dropped by mapper)"))

    if not (tr.get("languages") or []):
        issues.append(GladiaValidationIssue(
            severity="warning", code="NO_LANGUAGE",
            message="result.transcription.languages is missing/empty"))

    if expect_diarization:
        speakers = {utt.get("speaker") for utt in utterances
                    if isinstance(utt, dict) and utt.get("speaker") is not None}
        if not speakers:
            issues.append(GladiaValidationIssue(
                severity="warning", code="NO_SPEAKER",
                message="diarization expected but no utterance carries a "
                        "speaker (int; 0 counts as present)"))

    return issues
