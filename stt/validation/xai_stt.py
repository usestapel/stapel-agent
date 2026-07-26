"""Validate a raw xAI STT payload BEFORE mapping.

Structural + timestamp sanity checks on the provider payload. Nothing raises;
every problem is returned as an ``XaiSttValidationIssue`` (severity
``error`` | ``warning``) so a runner can gate on errors and surface warnings.
Mirrors ``speechmatics_validate`` / ``gladia_validate``.

xAI specifics (docs verified 2026-07-10, rest-api-reference/inference/voice):
  - the payload is the SYNCHRONOUS POST /v1/stt body: {text, language,
    duration, words[], channels[]?} — no job wrapper, no status field;
  - words[] is FLAT with SECONDS times; ``confidence`` is entropy-based and
    OMITTED when 0; ``speaker`` is an INT present only when diarize=true;
  - ``language`` is documented as CURRENTLY always "" ("language detection is
    not yet enabled") — an empty language is EXPECTED, not even a warning;
  - ``channels[]`` appears only for multichannel=true, which this adapter
    never sends — its presence is flagged.

Version: 1.0 · Date: 10 Jul 2026 (P76)
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class XaiSttValidationIssue(BaseModel):
    """One problem found in a raw xAI STT response (collected, never raised)."""

    severity: Literal["warning", "error"]
    code: str
    message: str


def validate_response(
    raw: dict, *, expect_diarization: bool = True
) -> list[XaiSttValidationIssue]:
    """Check the structure and timestamps of an xAI STT payload.

    Checks:
    - NO_TEXT (error): neither ``text`` nor any word text present.
    - NO_WORDS (warning): no ``words[]`` — the docs allow omission when
      empty; the mapper falls back to one unattributed turn.
    - NEGATIVE_TIMESTAMP (error): a word start/end < 0.
    - TIMESTAMP_ORDER (error): a word end < start.
    - UNTIMED_WORD (warning): a word with null start/end (dropped by mapper).
    - NO_SPEAKER (warning): diarization expected but no word carries an int
      speaker (0 IS a valid speaker — tested with ``is not None``).
    - NO_CONFIDENCE (warning): no word carries a confidence (the field is
      omitted-when-0 by contract, so all-absent is possible but worth eyes).
    - UNEXPECTED_CHANNELS (warning): a ``channels[]`` array is present — this
      adapter never requests multichannel.

    Returns an empty list when the response is clean.
    """
    issues: list[XaiSttValidationIssue] = []

    words = raw.get("words")
    words = words if isinstance(words, list) else []
    text = (raw.get("text") or "").strip()

    if not text and not any(
            isinstance(w, dict) and (w.get("text") or "").strip()
            for w in words):
        issues.append(XaiSttValidationIssue(
            severity="error", code="NO_TEXT",
            message="response has no transcript text and no word text"))

    if not words:
        issues.append(XaiSttValidationIssue(
            severity="warning", code="NO_WORDS",
            message="no words[] in the response (omitted-when-empty is "
                    "documented; word-level metrics are unavailable)"))

    untimed = negative = inverted = 0
    speakers_seen = confidences_seen = False
    for w in words:
        if not isinstance(w, dict):
            continue
        start, end = w.get("start"), w.get("end")
        if start is None or end is None:
            untimed += 1
        else:
            if start < 0 or end < 0:
                negative += 1
            if end < start:
                inverted += 1
        if w.get("speaker") is not None:   # 0 is a valid speaker
            speakers_seen = True
        if w.get("confidence") is not None:
            confidences_seen = True

    if negative:
        issues.append(XaiSttValidationIssue(
            severity="error", code="NEGATIVE_TIMESTAMP",
            message=f"{negative} word(s) with negative start/end"))
    if inverted:
        issues.append(XaiSttValidationIssue(
            severity="error", code="TIMESTAMP_ORDER",
            message=f"{inverted} word(s) with end < start"))
    if untimed:
        issues.append(XaiSttValidationIssue(
            severity="warning", code="UNTIMED_WORD",
            message=f"{untimed} word(s) missing start/end (dropped by mapper)"))

    if expect_diarization and words and not speakers_seen:
        issues.append(XaiSttValidationIssue(
            severity="warning", code="NO_SPEAKER",
            message="diarization expected but no word carries a speaker "
                    "(int; 0 is valid)"))
    if words and not confidences_seen:
        issues.append(XaiSttValidationIssue(
            severity="warning", code="NO_CONFIDENCE",
            message="no word carries a confidence (the field is omitted "
                    "when 0 by contract)"))

    if raw.get("channels"):
        issues.append(XaiSttValidationIssue(
            severity="warning", code="UNEXPECTED_CHANNELS",
            message="channels[] present but this adapter never requests "
                    "multichannel — check the request that produced this"))

    return issues
