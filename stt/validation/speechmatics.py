"""Validate a Speechmatics batch transcript payload BEFORE mapping.

Structural + timestamp sanity checks on the provider payload. Nothing raises;
every problem is returned as a ``SpeechmaticsValidationIssue`` (severity
``error`` | ``warning``) so a runner can gate on errors and surface warnings.
Mirrors ``gladia_validate`` / ``assemblyai_validate``.

Speechmatics specifics (docs verified 2026-07-10):
  - the payload here is the GET /v2/jobs/{id}/transcript JSON (format "2.x"),
    NOT the poll body — job status was already checked by the adapter
    (terminal statuses: done | rejected | deleted; a transcript only exists
    for done);
  - ``results[]`` is a FLAT stream of word / punctuation / entity items with
    SECONDS times; there are no provider utterances;
  - the speaker lives on ``alternatives[0].speaker`` as a STRING: "S1", "S2"
    ... by order of appearance, or "UU" when the speaker cannot be identified
    (docs: batch-diarization) — "UU" does NOT count as an attributed speaker;
  - unrecognized result types may appear and can be ignored (schema note).

Version: 1.0 · Date: 10 Jul 2026 (P73)
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

#: Speaker label meaning "could not be identified" (never a real speaker).
UNIDENTIFIED_SPEAKER = "UU"


class SpeechmaticsValidationIssue(BaseModel):
    """One problem found in a raw Speechmatics transcript (collected, never raised)."""

    severity: Literal["warning", "error"]
    code: str
    message: str


def validate_response(
    raw: dict, *, expect_diarization: bool = True
) -> list[SpeechmaticsValidationIssue]:
    """Check the structure and timestamps of a Speechmatics transcript payload.

    Checks:
    - NO_RESULTS (error): ``results`` absent or empty.
    - NO_WORDS (error): no item of type "word" (punctuation-only payload).
    - NEGATIVE_TIMESTAMP (error): a result start/end < 0.
    - TIMESTAMP_ORDER (error): a result end < start.
    - UNTIMED_RESULT (warning): a result with null start/end (dropped by mapper).
    - NO_ALTERNATIVES (warning): word items without a non-empty alternatives[]
      (they carry no text and are dropped by the mapper).
    - NO_LANGUAGE (warning): no word carries a language tag.
    - NO_SPEAKER (warning): diarization expected but no word carries an
      attributed speaker ("UU" = unidentified, does not count).

    Returns an empty list when the response is clean.
    """
    issues: list[SpeechmaticsValidationIssue] = []

    results = raw.get("results")
    if not isinstance(results, list) or not results:
        issues.append(SpeechmaticsValidationIssue(
            severity="error", code="NO_RESULTS",
            message="transcript has no non-empty 'results' array"))
        return issues

    words = untimed = negative = inverted = no_alts = 0
    languages_seen = False
    attributed_speakers: set[str] = set()
    for item in results:
        if not isinstance(item, dict):
            continue
        start, end = item.get("start_time"), item.get("end_time")
        if start is None or end is None:
            untimed += 1
        else:
            if start < 0 or end < 0:
                negative += 1
            if end < start:
                inverted += 1
        if item.get("type") != "word":
            continue
        words += 1
        alts = item.get("alternatives") or []
        alt = alts[0] if alts and isinstance(alts[0], dict) else None
        if alt is None:
            no_alts += 1
            continue
        if alt.get("language"):
            languages_seen = True
        speaker = alt.get("speaker")
        if speaker and speaker != UNIDENTIFIED_SPEAKER:
            attributed_speakers.add(speaker)

    if not words:
        issues.append(SpeechmaticsValidationIssue(
            severity="error", code="NO_WORDS",
            message="results contain no 'word' items"))
    if negative:
        issues.append(SpeechmaticsValidationIssue(
            severity="error", code="NEGATIVE_TIMESTAMP",
            message=f"{negative} result(s) with negative start/end"))
    if inverted:
        issues.append(SpeechmaticsValidationIssue(
            severity="error", code="TIMESTAMP_ORDER",
            message=f"{inverted} result(s) with end < start"))
    if untimed:
        issues.append(SpeechmaticsValidationIssue(
            severity="warning", code="UNTIMED_RESULT",
            message=f"{untimed} result(s) missing start/end (dropped by mapper)"))
    if no_alts:
        issues.append(SpeechmaticsValidationIssue(
            severity="warning", code="NO_ALTERNATIVES",
            message=f"{no_alts} word(s) without alternatives (no text; dropped)"))
    if words and not languages_seen:
        issues.append(SpeechmaticsValidationIssue(
            severity="warning", code="NO_LANGUAGE",
            message="no word carries a language tag"))

    if expect_diarization and words and not attributed_speakers:
        issues.append(SpeechmaticsValidationIssue(
            severity="warning", code="NO_SPEAKER",
            message="diarization expected but no word carries an attributed "
                    "speaker ('UU' = unidentified does not count)"))

    return issues
