"""Summarization prep — Markdown rendering and token-budget chunking.

``render_markdown`` / ``build_summary_input`` are retargeted at the
canonical ``NormalizedTranscript`` this package ships: utterances play
the role of segments and get stable ``seg_NNNN`` anchor ids (→ start
milliseconds) for click-to-timestamp in consuming UIs.

Django-free; the LLM orchestration lives in ``services.summarize``.
(Named ``summary.py`` — a ``summarize`` submodule would collide with the
package-level ``summarize`` function export.)
"""
from __future__ import annotations

from .stt.base import (
    NormalizedTranscript,
    NormalizedUtterance,
    utterances_from_words,
)

CHARS_PER_TOKEN = 4  # blunt but battle-tested heuristic from the source
DEFAULT_CHUNK_TOKENS = 15_000

SUMMARY_SYSTEM_PROMPT = (
    "You are an expert meeting and document summarizer. Produce a concise, "
    "well-structured Markdown summary of the given content: start with a "
    "short overview paragraph, then key points as bullets, then decisions "
    "and action items (with owners when identifiable). Preserve [MM:SS] "
    "timestamps when they are present in the input. Do not invent facts."
)

CHUNK_SYSTEM_PROMPT = (
    "You are an expert summarizer. The input is one PART of a longer "
    "recording or document. Summarize THIS PART only: key points, "
    "decisions and action items, preserving [MM:SS] timestamps when "
    "present. Be concise — this partial summary will be merged with "
    "others later. Do not invent facts."
)

MERGE_SYSTEM_PROMPT = (
    "You are an expert summarizer. The input is a sequence of partial "
    "summaries of consecutive parts of one recording or document. Merge "
    "them into a single coherent Markdown summary: a short overview "
    "paragraph, key points, decisions and action items. Deduplicate "
    "overlapping points and keep [MM:SS] timestamps. Do not invent facts."
)


#: Separates the Markdown summary from the speaker-name block the model
#: appends when ``speaker_names`` is asked for. Never shown to a reader:
#: :func:`split_speaker_names` cuts the block off before the text is returned.
SPEAKER_NAMES_MARKER = "<<<SPEAKER_NAMES>>>"

#: Appended to the single-shot and chunk system prompts (the merge pass sees
#: partial summaries, not the transcript, so it is never asked).
SPEAKER_NAMES_RULE = (
    "\n\nSpeaker names: after the summary, on a new line, write "
    f"{SPEAKER_NAMES_MARKER} followed by a JSON array, and nothing after it. "
    "For each speaker label shown in the transcript (the text before the "
    "colon on a line) whose own name the transcript states - the speaker "
    "introduces themselves, is addressed by name, or signs off - add one "
    'object: {"speaker": the label exactly as shown, "suggested_name": the '
    'name as said, "evidence_text": a verbatim quote of at most 25 words '
    "from one transcript line that states the name, in the original "
    'language, "confidence": "high" for a self-introduction or sign-off, '
    '"medium" for a consistent form of address by another speaker, "low" '
    "for a single ambiguous mention}. At most one object per label. Never "
    "invent a name; when no name is stated for a speaker, leave that speaker "
    "out. When no names are stated, write []."
)

#: Length caps applied to each parsed guess (untrusted model output).
SPEAKER_GUESS_LIMITS = {"speaker": 64, "suggested_name": 80, "evidence_text": 400}
SPEAKER_GUESS_CONFIDENCE = ("high", "medium", "low")


def speaker_names_rule(enabled: bool) -> str:
    """The speaker-name rule, or "" — with it off the prompts are unchanged."""
    return SPEAKER_NAMES_RULE if enabled else ""


