"""One meeting must not come back as one segment.

The defect, measured on the owner's stand (2026-09-09): of 83 completed
recordings, 24 render as a single segment and 7 as none; one ten-minute
meeting is a single turn 8592 characters long. Every builder of utterances
in this package cut on the speaker changing and nothing else, so a response
with no speaker ids — diarization off, or on and yielding nothing — never
cut at all.

Every case here is built from words with speaker=None, which is the shape
that broke, and asserts boundaries land where the pauses are.
"""
import pytest

from stapel_agent.stt import segmentation
from stapel_agent.stt.base import (
    NormalizedUtterance,
    NormalizedWord,
    utterances_from_words,
)


def speech(script, *, speaker=None, start=0.0, word_seconds=0.32, gap=0.05):
    """Words for *script* — a list of (text, pause_before_this_word)."""
    words, t = [], start
    for text, pause in script:
        t += pause
        words.append(NormalizedWord(text=text, start=round(t, 3),
                                    end=round(t + word_seconds, 3), speaker=speaker))
        t += word_seconds + gap
    return words


def monologue(n_words, *, pause_every=0, pause=1.2, speaker=None):
    script = []
    for i in range(n_words):
        script.append((f"word{i}", pause if (pause_every and i and i % pause_every == 0) else 0.0))
    return speech(script, speaker=speaker)


class TestTheDefect:
    """These fail on the pre-0.22 rule (speaker change only)."""

    def test_a_speakerless_meeting_is_not_one_segment(self):
        # ~10 minutes of speech, no speaker ids anywhere — the live shape.
        words = monologue(1600, pause_every=40, pause=1.1)
        assert all(w.speaker is None for w in words)

        out = utterances_from_words(words)

        assert len(out) > 1, "the whole meeting collapsed into one utterance"
        assert len(out) >= 30
        assert max(len(u.text) for u in out) <= segmentation.MAX_CHARS + 40

    def test_boundaries_fall_on_the_pauses(self):
        words = speech([
            ("one", 0.0), ("two", 0.0), ("three", 0.0), ("four", 0.0),
            ("five", 1.4),            # <- a long pause: a boundary
            ("six", 0.0), ("seven", 0.0), ("eight", 0.0), ("nine", 0.0),
            ("ten", 1.4),             # <- and another
            ("eleven", 0.0), ("twelve", 0.0), ("thirteen", 0.0), ("fourteen", 0.0),
        ])
        out = utterances_from_words(words)

        assert [u.text for u in out] == [
            "one two three four",
            "five six seven eight nine",
            "ten eleven twelve thirteen fourteen",
        ]

    def test_a_full_stop_ends_an_utterance(self):
        words = speech([
            ("Good", 0.0), ("morning", 0.0), ("everyone", 0.0), ("here.", 0.0),
            ("First", 0.0), ("item", 0.0), ("is", 0.0), ("billing.", 0.0),
            ("Anna", 0.0), ("will", 0.0), ("take", 0.0), ("it.", 0.0),
        ])
        out = utterances_from_words(words)

        assert len(out) == 3
        assert out[0].text == "Good morning everyone here."
        assert out[1].text == "First item is billing."

    def test_no_pauses_and_no_punctuation_still_cannot_wall(self):
        """The ceiling is the promise: SOMETHING cuts, whatever arrives."""
        words = monologue(4000)  # 20+ minutes, gapless, unpunctuated

        out = utterances_from_words(words)

        assert len(out) > 1
        assert all(len(u.text) <= segmentation.MAX_CHARS + 40 for u in out)
        assert all((u.end - u.start) <= segmentation.MAX_SECONDS + 1 for u in out)


class TestItStillRespectsSpeakers:
    def test_a_speaker_change_always_cuts(self):
        words = (
            speech([("hello", 0.0), ("there", 0.0)], speaker="speaker_0")
            + speech([("hi", 0.0), ("back", 0.0)], speaker="speaker_1", start=10.0)
        )
        out = utterances_from_words(words)

        assert [u.speaker for u in out] == ["speaker_0", "speaker_1"]

    def test_short_bursts_are_not_shredded(self):
        """A floor, or the rule turns speech into one-word rows."""
        words = speech([("yes", 0.0), ("absolutely", 0.9)])

        out = utterances_from_words(words)

        assert len(out) == 1, "a 0.9s pause after one word must not cut"

    def test_word_indexes_survive(self):
        words = monologue(200, pause_every=20, pause=1.2)
        out = utterances_from_words(words)

        seen = [i for u in out for i in u.word_indexes]
        assert seen == list(range(len(words)))


