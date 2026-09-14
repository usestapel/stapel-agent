"""A 74-second hole in a transcript, and the two things that let it through.

PRODUCTION, 2026-09-13. A 600-second recording — a 74.31-second fixture
looped — came back with segment 3 ending at 24.88 s and segment 4 starting
at 99.32 s. Exactly one fixture length of speech was missing. Timestamps
were monotonic. The QA verdict was ``passed``.

Two findings, and this file covers both.

**One.** Nothing in the fleet chunks audio today: ``stapel_recordings``
submits one normalised object per recording and this package makes one
provider call for it. But the checkpoint that 0.24.0 introduced is keyed
on the audio's CONTENT, and content is not identity for a caller that
DOES chunk: a looped fixture's chunks are byte-identical, so they compute
one key, and every chunk after the first is served the first one's
transcript — with the first one's timestamps. That is a latent hazard
with exactly the observed shape, and it is closed by ``audio_offset_ms``.

**Two.** Whatever produced the hole, nothing was looking for it. Every
check that ran — monotonicity, max-end-in-bounds, segments-present —
passes on a transcript that covers one minute of a ninety-minute meeting.
``stt/qa.py`` adds the check that does not: a gap between consecutive
segments longer than the threshold flags ``qa.gap`` instead of passing.
"""
import pytest

from stapel_agent import services
from stapel_agent.models import PromptLog
from stapel_agent.stt.base import AudioRef, NormalizedTranscript, NormalizedUtterance
from stapel_agent.stt.qa import find_gaps, transcript_qa
from stapel_agent.tests.fakes import LoopedFixtureSttProvider

#: The fixture that was looped, and the recording it was looped into.
FIXTURE_SECONDS = 74.31
CHUNKS = 8

#: One loop of the fixture. Every chunk is THIS — byte for byte, which is
#: the whole point: a content hash cannot tell chunk 3 from chunk 4.
CHUNK_BYTES = b"RIFF\x00\x00\x00\x00WAVEfixture-loop" * 64


@pytest.fixture
def looped(settings):
    settings.STAPEL_AGENT = {
        **getattr(settings, "STAPEL_AGENT", {}),
        "STT_PROVIDERS": {
            "looped-stt": "stapel_agent.tests.fakes.LoopedFixtureSttProvider",
        },
        "DEFAULT_STT_PROVIDER": "looped-stt",
    }
    LoopedFixtureSttProvider.reset()
    yield LoopedFixtureSttProvider
    LoopedFixtureSttProvider.reset()


def _chunk_ref():
    """A fresh ref over the SAME bytes — a new chunk of the same loop."""
    return AudioRef(data=CHUNK_BYTES)


def _transcribe_chunks(*, with_offsets: bool):
    """Submit the loop chunk by chunk, as a chunking caller would."""
    out = []
    for index in range(CHUNKS):
        offset_ms = int(round(index * FIXTURE_SECONDS * 1000))
        result = services.transcribe(
            _chunk_ref(),
            language="en",
            **({"audio_offset_ms": offset_ms} if with_offsets else {}),
        )
        assert result["status"] == "ok", result
        out.append((offset_ms, result))
    return out


def _concatenate(results):
    """Re-base each chunk's local timestamps onto the recording timeline —
    what any chunking caller does with the answers it gets back."""
    utterances = []
    for offset_ms, result in results:
        offset = offset_ms / 1000.0
        for utt in result["transcript"]["utterances"]:
            utterances.append(
                {"text": utt["text"],
                 "start": utt["start"] + offset,
                 "end": utt["end"] + offset}
            )
    return utterances