def split_speaker_names(text: str) -> tuple[str, list[dict] | None, int]:
    """``(summary, guesses, dropped)`` out of a reply asked for speaker names.

    The block after the LAST :data:`SPEAKER_NAMES_MARKER` is parsed as a JSON
    array (a code fence around it is tolerated) and each entry is validated
    as :class:`SpeakerNameGuess`; entries that do not validate are dropped
    and counted. ``guesses`` is None when the reply carries no parsable
    block (the marker is missing or the JSON is broken) — a reason, not an
    empty answer. The summary text never keeps the block.
    """
    import json

    from pydantic import ValidationError

    text = text or ""
    head, sep, tail = text.rpartition(SPEAKER_NAMES_MARKER)
    if not sep:
        return text, None, 0
    summary = head.rstrip()
    body = tail.strip()
    if body.startswith("```"):
        body = body.strip("`").strip()
        if body.lower().startswith("json"):
            body = body[4:].strip()
    try:
        items = json.loads(body) if body else []
    except ValueError:
        return summary, None, 0
    if not isinstance(items, list):
        return summary, None, 0
    guesses: list[dict] = []
    dropped = 0
    for item in items:
        try:
            guesses.append(SpeakerNameGuess.model_validate(item).model_dump())
        except ValidationError:
            dropped += 1
    return summary, guesses, dropped


def _speaker_guess_model():
    from typing import Literal

    from pydantic import BaseModel, ConfigDict, field_validator

    limits = SPEAKER_GUESS_LIMITS

    class SpeakerNameGuess(BaseModel):
        """One name the transcript states for a speaker label (model output,
        untrusted: the caller still checks the label and the quote)."""

        model_config = ConfigDict(extra="ignore")

        speaker: str
        suggested_name: str
        evidence_text: str
        confidence: Literal["high", "medium", "low"]

        @field_validator("speaker", "suggested_name", "evidence_text")
        @classmethod
        def _bounded(cls, value: str, info):
            value = value.strip()
            if not value or len(value) > limits[info.field_name]:
                raise ValueError(f"{info.field_name} empty or over its cap")
            return value

    return SpeakerNameGuess


SpeakerNameGuess = _speaker_guess_model()


#: Length caps of the optional purpose inputs (``llm.summarize`` schema).
PURPOSE_GUIDANCE_MAX_CHARS = 2000
PURPOSE_LABEL_MAX_CHARS = 64


def _purpose_pair(label: str | None, guidance: str | None) -> tuple[str, str] | None:
    """Both inputs as given, or None: a label without guidance (or the
    reverse, or a blank one) sends nothing."""
    if not (label or "").strip() or not (guidance or "").strip():
        return None
    return label, guidance


def purpose_block(label: str | None, guidance: str | None) -> str:
    """The recording-type block for the single-shot and merge system prompts.

    Goes after the base prompt and before ``language_directive``; it ends with
    a newline so the language line stays last. Empty unless both are present.
    """
    pair = _purpose_pair(label, guidance)
    if pair is None:
        return ""
    return (
        "\n\n## Recording type\n"
        f"Recording type (uploader's choice): {pair[0]}\n{pair[1]}\n"
    )


def purpose_label_line(label: str | None, guidance: str | None) -> str:
    """The label line alone, for the chunk system prompt.

    Guidance asks for an overview and lists, which would collide with
    "Summarize THIS PART only". Empty unless both inputs are present.
    """
    pair = _purpose_pair(label, guidance)
    if pair is None:
        return ""
    return f"\n\nRecording type (uploader's choice): {pair[0]}\n"


def language_directive(language: str | None) -> str:
    """Suffix appended to the summary prompts when a target language is set."""
    return f" Respond in {language}." if language else ""


def _segments(transcript: NormalizedTranscript) -> list[NormalizedUtterance]:
    if transcript.utterances:
        return transcript.utterances
    return utterances_from_words(transcript.words)


def _format_ms(ms: int) -> str:
    total_sec = ms // 1000
    return f"{total_sec // 60:02d}:{total_sec % 60:02d}"


def _to_ms(seconds: float) -> int:
    return int(round(float(seconds or 0) * 1000))


def _line(utt: NormalizedUtterance) -> str:
    speaker = utt.speaker or "Unknown"
    return f"[{_format_ms(_to_ms(utt.start))}] {speaker}: {utt.text}"


