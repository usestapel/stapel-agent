"""Where one utterance ends and the next begins.

THE DEFECT THIS EXISTS FOR
    Every place in this package that built utterances from words cut on
    exactly one condition: the speaker changed. A provider that returns no
    speaker ids — diarization off, or on and yielding nothing — gives every
    word ``speaker=None``, the comparison is never true after the first
    word, and the entire meeting collapses into ONE utterance. Measured on
    the owner's stand: of 83 completed recordings, 24 render as a single
    segment and 7 as none; one ten-minute meeting is a single turn 8592
    characters long. No renderer can fix that — it is faithfully drawing
    the one turn it was handed.

THE RULE, DERIVED FROM WHAT THE PROVIDERS ACTUALLY RETURN
    94 608 real word-to-word gaps off that stand (78 recordings through
    elevenlabs, 5 through assemblyai):

        p50 0.04s   p75 0.08s   p90 0.28s   p95 0.60s   p97 0.88s   p99 1.58s

    Speech is nearly gapless; the tail is where the pauses live. A
    threshold of 0.65 s sits just past p95, so it cuts on roughly one word
    boundary in twenty — a phrase every ~7 seconds — and ignores the
    ordinary 40 ms between words inside one. The same sample shows
    sentence punctuation is really there to cut on (5345 full stops, 715
    question marks, 1006 full-width commas), so it is used, and the hard
    ceilings below guarantee a wall cannot come out even from a provider
    that sends neither pauses nor punctuation.

WHAT A BOUNDARY MEANS
    A segment is what a timestamp anchors to — a citation, a "jump to this
    moment", a search hit. One segment per meeting means every anchor
    points at the whole meeting, which is the same as pointing nowhere.
    After this, an anchor resolves to a phrase of a few seconds.
"""
from __future__ import annotations

from typing import Optional

from .base import NormalizedUtterance, NormalizedWord

#: Silence between two words that ends an utterance. Just past p95 of the
#: measured distribution — see the module docstring.
GAP_SECONDS = 0.65

#: A word ending in one of these ends an utterance. Includes the CJK and
#: full-width forms, which appear in the same sample.
SENTENCE_ENDINGS = (".", "?", "!", "…", "。", "？", "！", "．")

#: Ceilings. Unlike the two signals above these are not linguistic — they
#: are the promise that no utterance is ever a wall of text, whatever the
#: provider sends. A cut here lands on a word boundary, which is the best
#: available when the audio offers no other clue.
MAX_SECONDS = 30.0
MAX_CHARS = 500

#: Floors, so the rule cannot shred speech into one-word rows: a soft
#: signal (gap or punctuation) only cuts once the utterance being built is
#: worth calling one. The ceilings above ignore these — they must.
MIN_SECONDS = 1.5
MIN_WORDS = 4


class SegmentationConfig:
    """The five numbers, overridable per deployment.

    ``STAPEL_AGENT["STT_SEGMENTATION"] = {"gap_seconds": 0.9, ...}`` for a
    host whose audio is not conversational — dictation wants a longer gap,
    a courtroom feed a shorter one.
    """

    __slots__ = (
        "gap_seconds", "max_seconds", "max_chars", "min_seconds", "min_words",
    )

    def __init__(
        self,
        gap_seconds: float = GAP_SECONDS,
        max_seconds: float = MAX_SECONDS,
        max_chars: int = MAX_CHARS,
        min_seconds: float = MIN_SECONDS,
        min_words: int = MIN_WORDS,
    ):
        self.gap_seconds = float(gap_seconds)
        self.max_seconds = float(max_seconds)
        self.max_chars = int(max_chars)
        self.min_seconds = float(min_seconds)
        self.min_words = int(min_words)

    @classmethod
    def resolve(cls, config: "SegmentationConfig | None" = None) -> "SegmentationConfig":
        if config is not None:
            return config
        try:
            from ..conf import agent_settings

            overrides = getattr(agent_settings, "STT_SEGMENTATION", None) or {}
        except Exception:
            overrides = {}
        if not isinstance(overrides, dict):
            overrides = {}
        return cls(**{k: v for k, v in overrides.items() if k in cls.__slots__})


def ends_sentence(text: str) -> bool:
    return bool(text) and text.rstrip().endswith(SENTENCE_ENDINGS)