@pytest.mark.django_db
class TestIdenticalChunks:
    def test_every_identical_chunk_is_transcribed_and_kept_in_order(self, looped):
        results = _transcribe_chunks(with_offsets=True)

        # THE ASSERTION THE INCIDENT IS ABOUT: N identical chunks are N
        # calls, not one cached answer reused N times.
        assert len(LoopedFixtureSttProvider.calls) == CHUNKS
        assert [r["cached"] for _, r in results] == [False] * CHUNKS

        merged = _concatenate(results)
        assert [u["text"] for u in merged] == [
            f"chunk {i}" for i in range(CHUNKS)
        ]
        # And the timeline is continuous: consecutive chunks meet exactly,
        # with no hole between them.
        for previous, current in zip(merged, merged[1:]):
            assert current["start"] == pytest.approx(previous["end"], abs=1e-6)

    def test_the_assembled_transcript_passes_qa(self, looped):
        merged = _concatenate(_transcribe_chunks(with_offsets=True))
        verdict = transcript_qa({"utterances": merged})
        assert verdict["passed"] is True

    def test_without_an_offset_identical_chunks_collapse(self, looped):
        """The hazard, stated as a test so it cannot come back unnoticed.

        This is the checkpoint working as designed — identical input,
        identical answer, no second charge — and it is exactly right for a
        WHOLE recording. It is wrong for a chunk, and the offset is how a
        caller says which of the two it is submitting.
        """
        results = _transcribe_chunks(with_offsets=False)

        assert len(LoopedFixtureSttProvider.calls) == 1
        assert [r["cached"] for _, r in results] == [False] + [True] * (CHUNKS - 1)
        # Seven chunks of the meeting are now the first chunk's text.
        texts = {
            u["text"] for u in _concatenate(results)
        }
        assert texts == {"chunk 0"}

    def test_a_deduplicating_caller_then_produces_the_production_shape(
        self, looped
    ):
        """Collapse → duplicate text → a caller that drops duplicates →
        a hole. The chain that ends in a 74-second gap, and the QA check
        that now names it."""
        merged = _concatenate(_transcribe_chunks(with_offsets=False))
        seen, deduplicated = set(), []
        for utt in merged:
            if utt["text"] in seen:
                continue
            seen.add(utt["text"])
            deduplicated.append(utt)

        # One surviving segment out of eight. Timestamps still monotonic,
        # last end still inside the recording — every pre-0.25.0 check
        # passes on this.
        assert len(deduplicated) == 1

        verdict = transcript_qa({
            "utterances": deduplicated + [
                {"text": "tail", "start": 594.48, "end": 600.0}
            ]
        })
        assert verdict["passed"] is False
        assert verdict["checks"]["gap"].startswith("FAIL")

    def test_two_different_offsets_are_two_checkpoint_rows(self, looped):
        from stapel_agent.models import ProviderCheckpoint

        services.transcribe(_chunk_ref(), language="en", audio_offset_ms=0)
        services.transcribe(_chunk_ref(), language="en", audio_offset_ms=74310)

        assert ProviderCheckpoint.objects.count() == 2

    def test_the_same_offset_is_still_one_paid_call(self, looped):
        """The offset makes chunks distinct; it must not make a RETRY
        distinct. A redelivered chunk 4 is still chunk 4."""
        first = services.transcribe(
            _chunk_ref(), language="en", audio_offset_ms=74310
        )
        second = services.transcribe(
            _chunk_ref(), language="en", audio_offset_ms=74310
        )

        assert first["cached"] is False
        assert second["cached"] is True
        assert len(LoopedFixtureSttProvider.calls) == 1

    def test_a_whole_recording_needs_no_offset(self, looped):
        """The default is unchanged: one recording, one content key."""
        first = services.transcribe(_chunk_ref(), language="en")
        second = services.transcribe(_chunk_ref(), language="en")
        assert (first["cached"], second["cached"]) == (False, True)

    def test_the_offset_is_on_the_ledger_row(self, looped):
        services.transcribe(_chunk_ref(), language="en", audio_offset_ms=74310)
        row = PromptLog.objects.get()
        assert row.metadata["audio_offset_ms"] == 74310

    def test_a_whole_recording_row_carries_no_offset_key(self, looped):
        services.transcribe(_chunk_ref(), language="en")
        assert "audio_offset_ms" not in PromptLog.objects.get().metadata


