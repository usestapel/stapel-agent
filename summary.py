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
    "SUMMARY_SYSTEM_PROMPT",
    "build_summary_input",
    "language_directive",
    "render_markdown",
    "split_text_chunks",
]