def utterances_from_words(
    words: list[NormalizedWord], *, config: Optional[SegmentationConfig] = None
) -> list[NormalizedUtterance]:
    """Group *words* into utterances on speaker, pause, sentence and size.

    The word indexes are recorded, so the caller keeps the link back to the
    timings (``stapel_recordings`` renders per-word ``words_json`` from
    them, and an anchor needs it).
    """
    cfg = SegmentationConfig.resolve(config)
    if not words:
        return []

    out: list[NormalizedUtterance] = []
    buf_text: list[str] = []
    buf_indexes: list[int] = []
    buf_start = words[0].start
    buf_end = words[0].end
    buf_speaker = words[0].speaker

    def flush():
        text = " ".join(buf_text).strip()
        if not text:
            return
        out.append(NormalizedUtterance(
            text=text,
            start=buf_start,
            end=buf_end,
            speaker=buf_speaker,
            word_indexes=list(buf_indexes),
        ))

    for idx, w in enumerate(words):
        if not idx:
            buf_text, buf_indexes = [w.text], [idx]
            continue

        prev = words[idx - 1]
        span = buf_end - buf_start
        chars = sum(len(t) + 1 for t in buf_text)

        # 1. A different voice is always a different utterance.
        cut = w.speaker != buf_speaker
        # 2. Ceilings — unconditional, and the reason a wall cannot survive
        #    a provider that offers no other signal.
        if not cut and (span >= cfg.max_seconds or chars >= cfg.max_chars):
            cut = True
        # 3. Pause or full stop, once there is an utterance worth cutting.
        if not cut and (span >= cfg.min_seconds or len(buf_text) >= cfg.min_words):
            gap = w.start - prev.end
            if gap >= cfg.gap_seconds or ends_sentence(prev.text):
                cut = True

        if cut:
            flush()
            buf_text, buf_indexes = [w.text], [idx]
            buf_start, buf_speaker = w.start, w.speaker
        else:
            buf_text.append(w.text)
            buf_indexes.append(idx)
        buf_end = w.end
    flush()
    return out


def _words_in_span(words: list[NormalizedWord], start: float, end: float) -> list[int]:
    """Indexes of the words whose start falls inside ``[start, end]``.

    Time, not identity, because the providers that ship their own
    utterances (assemblyai, deepgram) do not say which words are in them.
    """
    return [i for i, w in enumerate(words) if start <= w.start <= end]


def finalize(
    utterances: list[NormalizedUtterance],
    words: list[NormalizedWord],
    *,
    config: Optional[SegmentationConfig] = None,
) -> list[NormalizedUtterance]:
    """The one call every adapter ends with.

    * No utterances but words — build them here. This is the diarization-off
      case for every provider that only emits ``utterances[]`` when it has
      speakers to put in them.
    * Utterances that are already reasonable — kept untouched. A provider's
      own segmentation is better than ours: it heard the audio.
    * An utterance past the ceilings — re-cut from the words it covers, so
      one provider turn that runs for eleven minutes still becomes readable.
      If no words cover it there is nothing to cut with, and it is kept as
      it is rather than chopped mid-word on a character count.
    """
    cfg = SegmentationConfig.resolve(config)
    if not utterances:
        return utterances_from_words(words, config=cfg)
    if not words:
        return utterances

    out: list[NormalizedUtterance] = []
    for utt in utterances:
        span = (utt.end or 0.0) - (utt.start or 0.0)
        if span < cfg.max_seconds and len(utt.text or "") < cfg.max_chars:
            out.append(utt)
            continue
        indexes = list(utt.word_indexes or []) or _words_in_span(words, utt.start, utt.end)
        covered = [words[i] for i in indexes if 0 <= i < len(words)]
        if len(covered) < 2:
            out.append(utt)  # nothing to re-cut with — keep the provider's
            continue
        for piece in utterances_from_words(covered, config=cfg):
            out.append(NormalizedUtterance(
                text=piece.text,
                start=piece.start,
                end=piece.end,
                # The provider's label wins: it is the one that diarized.
                speaker=utt.speaker if utt.speaker is not None else piece.speaker,
                confidence=utt.confidence,
                # Re-based onto the real word list, not the local slice.
                word_indexes=[indexes[j] for j in piece.word_indexes],
            ))
    return out


__all__ = [
    "GAP_SECONDS",
    "SENTENCE_ENDINGS",
    "MAX_SECONDS",
    "MAX_CHARS",
    "MIN_SECONDS",
    "MIN_WORDS",
    "SegmentationConfig",
    "ends_sentence",
    "utterances_from_words",
    "finalize",
]