class TestGapDetection:
    def test_the_production_numbers(self):
        # Segment 3 ended at 24.88 s; segment 4 started at 99.32 s.
        segments = [
            {"start": 0.0, "end": 8.2},
            {"start": 8.4, "end": 17.1},
            {"start": 17.3, "end": 24.88},
            {"start": 99.32, "end": 106.0},
        ]
        (gap,) = find_gaps(segments)
        assert gap == {"start": 24.88, "end": 99.32, "seconds": 74.44}

        verdict = transcript_qa({"utterances": segments})
        assert verdict["passed"] is False
        assert "74.44s" in verdict["checks"]["gap"]
        assert verdict["gaps"] == [gap]

    def test_ordinary_speech_passes(self):
        # p99 of 94 608 measured word gaps is 1.58 s; a 5 s threshold is
        # three times that, so normal conversation cannot trip it.
        segments = [
            {"start": i * 3.0, "end": i * 3.0 + 2.4} for i in range(50)
        ]
        verdict = transcript_qa({"utterances": segments})
        assert verdict["passed"] is True
        assert verdict["checks"]["gap"].startswith("PASS")

    def test_overlapping_turns_are_not_gaps(self):
        # Two speakers talking over each other, emitted turn by turn. The
        # running edge is the max end seen, so the third turn is measured
        # from the real edge rather than from the previous list item.
        segments = [
            {"start": 0.0, "end": 40.0},   # a long turn
            {"start": 2.0, "end": 6.0},    # interjection inside it
            {"start": 41.0, "end": 45.0},
        ]
        assert find_gaps(segments) == []

    def test_it_is_order_independent(self):
        segments = [
            {"start": 99.32, "end": 106.0},
            {"start": 0.0, "end": 24.88},
        ]
        assert len(find_gaps(segments)) == 1

    def test_words_are_used_when_a_provider_ships_no_utterances(self):
        transcript = NormalizedTranscript(
            provider="p", language="en", duration_seconds=100.0,
            words=[],
        )
        transcript.words = [
            type("W", (), {"start": 0.0, "end": 1.0})(),
            type("W", (), {"start": 80.0, "end": 81.0})(),
        ]
        assert transcript_qa(transcript)["passed"] is False

    @pytest.mark.parametrize(
        "segments", [[], [{"start": 0.0, "end": 1.0}]]
    )
    def test_too_little_to_judge_is_a_skip_not_a_failure(self, segments):
        verdict = transcript_qa({"utterances": segments})
        assert verdict["passed"] is True
        assert verdict["checks"]["gap"].startswith("SKIP") or \
            verdict["checks"]["gap"].startswith("PASS")

    def test_untimed_segments_are_ignored_rather_than_read_as_zero(self):
        segments = [
            {"start": 0.0, "end": 3.0},
            {"start": None, "end": None},
            {"start": 3.2, "end": 6.0},
        ]
        assert find_gaps(segments) == []

    def test_a_host_can_widen_or_disable_the_threshold(self, settings):
        segments = [{"start": 0.0, "end": 3.0}, {"start": 20.0, "end": 23.0}]
        assert transcript_qa({"utterances": segments})["passed"] is False

        settings.STAPEL_AGENT = {
            **getattr(settings, "STAPEL_AGENT", {}),
            "STT_QA": {"MAX_GAP_SECONDS": 30.0},
        }
        assert transcript_qa({"utterances": segments})["passed"] is True

        settings.STAPEL_AGENT = {
            **settings.STAPEL_AGENT, "STT_QA": {"MAX_GAP_SECONDS": 0},
        }
        verdict = transcript_qa({"utterances": segments})
        assert verdict["passed"] is True
        assert verdict["checks"]["gap"].startswith("SKIP")

    def test_the_report_is_bounded(self):
        # One defect, not four hundred: a report that grows with the
        # damage is a report that cannot be logged.
        segments = [{"start": i * 100.0, "end": i * 100.0 + 1.0} for i in range(40)]
        verdict = transcript_qa({"utterances": segments})
        assert verdict["passed"] is False
        assert len(verdict["gaps"]) == 39
        assert "+34 more" in verdict["checks"]["gap"]

    def test_qa_never_raises(self):
        # A QA routine that can throw turns a suspect transcript into a
        # lost one.
        assert transcript_qa(object())["passed"] is True


@pytest.mark.django_db
class TestVerdictTravels:
    def test_the_result_and_the_row_both_carry_it(self, fake_stt):
        result = services.transcribe(AudioRef(url="https://minio.test/a.mp3"))

        assert result["qa"]["passed"] is True
        assert PromptLog.objects.get().metadata["qa"]["passed"] is True

    def test_a_checkpoint_hit_is_re_judged_not_trusted(self, fake_stt, settings):
        from stapel_agent.tests.fakes import FakeSttProvider

        FakeSttProvider.result = NormalizedTranscript(
            provider="fake-stt", language="en", duration_seconds=120.0,
            utterances=[
                NormalizedUtterance(text="a", start=0.0, end=24.88),
                NormalizedUtterance(text="b", start=99.32, end=120.0),
            ],
        )
        digest = "sha256:" + "c" * 64
        audio = AudioRef(url="https://minio.test/a.mp3")

        first = services.transcribe(audio, audio_content_hash=digest)
        second = services.transcribe(audio, audio_content_hash=digest)

        assert second["cached"] is True
        # The same answer must carry the same label. A verdict that only
        # exists on the uncached path is one an operator sees once.
        assert first["qa"]["passed"] is False
        assert second["qa"]["passed"] is False
        rows = list(PromptLog.objects.order_by("created_at"))
        assert [r.metadata["qa"]["passed"] for r in rows] == [False, False]