def render_markdown(transcript: NormalizedTranscript) -> str:
    """Render a transcript as Markdown suitable for LLM input.

    Format: header with duration/language/speakers, then one
    ``[MM:SS] speaker: text`` line per utterance.
    """
    duration_str = _format_ms(_to_ms(transcript.duration_seconds or 0))
    lang = transcript.language or "?"
    lines = [
        "# Transcript",
        f"Duration: {duration_str} | Language: {lang} | "
        f"Speakers: {len(transcript.speakers_detected)}",
        "",
    ]
    lines.extend(_line(utt) for utt in _segments(transcript))
    return "\n".join(lines)


def build_summary_input(
    transcript: NormalizedTranscript,
    *,
    chunk_tokens: int = DEFAULT_CHUNK_TOKENS,
    overlap_segments: int = 1,
) -> dict:
    """Chunk the transcript for LLM summarization.

    Returns ``{"meta": {...}, "chunks": [{"text": ..., "anchors":
    {"seg_0001": start_ms, ...}}]}``. Each chunk is a Markdown rendering
    of consecutive utterances; ``anchors`` maps segment id → start
    milliseconds for click-to-timestamp. Budget ≈ chunk_tokens × 4 chars.
    """
    max_chars = chunk_tokens * CHARS_PER_TOKEN
    segments = _segments(transcript)

    chunks: list[dict] = []
    buf: list[tuple[int, NormalizedUtterance]] = []
    buf_chars = 0

    def seg_chars(utt: NormalizedUtterance) -> int:
        return len(utt.text) + 30  # approximate timestamp/speaker prefix

    def flush(entries: list[tuple[int, NormalizedUtterance]]) -> None:
        if not entries:
            return
        anchors = {f"seg_{idx:04d}": _to_ms(utt.start) for idx, utt in entries}
        chunks.append(
            {"text": "\n".join(_line(utt) for _, utt in entries), "anchors": anchors}
        )

    for idx, utt in enumerate(segments):
        cost = seg_chars(utt)
        if buf_chars + cost > max_chars and buf:
            flush(buf)
            # Keep overlap segments for context continuity.
            buf = buf[-overlap_segments:] if overlap_segments else []
            buf_chars = sum(seg_chars(u) for _, u in buf)
        buf.append((idx, utt))
        buf_chars += cost

    flush(buf)

    return {
        "meta": {
            "provider": transcript.provider,
            "language": transcript.language,
            "duration_ms": _to_ms(transcript.duration_seconds or 0),
            "speakers": list(transcript.speakers_detected),
            "total_segments": len(segments),
            "chunks_count": len(chunks),
            "tokens_per_chunk_target": chunk_tokens,
        },
        "chunks": chunks,
    }


def split_text_chunks(text: str, *, chunk_tokens: int = DEFAULT_CHUNK_TOKENS) -> list[str]:
    """Split plain text into ≈chunk_tokens pieces on paragraph, then line,
    boundaries (hard-splitting only a single oversized block)."""
    max_chars = chunk_tokens * CHARS_PER_TOKEN
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    buf: list[str] = []
    buf_chars = 0
    for block in text.replace("\r\n", "\n").split("\n\n"):
        pieces = [block] if len(block) <= max_chars else [
            block[i:i + max_chars] for i in range(0, len(block), max_chars)
        ]
        for piece in pieces:
            cost = len(piece) + 2
            if buf_chars + cost > max_chars and buf:
                chunks.append("\n\n".join(buf))
                buf = []
                buf_chars = 0
            buf.append(piece)
            buf_chars += cost
    if buf:
        chunks.append("\n\n".join(buf))
    return chunks


__all__ = [
    "CHUNK_SYSTEM_PROMPT",
    "DEFAULT_CHUNK_TOKENS",
    "MERGE_SYSTEM_PROMPT",
    "PURPOSE_GUIDANCE_MAX_CHARS",
    "PURPOSE_LABEL_MAX_CHARS",
    "SUMMARY_SYSTEM_PROMPT",
    "SPEAKER_NAMES_MARKER",
    "SPEAKER_NAMES_RULE",
    "SpeakerNameGuess",
    "build_summary_input",
    "speaker_names_rule",
    "split_speaker_names",
    "language_directive",
    "purpose_block",
    "purpose_label_line",
    "render_markdown",
    "split_text_chunks",
]
