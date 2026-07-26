"""Validate a raw pyannoteAI diarization job payload BEFORE mapping.

Structural + timestamp sanity checks on the provider payload. Nothing raises;
every problem is returned as a ``PyannoteDiarValidationIssue`` (severity
``error`` | ``warning``) so a runner can gate on errors and surface warnings.
Mirrors ``gigaam_home_validate`` / ``xai_stt_validate``.

pyannote specifics (contract verified 2026-07-11, DiarizationJobOutput):
  - the payload is the polled GET /v1/jobs/{id} body of a SUCCEEDED job:
    {jobId, status, output: {diarization[], exclusiveDiarization?}};
  - ``output.diarization`` is REQUIRED by the schema; an EMPTY list on a
    successful job means no speech was attributed — a hybrid merge cannot
    assign any speaker, so emptiness is an ERROR here (the hybrid gate),
    not a curiosity;
  - segments are {speaker: str, start: s-float, end: s-float}; a per-turn
    ``confidence`` map appears only when turnLevelConfidence was requested
    (we don't request it — its presence is a drift warning, not an error);
  - ``exclusiveDiarization`` (requested via ``exclusive: true``) must not
    contain overlapping segments — that is its defining property; overlap
    there is a contract DRIFT worth eyes.

Version: 1.0 · Date: 11 Jul 2026 (P84)
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class PyannoteDiarValidationIssue(BaseModel):
    """One problem found in a raw pyannote job payload (collected, never raised)."""

    severity: Literal["warning", "error"]
    code: str
    message: str


def _issue(severity: str, code: str, message: str) -> PyannoteDiarValidationIssue:
    return PyannoteDiarValidationIssue(
        severity=severity, code=code, message=message)


def _check_segments(segments: list, layer: str,
                    issues: list[PyannoteDiarValidationIssue]) -> None:
    for i, seg in enumerate(segments):
        if not isinstance(seg, dict):
            issues.append(_issue(
                "error", f"{layer}_segment_not_object",
                f"{layer}[{i}] is not an object"))
            continue
        if not seg.get("speaker"):
            issues.append(_issue(
                "error", f"{layer}_missing_speaker",
                f"{layer}[{i}] has no speaker label"))
        start, end = seg.get("start"), seg.get("end")
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            issues.append(_issue(
                "error", f"{layer}_bad_times",
                f"{layer}[{i}] start/end are not numbers"))
            continue
        if end < start:
            issues.append(_issue(
                "warning", f"{layer}_inverted_times",
                f"{layer}[{i}] end {end} < start {start}"))


def validate_response(raw: dict) -> list[PyannoteDiarValidationIssue]:
    """Check the structure and timestamps of a succeeded diarization payload."""
    issues: list[PyannoteDiarValidationIssue] = []

    status = raw.get("status")
    if status != "succeeded":
        issues.append(_issue(
            "error", "not_succeeded",
            f"job status is {status!r}, expected 'succeeded'"))

    output = raw.get("output")
    if not isinstance(output, dict):
        issues.append(_issue("error", "missing_output", "no output object"))
        return issues

    if output.get("error"):
        issues.append(_issue(
            "error", "output_error", f"output.error: {output['error']}"))
    if output.get("warning"):
        issues.append(_issue(
            "warning", "output_warning", f"output.warning: {output['warning']}"))

    diarization = output.get("diarization")
    if not isinstance(diarization, list):
        issues.append(_issue(
            "error", "missing_diarization",
            "output.diarization is absent or not a list (schema-required)"))
        return issues
    if not diarization:
        issues.append(_issue(
            "error", "empty_diarization",
            "output.diarization is empty — a hybrid merge cannot assign any "
            "speaker to the STT words"))
    _check_segments(diarization, "diarization", issues)

    if any(isinstance(s, dict) and "confidence" in s for s in diarization):
        issues.append(_issue(
            "warning", "unexpected_turn_confidence",
            "per-turn confidence present although turnLevelConfidence was "
            "not requested — contract drift worth eyes"))

    exclusive = output.get("exclusiveDiarization")
    if exclusive is not None:
        if not isinstance(exclusive, list):
            issues.append(_issue(
                "error", "bad_exclusive_layer",
                "output.exclusiveDiarization is not a list"))
        else:
            _check_segments(exclusive, "exclusiveDiarization", issues)
            # Non-overlap is the DEFINING property of the exclusive layer.
            timeline = sorted(
                ((float(s.get("start", 0)), float(s.get("end", 0)))
                 for s in exclusive if isinstance(s, dict)
                 and isinstance(s.get("start"), (int, float))
                 and isinstance(s.get("end"), (int, float))),
                key=lambda t: t[0])
            for (s1, e1), (s2, _e2) in zip(timeline, timeline[1:]):
                if s2 < e1:
                    issues.append(_issue(
                        "warning", "exclusive_overlap",
                        f"exclusiveDiarization overlaps itself around "
                        f"{s2:.3f}s — contract drift worth eyes"))
                    break

    return issues
