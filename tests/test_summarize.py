"""Summarization tests — chunking prep (summary.py) and the map-reduce
orchestration in services.summarize (fake LLM provider counts calls)."""
import pytest

from stapel_agent import services, summary
from stapel_agent.models import PromptLog, PromptSource
from stapel_agent.providers.base import ProviderError, ProviderResult
from stapel_agent.stt.base import (
    NormalizedTranscript,
    NormalizedUtterance,
    NormalizedWord,
)


def transcript(n_utterances=3, text="Point number %d discussed at length."):
    return NormalizedTranscript(
        provider="fake-stt",
        language="en",
        duration_seconds=float(n_utterances * 10),
        utterances=[
            NormalizedUtterance(
                text=text % i, start=i * 10.0, end=i * 10.0 + 9.0, speaker="A"
            )
            for i in range(n_utterances)
        ],
        speakers_detected=["A"],
    )


class TestRenderMarkdown:
    def test_header_and_timestamped_lines(self):
        md = summary.render_markdown(transcript(2))
        lines = md.split("\n")
        assert lines[0] == "# Transcript"
        assert lines[1] == "Duration: 00:20 | Language: en | Speakers: 1"
        assert lines[3] == "[00:00] A: Point number 0 discussed at length."
        assert lines[4] == "[00:10] A: Point number 1 discussed at length."

    def test_words_only_transcript_derives_utterances(self):
        t = NormalizedTranscript(
            provider="p",
            language=None,
            duration_seconds=None,
            words=[
                NormalizedWord(text="Hi", start=61.0, end=61.5, speaker="B"),
                NormalizedWord(text="there", start=61.5, end=62.0, speaker="B"),
            ],
        )
        md = summary.render_markdown(t)
        assert "[01:01] B: Hi there" in md
        assert "Language: ?" in md


class TestBuildSummaryInput:
    def test_single_chunk_with_anchors(self):
        built = summary.build_summary_input(transcript(3))
        assert built["meta"]["chunks_count"] == 1
        assert built["meta"]["total_segments"] == 3
        assert built["meta"]["duration_ms"] == 30000
        (chunk,) = built["chunks"]
        # seg_NNNN anchors → start milliseconds, one per utterance
        assert chunk["anchors"] == {
            "seg_0000": 0,
            "seg_0001": 10000,
            "seg_0002": 20000,
        }
        assert chunk["text"].startswith("[00:00] A: Point number 0")

    def test_splits_on_token_budget_with_overlap(self):
        built = summary.build_summary_input(transcript(4), chunk_tokens=40)
        # ~66 chars per segment (36 text + 30 prefix) vs a 160-char budget
        # → 2 segments per chunk, 1 overlap segment carried forward.
        assert built["meta"]["chunks_count"] == len(built["chunks"]) >= 2
        first, second = built["chunks"][0], built["chunks"][1]
        # the overlap: the last anchor of chunk N reappears in chunk N+1
        last_anchor = sorted(first["anchors"])[-1]
        assert last_anchor in second["anchors"]

    def test_empty_transcript_yields_no_chunks(self):
        t = NormalizedTranscript(provider="p", language=None, duration_seconds=None)
        built = summary.build_summary_input(t)
        assert built["chunks"] == []
        assert built["meta"]["total_segments"] == 0
        assert built["meta"]["chunks_count"] == 0

    def test_anchor_ids_are_global_segment_indexes(self):
        built = summary.build_summary_input(transcript(4), chunk_tokens=40)
        all_anchors = {}
        for chunk in built["chunks"]:
            all_anchors.update(chunk["anchors"])
        assert all_anchors == {
            "seg_0000": 0,
            "seg_0001": 10000,
            "seg_0002": 20000,
            "seg_0003": 30000,
        }


class TestSplitTextChunks:
    def test_short_text_is_one_chunk(self):
        assert summary.split_text_chunks("hello") == ["hello"]

    def test_splits_on_paragraphs(self):
        paras = ["a" * 30, "b" * 30, "c" * 30]
        chunks = summary.split_text_chunks("\n\n".join(paras), chunk_tokens=10)
        assert len(chunks) == 3
        assert chunks[0] == "a" * 30

    def test_oversized_block_is_hard_split(self):
        chunks = summary.split_text_chunks("x" * 100, chunk_tokens=10)  # 40 chars
        assert len(chunks) == 3
        assert "".join(chunks) == "x" * 100

    def test_language_directive(self):
        assert summary.language_directive("de") == " Respond in de."
        assert summary.language_directive(None) == ""


