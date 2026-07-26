"""Validate a composite Soniox raw payload BEFORE mapping.

Structural + timestamp sanity checks on the provider payload. Nothing raises;
every problem is returned as a ``SonioxValidationIssue`` (severity
``error`` | ``warning``) so a runner can gate on errors and surface warnings.
Mirrors ``speechmatics_validate`` / ``gladia_validate`` / ``xai_stt_validate``.

Soniox specifics (OpenAPI verified 2026-07-10):
  - the payload here is the adapter's COMPOSITE raw: ``transcription`` (the
    final job object) + ``transcript`` ({id, text, tokens[]}) — job status
    was already checked by the adapter (terminal: completed | error/failed);
  - tokens are SUB-WORD with int MILLISECOND times and a 0..1 confidence;
  - ``speaker`` is a STRING "1".."15" present only with diarization;
    ``language`` only with language identification;
  - the job object should ECHO the requested ``model`` — a mismatch against
    the adapter's pin is flagged (attribution risk, the AAI 'best' lesson).

Version: 1.0 · Date: 10 Jul 2026 (P76)
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class SonioxValidationIssue(BaseModel):
    """One problem found in a raw Soniox response (collected, never raised)."""

    severity: Literal["warning", "error"]
    code: str
    message: str


def validate_response(
    raw: dict, *, expect_diarization: bool = True,
    expected_model: str | None = "stt-async-v5",
) -> list[SonioxValidationIssue]:
    """Check the structure and timestamps of a composite Soniox payload.

    Checks:
    - NO_TOKENS (error): ``transcript.tokens`` absent or empty.
    - NO_TEXT (warning): no top-level transcript text (mapper rebuilds it).
    - NEGATIVE_TIMESTAMP (error): a token start_ms/end_ms < 0.
    - TIMESTAMP_ORDER (error): a token end_ms < start_ms.
    - UNTIMED_TOKEN (warning): a token with null start_ms/end_ms (the schema
      requires them; dropped by the mapper if ever absent).
    - NO_SPEAKER (warning): diarization expected but no token carries a
      speaker.
    - NO_LANGUAGE (warning): no token carries a language label (LID is
      always requested by this adapter).
    - NO_CONFIDENCE (warning): no token carries a confidence (the docs say
      it is always included).
    - MODEL_MISMATCH (warning): the job object's ``model`` echo differs from
      the adapter's pin (attribution risk).

    Returns an empty list when the response is clean.
    """
    issues: list[SonioxValidationIssue] = []

    job = raw.get("transcription") or {}
    transcript = raw.get("transcript") or {}
    if not transcript and "tokens" in raw:
        transcript = raw          # defensive: a bare transcript payload

    tokens = transcript.get("tokens")
    if not isinstance(tokens, list) or not tokens:
        issues.append(SonioxValidationIssue(
            severity="error", code="NO_TOKENS",
            message="transcript has no non-empty 'tokens' array"))
        return issues

    if not (transcript.get("text") or "").strip():
        issues.append(SonioxValidationIssue(
            severity="warning", code="NO_TEXT",
            message="transcript has no top-level text (mapper will rebuild "
                    "it from tokens)"))

    untimed = negative = inverted = 0
    speakers_seen = languages_seen = confidences_seen = False
    for tok in tokens:
        if not isinstance(tok, dict):
            continue
        start, end = tok.get("start_ms"), tok.get("end_ms")
        if start is None or end is None:
            untimed += 1
        else:
            if start < 0 or end < 0:
                negative += 1
            if end < start:
                inverted += 1
        if tok.get("speaker"):
            speakers_seen = True
        if tok.get("language"):
            languages_seen = True
        if tok.get("confidence") is not None:
            confidences_seen = True

    if negative:
        issues.append(SonioxValidationIssue(
            severity="error", code="NEGATIVE_TIMESTAMP",
            message=f"{negative} token(s) with negative start_ms/end_ms"))
    if inverted:
        issues.append(SonioxValidationIssue(
            severity="error", code="TIMESTAMP_ORDER",
            message=f"{inverted} token(s) with end_ms < start_ms"))
    if untimed:
        issues.append(SonioxValidationIssue(
            severity="warning", code="UNTIMED_TOKEN",
            message=f"{untimed} token(s) missing start_ms/end_ms (dropped "
                    f"by mapper)"))

    if expect_diarization and not speakers_seen:
        issues.append(SonioxValidationIssue(
            severity="warning", code="NO_SPEAKER",
            message="diarization expected but no token carries a speaker"))
    if not languages_seen:
        issues.append(SonioxValidationIssue(
            severity="warning", code="NO_LANGUAGE",
            message="no token carries a language label (LID is always "
                    "requested by this adapter)"))
    if not confidences_seen:
        issues.append(SonioxValidationIssue(
            severity="warning", code="NO_CONFIDENCE",
            message="no token carries a confidence (docs: always included)"))

    echoed = job.get("model")
    if expected_model and echoed and echoed != expected_model:
        issues.append(SonioxValidationIssue(
            severity="warning", code="MODEL_MISMATCH",
            message=f"job echoes model {echoed!r}, adapter pinned "
                    f"{expected_model!r} (attribution risk)"))

    return issues
