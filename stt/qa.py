"""Does this transcript cover the audio it was made from?

THE DEFECT THIS EXISTS FOR
    Production, 2026-09-13. A 600-second recording — a 74.31-second
    fixture looped eight times — came back with segment 3 ending at
    24.88 s and segment 4 starting at 99.32 s. Seventy-four and a half
    seconds of speech, exactly one fixture length, were simply not in the
    transcript. Every check that ran passed:

    * timestamps were MONOTONIC, because a hole does not reverse them.
      The host's QA loop compares each segment's start against the
      previous end and fails on ``start < prev_end`` — it is looking for
      OVERLAP, and a gap is the other sign of the same subtraction;
    * the last segment ended within the recording's duration, so the
      "max end in bounds" check passed — it bounds the top of the
      timeline and says nothing about what is missing underneath;
    * segments were present, speakers were present.

    A transcript covering one minute of a ninety-minute meeting passes
    all three. That is not a QA failure, it is a QA silence: nothing in
    the pipeline was ever asked the question "is any of it missing".

THE CHECK
    Inside a CONTINUOUS recording, consecutive segments are consecutive
    speech. The real distribution says how close: 94 608 measured
    word-to-word gaps (see :mod:`stapel_agent.stt.segmentation`) put p99
    at 1.58 s, and this package's own utterance boundary at 0.65 s. Five
    seconds is three times the p99 and an order of magnitude past the
    boundary — long enough that no ordinary pause reaches it, short
    enough that a lost chunk cannot hide under it.

    A gap past that threshold flags ``qa.gap`` and the verdict is NOT
    passed. It is a flag, never an exception: the transcript is already
    paid for and a partial transcript is worth more than none. What
    changes is that it arrives LABELLED.

WHAT IT IS NOT
    Not a silence detector. Long true silences exist — a break in a
    meeting, a recording left running — and this check will flag them.
    That is the trade taken deliberately: a false flag costs someone a
    look at a waveform, and a missed hole costs a customer the middle of
    their meeting. The check reports WHERE (start, end, seconds) so the
    look is cheap, and ``STAPEL_AGENT["STT_QA"]["MAX_GAP_SECONDS"] = 0``
    turns it off for a host whose audio is genuinely gappy.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

#: Silence between consecutive segments that stops reading as a pause.
#: See the module docstring for where the number comes from.
MAX_GAP_SECONDS = 5.0

#: The check's name in the report, and the key an operator greps for.
CHECK_GAP = "gap"

#: How many holes are named in the report. A transcript with four hundred
#: of them is one defect, not four hundred, and a report that grows with
#: the damage is a report that cannot be logged.
MAX_REPORTED_GAPS = 5


def max_gap_seconds() -> float:
    """``STT_QA["MAX_GAP_SECONDS"]``, or the shipped default."""
    try:
        from ..conf import agent_settings

        configured = agent_settings.STT_QA or {}
    except Exception:  # Django absent or settings not configured
        return MAX_GAP_SECONDS
    if not isinstance(configured, dict):
        return MAX_GAP_SECONDS
    try:
        return float(configured.get("MAX_GAP_SECONDS", MAX_GAP_SECONDS))
    except (TypeError, ValueError):
        logger.warning(
            "stapel-agent: STT_QA['MAX_GAP_SECONDS'] is not a number — "
            "using the default of %ss.", MAX_GAP_SECONDS,
        )
        return MAX_GAP_SECONDS


def find_gaps(segments, *, threshold: Optional[float] = None) -> list[dict]:
    """Holes longer than *threshold* between consecutive *segments*.

    *segments* is any sequence of objects or dicts carrying ``start`` and
    ``end`` in SECONDS — ``NormalizedUtterance`` as it comes off an
    adapter, or the plain dicts a transcript payload carries.

    Segments are walked in timeline order rather than list order, and the
    running edge is the maximum end seen so far: a provider that emits
    two speakers' overlapping turns back to back would otherwise show a
    "gap" between the second turn's end and the third turn's start that
    is really just an overlap the list order hid.
    """
    limit = max_gap_seconds() if threshold is None else float(threshold)
    if limit <= 0:
        return []

    spans = []
    for seg in segments or []:
        start = _number(seg, "start")
        end = _number(seg, "end")
        if start is None or end is None:
            continue
        spans.append((start, max(end, start)))
    if len(spans) < 2:
        return []
    spans.sort()

    gaps: list[dict] = []
    edge = spans[0][1]
    for start, end in spans[1:]:
        delta = start - edge
        if delta > limit:
            gaps.append(
                {"start": round(edge, 3), "end": round(start, 3),
                 "seconds": round(delta, 3)}
            )
        edge = max(edge, end)
    return gaps


def _number(seg, field: str) -> Optional[float]:
    value = seg.get(field) if isinstance(seg, dict) else getattr(seg, field, None)
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


#: Audio at least this long that comes back with no words and no utterances
#: is not a transcript, it is a provider that did not deliver. Five seconds
#: is the same order as MAX_GAP_SECONDS: below it, "nobody said anything"
#: is a credible answer for a clip.
EMPTY_TRANSCRIPT_MIN_MS = 5000

#: The retryable reason the router records for it in the attempts ledger.
REASON_EMPTY = "empty"


def empty_transcript_min_ms() -> int:
    """``STT_QA["EMPTY_TRANSCRIPT_MIN_MS"]``, or the shipped default. 0 disables."""
    try:
        from ..conf import agent_settings

        configured = agent_settings.STT_QA or {}
    except Exception:  # Django absent or settings not configured
        return EMPTY_TRANSCRIPT_MIN_MS
    if not isinstance(configured, dict):
        return EMPTY_TRANSCRIPT_MIN_MS
    try:
        return int(configured.get("EMPTY_TRANSCRIPT_MIN_MS", EMPTY_TRANSCRIPT_MIN_MS))
    except (TypeError, ValueError):
        return EMPTY_TRANSCRIPT_MIN_MS


def is_empty_for_audio(transcript, audio_ms: Optional[int]) -> bool:
    """Is *transcript* empty for audio long enough that empty is a failure?

    Empty means no utterances AND no words. *audio_ms* is what was
    SUBMITTED (caller-stated or probed), never the provider's own duration
    — that number is the last word's end for several adapters, and an
    empty transcript would report itself as zero seconds of audio and pass.
    Unmeasured audio (``None``) cannot be judged and is accepted.
    """
    if isinstance(transcript, dict):
        utterances = transcript.get("utterances") or []
        words = transcript.get("words") or []
    else:
        utterances = getattr(transcript, "utterances", None) or []
        words = getattr(transcript, "words", None) or []
    if utterances or words:
        return False
    floor = empty_transcript_min_ms()
    if floor <= 0 or audio_ms is None:
        return False
    try:
        return int(audio_ms) >= floor
    except (TypeError, ValueError):
        return False


def transcript_qa(transcript, *, threshold: Optional[float] = None) -> dict:
    """The QA verdict for one transcript.

    *transcript* is a ``NormalizedTranscript`` or the dict form of one.
    Returns ``{"passed": bool, "checks": {...}}`` — the same two-key shape
    ``stapel_recordings.transcript_schema.run_qa`` already produces, so a
    host merges the two reports rather than choosing between them. Each
    check's value is a string starting ``PASS`` / ``FAIL`` / ``SKIP``, and
    only ``FAIL`` clears ``passed``.

    Never raises. A QA routine that can throw turns a transcript that was
    merely suspect into a transcription that was lost.
    """
    try:
        return _transcript_qa(transcript, threshold=threshold)
    except Exception:  # pragma: no cover - defensive
        logger.warning("stapel-agent: transcript QA raised", exc_info=True)
        return {"passed": True, "checks": {CHECK_GAP: "SKIP: qa error"}}


def _transcript_qa(transcript, *, threshold: Optional[float] = None) -> dict:
    if isinstance(transcript, dict):
        utterances = transcript.get("utterances") or []
        words = transcript.get("words") or []
    else:
        utterances = getattr(transcript, "utterances", None) or []
        words = getattr(transcript, "words", None) or []

    # Utterances are the unit a host persists and a reader sees, so they
    # are what is checked. Words are the fallback for the adapters that
    # return no grouping — and they are the STRICTER of the two, since a
    # gap between words cannot be a segmentation artefact.
    segments = utterances or words
    checks: dict[str, str] = {}

    if not segments:
        checks[CHECK_GAP] = "SKIP: no segments"
        return {"passed": True, "checks": checks}

    limit = max_gap_seconds() if threshold is None else float(threshold)
    if limit <= 0:
        checks[CHECK_GAP] = "SKIP: STT_QA['MAX_GAP_SECONDS'] is 0"
        return {"passed": True, "checks": checks}

    gaps = find_gaps(segments, threshold=limit)
    if not gaps:
        checks[CHECK_GAP] = f"PASS: no gap over {limit}s in {len(segments)} segments"
        return {"passed": True, "checks": checks}

    named = gaps[:MAX_REPORTED_GAPS]
    rendered = ", ".join(
        f"{g['start']}s→{g['end']}s ({g['seconds']}s)" for g in named
    )
    more = "" if len(gaps) == len(named) else f", +{len(gaps) - len(named)} more"
    total = round(sum(g["seconds"] for g in gaps), 3)
    checks[CHECK_GAP] = (
        f"FAIL: {len(gaps)} gap(s) over {limit}s, {total}s unaccounted "
        f"for — {rendered}{more}"
    )
    return {"passed": False, "checks": checks, "gaps": gaps}


__all__ = [
    "CHECK_GAP",
    "EMPTY_TRANSCRIPT_MIN_MS",
    "MAX_GAP_SECONDS",
    "MAX_REPORTED_GAPS",
    "REASON_EMPTY",
    "empty_transcript_min_ms",
    "find_gaps",
    "is_empty_for_audio",
    "max_gap_seconds",
    "transcript_qa",
]