@pytest.mark.django_db
class TestSummarizeService:
    def test_single_shot_text(self, fake_provider):
        fake_provider.result = ProviderResult(
            text="## Summary", input_tokens=10, output_tokens=5
        )
        result = services.summarize("short text to summarize")
        assert result == {
            "status": "ok",
            "summary": "## Summary",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        assert len(fake_provider.calls) == 1
        call = fake_provider.calls[0]
        assert call["prompt"] == "short text to summarize"
        assert call["system_prompt"] == summary.SUMMARY_SYSTEM_PROMPT

    def test_map_reduce_counts_chunk_plus_merge_calls(self, fake_provider):
        paras = [("Paragraph %d. " % i) + "z" * 50 for i in range(3)]
        result = services.summarize("\n\n".join(paras), chunk_tokens=20)
        assert result["status"] == "ok"
        # 3 chunk passes + 1 merge pass
        assert len(fake_provider.calls) == 4
        chunk_calls, merge_call = fake_provider.calls[:3], fake_provider.calls[3]
        for i, call in enumerate(chunk_calls):
            assert call["prompt"].startswith(f"Part {i + 1} of 3:")
            assert call["system_prompt"] == summary.CHUNK_SYSTEM_PROMPT
        assert merge_call["system_prompt"] == summary.MERGE_SYSTEM_PROMPT
        assert "Part 1 summary:" in merge_call["prompt"]
        # usage aggregated across ALL four calls
        assert result["usage"] == {"input_tokens": 40, "output_tokens": 20}

    def test_transcript_object_single_shot(self, fake_provider):
        result = services.summarize(transcript(2), language="de")
        assert result["status"] == "ok"
        assert len(fake_provider.calls) == 1
        call = fake_provider.calls[0]
        # chunk text is the timestamped Markdown rendering
        assert "[00:00] A: Point number 0" in call["prompt"]
        assert call["system_prompt"].endswith(" Respond in de.")

    def test_transcript_dict_accepted(self, fake_provider):
        result = services.summarize(transcript(2).to_dict())
        assert result["status"] == "ok"
        assert len(fake_provider.calls) == 1

    def test_transcript_map_reduce(self, fake_provider):
        result = services.summarize(transcript(4), chunk_tokens=40)
        assert result["status"] == "ok"
        assert len(fake_provider.calls) > 2  # chunks + merge

    def test_invalid_transcript_dict_is_failure(self, fake_provider):
        result = services.summarize({"provider": "x", "words": [{"beep": 1}]})
        assert result["status"] == "failure"
        assert "Invalid transcript payload" in result["reason"]
        assert fake_provider.calls == []

    def test_empty_text_is_failure(self, fake_provider):
        result = services.summarize("   \n ")
        assert result == {"status": "failure", "reason": "Nothing to summarize"}
        assert fake_provider.calls == []

    def test_wrong_type_is_failure(self, fake_provider):
        result = services.summarize(12345)
        assert result["status"] == "failure"
        assert "str, NormalizedTranscript or transcript dict" in result["reason"]

    def test_provider_failure_single_shot(self, fake_provider):
        fake_provider.error = ProviderError("llm down")
        result = services.summarize("text")
        assert result == {"status": "failure", "reason": "llm down"}

    def test_chunk_failure_aborts_map_reduce(self, fake_provider):
        fake_provider.error = ProviderError("llm down")
        result = services.summarize("a" * 100, chunk_tokens=10)
        assert result == {"status": "failure", "reason": "llm down"}
        assert len(fake_provider.calls) == 1  # stopped at the first chunk

    def test_merge_failure_is_reported(self, fake_provider, monkeypatch):
        # Chunk passes succeed; only the final merge call blows up.
        original = fake_provider.complete

        def flaky(self, *, prompt, model, system_prompt=None):
            if "Part 1 summary:" in prompt:
                raise ProviderError("merge down")
            return original(
                self, prompt=prompt, model=model, system_prompt=system_prompt
            )

        monkeypatch.setattr(fake_provider, "complete", flaky)
        result = services.summarize("a" * 100, chunk_tokens=10)
        assert result == {"status": "failure", "reason": "merge down"}

    def test_model_size_and_provider_forwarded(self, fake_provider):
        services.summarize("text", model_size="large", provider="fake")
        assert fake_provider.calls[0]["model"] == "claude-opus-4-8"

    def test_ledger_rows_have_source_summarize(self, fake_provider):
        services.summarize("a" * 100, chunk_tokens=10, user_id="u-7")
        rows = PromptLog.objects.all()
        assert rows.count() == len(fake_provider.calls) >= 2
        assert {r.source for r in rows} == {PromptSource.SUMMARIZE}
        assert {r.user_id for r in rows} == {"u-7"}
        assert {r.metadata["provider"] for r in rows} == {"fake"}

    def test_summarize_not_cached_by_default(self, fake_provider):
        services.summarize("same text")
        services.summarize("same text")
        # CACHE_LOOKUP defaults summarize to off — both calls hit the LLM.
        assert len(fake_provider.calls) == 2