class TestFinalize:
    def test_no_utterances_means_build_them(self):
        words = monologue(600, pause_every=25, pause=1.1)
        out = segmentation.finalize([], words)
        assert len(out) > 1

    def test_a_reasonable_provider_segmentation_is_left_alone(self):
        words = monologue(20)
        provider = [NormalizedUtterance(text="short turn", start=0.0, end=4.0,
                                        speaker="speaker_0")]
        assert segmentation.finalize(provider, words) == provider

    def test_an_eleven_minute_provider_turn_is_recut(self):
        words = monologue(1200, pause_every=30, pause=1.1)
        wall = NormalizedUtterance(
            text=" ".join(w.text for w in words),
            start=words[0].start, end=words[-1].end, speaker="speaker_0",
        )
        out = segmentation.finalize([wall], words)

        assert len(out) > 1
        # The provider heard the audio; its label survives the re-cut.
        assert all(u.speaker == "speaker_0" for u in out)
        # Indexes are re-based onto the real word list, not the slice.
        assert out[-1].word_indexes[-1] == len(words) - 1

    def test_a_turn_with_no_words_under_it_is_kept(self):
        wall = NormalizedUtterance(text="x" * 4000, start=0.0, end=900.0)
        assert segmentation.finalize([wall], []) == [wall]


class TestConfigurable:
    def test_a_host_can_move_the_gap(self):
        words = speech([("a", 0.0), ("b", 0.0), ("c", 0.0), ("d", 0.0), ("e", 0.8)])

        tight = segmentation.utterances_from_words(
            words, config=segmentation.SegmentationConfig(gap_seconds=0.5)
        )
        loose = segmentation.utterances_from_words(
            words, config=segmentation.SegmentationConfig(gap_seconds=2.0)
        )
        assert len(tight) == 2
        assert len(loose) == 1


class TestEveryAdapterCuts:
    """The class, not the one adapter: same shape through each mapper."""

    def _words_payload(self, n=1200):
        out, t = [], 0.0
        for i in range(n):
            if i and i % 30 == 0:
                t += 1.2
            out.append((f"word{i}", round(t, 3), round(t + 0.32, 3)))
            t += 0.37
        return out

    def test_elevenlabs_with_no_speaker_ids(self):
        from stapel_agent.stt.providers.elevenlabs import _normalize

        payload = {"language_code": "en", "words": [
            {"type": "word", "text": t, "start": s, "end": e}
            for t, s, e in self._words_payload()
        ]}
        tr = _normalize(payload, provider="elevenlabs")

        assert len(tr.words) == 1200
        assert len(tr.utterances) > 1, "elevenlabs still returns one wall"
        assert tr.speakers_detected == []

    def test_deepgram_with_no_provider_utterances(self):
        from stapel_agent.stt.providers.deepgram import _normalize

        payload = {"results": {"channels": [{"alternatives": [{"words": [
            {"word": t, "start": s, "end": e} for t, s, e in self._words_payload()
        ]}]}]}, "metadata": {"duration": 900.0}}
        tr = _normalize(payload, provider="deepgram")

        assert len(tr.utterances) > 1

    def test_whisper_http_prefers_words_over_the_whole_text(self):
        from stapel_agent.stt.providers.whisper_http import _normalize

        wp = self._words_payload()
        payload = {
            "text": " ".join(t for t, _, _ in wp),
            "words": [{"word": t, "start": s, "end": e} for t, s, e in wp],
            "duration": 900.0,
        }
        tr = _normalize(payload, provider="whisper")

        assert len(tr.utterances) > 1


@pytest.mark.parametrize("module_name", [
    "assemblyai", "deepgram", "elevenlabs", "gladia",
    "soniox", "speechmatics", "whisper_http", "xai_stt",
])
def test_every_adapter_routes_through_the_shared_rule(module_name):
    """A new adapter that segments on its own is the defect coming back."""
    import importlib
    import inspect

    mod = importlib.import_module(f"stapel_agent.stt.providers.{module_name}")
    source = inspect.getsource(mod)
    assert "segmentation." in source or "utterances_from_words" in source, (
        f"{module_name} builds utterances without the shared cut rule"
    )
