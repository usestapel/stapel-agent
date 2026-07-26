"""Validate a raw Deepgram Nova-3 response BEFORE mapping.

Structural + timestamp sanity checks on the provider payload. Nothing raises;
every problem is returned as a ``DGValidationIssue`` (severity ``error`` |
``warning``) so the runner can gate on errors and surface warnings. Mirrors the
pattern of ``pipeline.adapters.elevenlabs_validate``.

Note the ``speaker`` trap: a Deepgram speaker is an INTEGER starting at 0, so
``speaker == 0`` is a VALID speaker that is falsy — every presence check here uses
``is not None`` (never truthiness).

Version: 1.0 · Date: 05 Jul 2026
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class DGValidationIssue(BaseModel):
    """One problem found in a raw Deepgram response (collected, never raised)."""

    severity: Literal["warning", "error"]
    code: str
    message: str


def _first_alternative(raw: dict) -> dict:
    """``results.channels[0].alternatives[0]`` or an empty dict."""
    results = raw.get("results") or {}
    channels = results.get("channels") or []
    channel = channels[0] if channels and isinstance(channels[0], dict) else {}
    alts = channel.get("alternatives") or []
    return alts[0] if alts and isinstance(alts[0], dict) else {}


def validate_response(
    raw: dict, *, expect_diarization: bool = True
) -> list[DGValidationIssue]:
    """Check the structure and timestamps of a raw Deepgram STT response.

    Checks:
    - NO_REQUEST_ID (warning): ``metadata.request_id`` missing (repro handle).
    - EMPTY_TEXT (error): ``alternatives[0].transcript`` missing/blank.
    - NO_WORDS (error): ``alternatives[0].words`` absent or empty.
    - NEGATIVE_TIMESTAMP (error): a word start/end < 0.
    - TIMESTAMP_ORDER (error): a word end < start.
    - UNTIMED_WORD (warning): a word with null start/end (dropped by the mapper).
    - NON_MONOTONIC (warning): a word starts before the previous word.
    - NO_SPEAKER (warning): diarization requested but no integer ``speaker`` present.

    Returns an empty list when the response is clean.
    """
    issues: list[DGValidationIssue] = []

    if not ((raw.get("metadata") or {}).get("request_id")):
        issues.append(DGValidationIssue(
            severity="warning", code="NO_REQUEST_ID",
            message="response metadata missing 'request_id'"))

    alt = _first_alternative(raw)

    text = alt.get("transcript")
    if not text or not str(text).strip():
        issues.append(DGValidationIssue(
            severity="error", code="EMPTY_TEXT",
            message="alternatives[0].transcript is missing or blank"))

    words = alt.get("words")
    if not isinstance(words, list) or not words:
        issues.append(DGValidationIssue(
            severity="error", code="NO_WORDS",
            message="response has no non-empty alternatives[0].words array"))
        return issues  # nothing further to check

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
        issues.append(DGValidationIssue(
            severity="error", code="NEGATIVE_TIMESTAMP",
            message=f"{negative} word(s) with negative start/end"))
    if inverted:
        issues.append(DGValidationIssue(
            severity="error", code="TIMESTAMP_ORDER",
            message=f"{inverted} word(s) with end < start"))
    if untimed:
        issues.append(DGValidationIssue(
            severity="warning", code="UNTIMED_WORD",
            message=f"{untimed} word(s) missing start/end (dropped by mapper)"))

    timed = [
        (w["start"], w["end"]) for w in words
        if isinstance(w, dict) and w.get("start") is not None
        and w.get("end") is not None
    ]
    non_mono = sum(1 for i in range(1, len(timed)) if timed[i][0] < timed[i - 1][0])
    if non_mono:
        issues.append(DGValidationIssue(
            severity="warning", code="NON_MONOTONIC",
            message=f"{non_mono} word(s) start before the previous word"))

    if expect_diarization:
        # speaker is an int starting at 0 -> check ``is not None``, never truthiness.
        speakers = {
            w.get("speaker") for w in words
            if isinstance(w, dict) and w.get("speaker") is not None
        }
        if not speakers:
            issues.append(DGValidationIssue(
                severity="warning", code="NO_SPEAKER",
                message="diarize_model requested but no integer 'speaker' present"))

    return issues
