"""Provider-response validator tests — ported verbatim from the iron-benchmark
research harness.

Only the validator-focused tests (plus the fixtures they need) were brought
over; the mapper / adapter / pricing / routing tests that live alongside them
in the harness stay there — this module is scoped to
``stapel_agent.stt.validation`` / ``stapel_agent.diarization.validation``.

Each provider's harness test module used its own ``validate_response`` import
and, in several cases, reused test names like ``test_validate_clean_and_edge_cases``
independently of the other providers' modules. Grouping one class per provider
here avoids those cross-provider name collisions while keeping every method
name, docstring, assertion and inline sample payload exactly as written in the
source. The only changes from the originals are: the import paths (now
pointing at ``stapel_agent.stt.validation.*`` /
``stapel_agent.diarization.validation.*`` instead of
``pipeline.adapters.*_validate``), the ``self`` parameter and indentation
needed to nest each function as a class method, and a local
``validate_response = ...`` alias line at the top of each method so the
assertion bodies below it can call the bare ``validate_response(...)`` name
exactly as the source did.

Sources (ironmemo-backend, origin/feature/benchmark-harness):
  iron-benchmark/pipeline/tests/test_assemblyai.py
  iron-benchmark/pipeline/tests/test_deepgram.py
  iron-benchmark/pipeline/tests/test_elevenlabs.py
  iron-benchmark/pipeline/tests/test_gladia_p66.py
  iron-benchmark/pipeline/tests/test_soniox_p76.py
  iron-benchmark/pipeline/tests/test_speechmatics_p73.py
  iron-benchmark/pipeline/tests/test_xai_stt_p76.py
  iron-benchmark/pipeline/tests/test_pyannote_hybrid_p84.py
"""

from stapel_agent.stt.validation.assemblyai import (
    validate_response as _assemblyai_validate_response,
)
from stapel_agent.stt.validation.deepgram import (
    validate_response as _deepgram_validate_response,
)
from stapel_agent.stt.validation.elevenlabs import (
    validate_response as _elevenlabs_validate_response,
)
from stapel_agent.stt.validation.gladia import (
    validate_response as _gladia_validate_response,
)
from stapel_agent.stt.validation.soniox import (
    validate_response as _soniox_validate_response,
)
from stapel_agent.stt.validation.speechmatics import (
    validate_response as _speechmatics_validate_response,
)
from stapel_agent.stt.validation.xai_stt import (
    validate_response as _xai_stt_validate_response,
)
from stapel_agent.diarization.validation.pyannote import (
    validate_response as _pyannote_validate_response,
)


# =============================================================================
# AssemblyAI — source: iron-benchmark/pipeline/tests/test_assemblyai.py
# =============================================================================

def _fake_completed(n_speakers: int = 3, *, audio_duration_sec: int = 78) -> dict:
    """A completed-transcript dict in AssemblyAI's documented shape (times MS)."""
    utterances, words = [], []
    t = 0
    for si in range(n_speakers):
        spk = chr(ord("A") + si)  # AAI labels are letters A, B, C...
        u_words = []
        u_start = t
        for wi in range(4):
            w = {"text": f"w{si}{wi}", "start": t, "end": t + 400,
                 "confidence": 0.9, "speaker": spk}
            u_words.append(w)
            words.append(w)
            t += 500
        utterances.append({"speaker": spk, "text": " ".join(x["text"] for x in u_words),
                           "start": u_start, "end": t - 100, "confidence": 0.9,
                           "words": u_words})
    return {
        "id": "fake-id", "status": "completed", "language_code": "en",
        "audio_duration": audio_duration_sec, "confidence": 0.91,
        "text": " ".join(w["text"] for w in words),
        "words": words, "utterances": utterances,
    }


class TestAssemblyAIValidate:
    """Mirrors ``test_assemblyai.py``'s validator edge-case coverage."""

    def test_validate_flags_missing_utterances_and_status(self):
        validate_response = _assemblyai_validate_response
        raw = _fake_completed(2)
        raw["utterances"] = []
        issues = validate_response(raw, expect_diarization=True)
        assert any(i.code == "NO_UTTERANCES" and i.severity == "error" for i in issues)

        raw2 = _fake_completed(2)
        raw2["status"] = "processing"
        issues2 = validate_response(raw2, expect_diarization=True)
        assert any(i.code == "NOT_COMPLETED" for i in issues2)


# =============================================================================
# Deepgram — source: iron-benchmark/pipeline/tests/test_deepgram.py
# =============================================================================

def _fake_response() -> dict:
    """A fresh Deepgram pre-recorded response in the documented shape (times SECONDS).

    Two speakers (integer 0 and 1 — note 0 is a VALID but falsy speaker), four
    words, two utterances. ``punctuated_word`` present (smart_format).
    """
    words = [
        {"word": "hello", "start": 0.5, "end": 0.8, "confidence": 0.99,
         "punctuated_word": "Hello", "speaker": 0, "speaker_confidence": 0.95},
        {"word": "how", "start": 0.9, "end": 1.1, "confidence": 0.97,
         "punctuated_word": "how", "speaker": 0, "speaker_confidence": 0.95},
        {"word": "are", "start": 1.2, "end": 1.3, "confidence": 0.98,
         "punctuated_word": "are", "speaker": 1, "speaker_confidence": 0.88},
        {"word": "you", "start": 1.4, "end": 1.6, "confidence": 0.99,
         "punctuated_word": "you?", "speaker": 1, "speaker_confidence": 0.88},
    ]
    return {
        "metadata": {
            "request_id": "test-123",
            "duration": 5.5,  # SECONDS
            "model_info": {"uuid-1": {"name": "nova-3", "arch": "nova-3"}},
        },
        "results": {
            "channels": [{
                "detected_language": "en",
                "alternatives": [{
                    "transcript": "Hello how are you?",
                    "confidence": 0.98,
                    "words": [dict(w) for w in words],
                }],
            }],
            "utterances": [
                {"start": 0.5, "end": 1.1, "confidence": 0.98, "channel": 0,
                 "transcript": "Hello how", "speaker": 0,
                 "words": [dict(words[0]), dict(words[1])], "id": "utt-0"},
                {"start": 1.2, "end": 1.6, "confidence": 0.98, "channel": 0,
                 "transcript": "are you?", "speaker": 1,
                 "words": [dict(words[2]), dict(words[3])], "id": "utt-1"},
            ],
        },
    }


class TestDeepgramValidate:
    """Mirrors ``test_deepgram.py``'s validator edge-case coverage."""

    # --- test 6: validator flags empty transcript --------------------------------

    def test_validate_empty_text(self):
        validate_response = _deepgram_validate_response
        raw = _fake_response()
        raw["results"]["channels"][0]["alternatives"][0]["transcript"] = "   "
        issues = validate_response(raw)
        assert any(i.code == "EMPTY_TEXT" and i.severity == "error" for i in issues)

    # --- test 7: validator flags a negative timestamp ----------------------------

    def test_validate_negative_timestamp(self):
        validate_response = _deepgram_validate_response
        raw = _fake_response()
        raw["results"]["channels"][0]["alternatives"][0]["words"][0]["start"] = -0.5
        issues = validate_response(raw)
        assert any(i.code == "NEGATIVE_TIMESTAMP" and i.severity == "error" for i in issues)


# =============================================================================
# ElevenLabs — source: iron-benchmark/pipeline/tests/test_elevenlabs.py
# =============================================================================

# 3 words, 2 speakers, spacing tokens between words (times in SECONDS).
FAKE_ELEVENLABS_RESPONSE = {
    "language_code": "en",
    "language_probability": 0.98,
    "text": "Hello world test",
    "words": [
        {"start": 0.5, "end": 0.8, "text": "Hello", "type": "word", "speaker_id": "speaker_0"},
        {"start": 0.8, "end": 0.81, "text": " ", "type": "spacing", "speaker_id": "speaker_0"},
        {"start": 0.9, "end": 1.2, "text": "world", "type": "word", "speaker_id": "speaker_0"},
        {"start": 1.2, "end": 1.21, "text": " ", "type": "spacing", "speaker_id": "speaker_0"},
        {"start": 1.5, "end": 1.9, "text": "test", "type": "word", "speaker_id": "speaker_1"},
    ],
}


class TestElevenLabsValidate:
    """Mirrors ``test_elevenlabs.py``'s validator coverage."""

    def test_validate_clean(self):
        validate_response = _elevenlabs_validate_response
        issues = validate_response(FAKE_ELEVENLABS_RESPONSE, expect_diarization=True)
        assert [i for i in issues if i.severity == "error"] == []

    def test_validate_empty_text(self):
        validate_response = _elevenlabs_validate_response
        raw = dict(FAKE_ELEVENLABS_RESPONSE, text="")
        codes = {i.code for i in validate_response(raw)}
        assert "EMPTY_TEXT" in codes

    def test_validate_negative_timestamp(self):
        validate_response = _elevenlabs_validate_response
        raw = {
            "language_code": "en", "text": "bad",
            "words": [{"start": -0.1, "end": 0.2, "text": "bad", "type": "word", "speaker_id": "s0"}],
        }
        issues = validate_response(raw)
        assert any(i.code == "NEGATIVE_TIMESTAMP" and i.severity == "error" for i in issues)

    def test_validate_no_words(self):
        validate_response = _elevenlabs_validate_response
        issues = validate_response({"text": "hi", "words": []})
        assert any(i.code == "NO_WORDS" for i in issues)

    def test_validate_untimed_word_warns(self):
        validate_response = _elevenlabs_validate_response
        raw = {
            "language_code": "en", "text": "hi",
            "words": [
                {"start": 0.0, "end": 0.3, "text": "hi", "type": "word", "speaker_id": "s0"},
                {"start": None, "end": None, "text": "x", "type": "word", "speaker_id": "s0"},
            ],
        }
        issues = validate_response(raw)
        assert any(i.code == "UNTIMED_WORD" and i.severity == "warning" for i in issues)


# =============================================================================
# Gladia — source: iron-benchmark/pipeline/tests/test_gladia_p66.py
# =============================================================================

def _fake_done(n_speakers: int = 2, *, with_speakers: bool = True) -> dict:
    """A done GET /v2/pre-recorded/{id} payload in the documented shape.

    Times SECONDS; utterance ``speaker`` is an INT by order of appearance and
    words carry NO speaker of their own (schema-verified 2026-07-09).
    """
    utterances, t = [], 0.5
    for si in range(n_speakers):
        words = []
        u_start = t
        for wi in range(3):
            words.append({"word": f"w{si}{wi}", "start": round(t, 3),
                          "end": round(t + 0.4, 3), "confidence": 0.9})
            t += 0.5
        utt = {"words": words, "text": " ".join(w["word"] for w in words),
               "language": "en", "start": u_start, "end": round(t - 0.1, 3),
               "confidence": 0.9, "channel": 0}
        if with_speakers:
            utt["speaker"] = si
        utterances.append(utt)
    return {
        "id": "45463597-20b7-4af7-b3b3-f5fb778203ab",
        "request_id": "G-45463597", "version": 2, "status": "done",
        "created_at": "2026-07-09T00:00:00.000Z",
        "completed_at": "2026-07-09T00:00:20.000Z",
        "kind": "pre-recorded",
        "file": {"id": "f-1", "filename": "a.wav", "source": None,
                 "audio_duration": 3.4, "number_of_channels": 1},
        "result": {
            "metadata": {"audio_duration": 3.4,
                         "number_of_distinct_channels": 1,
                         "billing_time": 3.4, "transcription_time": 1.2},
            "transcription": {
                "full_transcript": " ".join(u["text"] for u in utterances),
                "languages": ["en"],
                "utterances": utterances,
            },
        },
    }


class TestGladiaValidate:
    """Mirrors ``test_gladia_p66.py``'s validator edge-case coverage."""

    def test_validate_clean_and_edge_cases(self):
        validate_response = _gladia_validate_response
        assert validate_response(_fake_done(), expect_diarization=True) == []

        processing = _fake_done()
        processing["status"] = "processing"
        assert any(i.code == "NOT_DONE" and i.severity == "error"
                   for i in validate_response(processing))

        empty = _fake_done()
        empty["result"]["transcription"]["utterances"] = []
        assert any(i.code == "NO_UTTERANCES"
                   for i in validate_response(empty))

        no_spk = _fake_done(with_speakers=False)
        issues = validate_response(no_spk, expect_diarization=True)
        assert any(i.code == "NO_SPEAKER" and i.severity == "warning"
                   for i in issues)

        neg = _fake_done()
        neg["result"]["transcription"]["utterances"][0]["start"] = -1.0
        assert any(i.code == "NEGATIVE_TIMESTAMP" and i.severity == "error"
                   for i in validate_response(neg))

    def test_validate_speaker_zero_counts_as_present(self):
        validate_response = _gladia_validate_response
        # A lone speaker 0 is a VALID diarization result (int 0 is falsy!).
        one = _fake_done(1)
        assert not any(i.code == "NO_SPEAKER" for i in validate_response(one))


# =============================================================================
# Soniox — source: iron-benchmark/pipeline/tests/test_soniox_p76.py
# =============================================================================

def _fake_tokens() -> list[dict]:
    """Sub-word tokens in the documented shape (ms, string speaker, LID).

    "Beau|ti|ful" carries no leading spaces (one word), " day" starts with a
    space (new word), "." glues onto it, then the speaker changes to "2" with
    a German word split over two tokens.
    """
    return [
        {"text": "Beau", "start_ms": 300, "end_ms": 420, "confidence": 0.82,
         "speaker": "1", "language": "en"},
        {"text": "ti", "start_ms": 420, "end_ms": 540, "confidence": 0.87,
         "speaker": "1", "language": "en"},
        {"text": "ful", "start_ms": 540, "end_ms": 780, "confidence": 0.98,
         "speaker": "1", "language": "en"},
        {"text": " day", "start_ms": 800, "end_ms": 1000, "confidence": 0.9,
         "speaker": "1", "language": "en"},
        {"text": ".", "start_ms": 1000, "end_ms": 1010, "confidence": 1.0,
         "speaker": "1", "language": "en"},
        {"text": " Ja", "start_ms": 1200, "end_ms": 1300, "confidence": 0.8,
         "speaker": "2", "language": "de"},
        {"text": "wohl", "start_ms": 1300, "end_ms": 1500, "confidence": 0.85,
         "speaker": "2", "language": "de"},
    ]


def _fake_job(status: str = "completed", **extra) -> dict:
    return {"id": "t-1", "status": status, "model": "stt-async-v5",
            "filename": "a.wav", "audio_duration_ms": 4000,
            "enable_speaker_diarization": True,
            "enable_language_identification": True,
            "created_at": "2026-07-10T00:00:00Z", **extra}


def _fake_composite() -> dict:
    return {"transcription": _fake_job(),
            "transcript": {"id": "t-1", "text": "Beautiful day. Jawohl",
                           "tokens": _fake_tokens()},
            "cleanup": {"file_deleted": True, "transcription_deleted": True}}


class TestSonioxValidate:
    """Mirrors ``test_soniox_p76.py``'s validator edge-case coverage."""

    def test_validate_clean_and_edge_cases(self):
        validate_response = _soniox_validate_response
        assert validate_response(_fake_composite(), expect_diarization=True) == []

        empty = _fake_composite()
        empty["transcript"]["tokens"] = []
        assert any(i.code == "NO_TOKENS" and i.severity == "error"
                   for i in validate_response(empty))

        neg = _fake_composite()
        neg["transcript"]["tokens"][0]["start_ms"] = -5
        assert any(i.code == "NEGATIVE_TIMESTAMP" and i.severity == "error"
                   for i in validate_response(neg))

        inv = _fake_composite()
        inv["transcript"]["tokens"][0]["end_ms"] = 100    # < start_ms 300
        assert any(i.code == "TIMESTAMP_ORDER" for i in validate_response(inv))

    def test_validate_speaker_language_and_model_echo(self):
        validate_response = _soniox_validate_response
        no_spk = _fake_composite()
        for t in no_spk["transcript"]["tokens"]:
            t.pop("speaker", None)
        assert any(i.code == "NO_SPEAKER" for i in
                   validate_response(no_spk, expect_diarization=True))
        assert not any(i.code == "NO_SPEAKER" for i in
                       validate_response(no_spk, expect_diarization=False))

        no_lang = _fake_composite()
        for t in no_lang["transcript"]["tokens"]:
            t.pop("language", None)
        assert any(i.code == "NO_LANGUAGE" for i in validate_response(no_lang))

        drifted = _fake_composite()
        drifted["transcription"]["model"] = "stt-async-v6-preview"
        issues = validate_response(drifted, expected_model="stt-async-v5")
        assert any(i.code == "MODEL_MISMATCH" and i.severity == "warning"
                   for i in issues)


# =============================================================================
# Speechmatics — source: iron-benchmark/pipeline/tests/test_speechmatics_p73.py
# =============================================================================

def _word(content, start, end, *, speaker="S1", language="en",
          confidence=0.95) -> dict:
    return {"type": "word", "start_time": start, "end_time": end,
            "alternatives": [{"content": content, "confidence": confidence,
                              "language": language, "speaker": speaker}]}


def _eos(start, *, speaker="S1") -> dict:
    return {"type": "punctuation", "start_time": start, "end_time": start,
            "attaches_to": "previous", "is_eos": True,
            "alternatives": [{"content": ".", "confidence": 1.0,
                              "language": "en", "speaker": speaker}]}


def _fake_transcript(n_speakers: int = 2, *, with_speakers: bool = True,
                     model: str = "melia-1", language: str = "multi") -> dict:
    """A done GET /jobs/{id}/transcript payload in the documented 2.9 shape.

    Times SECONDS; the speaker is a STRING on alternatives[0] ("S1", "S2" by
    order of appearance, docs batch-diarization.md); each speaker's sentence
    ends with an is_eos period that attaches to the previous word.
    """
    results, t = [], 0.5
    for si in range(n_speakers):
        label = f"S{si + 1}" if with_speakers else "UU"
        lang = "en" if si == 0 else "de"
        for wi in range(3):
            results.append(_word(f"w{si}{wi}", round(t, 3), round(t + 0.4, 3),
                                 speaker=label, language=lang))
            t += 0.5
        results.append(_eos(round(t - 0.1, 3), speaker=label))
    return {
        "format": "2.9",
        "job": {"created_at": "2026-07-10T00:00:00.000Z",
                "data_name": "a.wav", "duration": 4, "id": "650krlru2e"},
        "metadata": {
            "created_at": "2026-07-10T00:00:20.000Z",
            "type": "transcription",
            "transcription_config": {"language": language, "model": model,
                                     "diarization": "speaker"},
        },
        "results": results,
    }


class TestSpeechmaticsValidate:
    """Mirrors ``test_speechmatics_p73.py``'s validator edge-case coverage."""

    def test_validate_clean_and_edge_cases(self):
        validate_response = _speechmatics_validate_response
        assert validate_response(_fake_transcript(), expect_diarization=True) == []

        empty = _fake_transcript()
        empty["results"] = []
        assert any(i.code == "NO_RESULTS" and i.severity == "error"
                   for i in validate_response(empty))

        neg = _fake_transcript()
        neg["results"][0]["start_time"] = -1.0
        assert any(i.code == "NEGATIVE_TIMESTAMP" and i.severity == "error"
                   for i in validate_response(neg))

        inv = _fake_transcript()
        inv["results"][0]["end_time"] = 0.1      # < start_time 0.5
        assert any(i.code == "TIMESTAMP_ORDER" for i in validate_response(inv))

    def test_validate_uu_only_flags_no_speaker(self):
        validate_response = _speechmatics_validate_response
        # An all-"UU" transcript has NO attributed speaker (docs: UU =
        # unidentified) - warn when diarization was expected.
        uu = _fake_transcript(1, with_speakers=False)
        issues = validate_response(uu, expect_diarization=True)
        assert any(i.code == "NO_SPEAKER" and i.severity == "warning"
                   for i in issues)
        assert not any(i.code == "NO_SPEAKER"
                       for i in validate_response(uu, expect_diarization=False))


# =============================================================================
# xAI STT — source: iron-benchmark/pipeline/tests/test_xai_stt_p76.py
# =============================================================================

def _fake_raw(*, with_speakers: bool = True, with_words: bool = True) -> dict:
    """A POST /v1/stt response in the documented shape (times SECONDS).

    The last word deliberately carries NO confidence — the field is
    omitted-when-0 by contract.
    """
    words = []
    if with_words:
        words = [
            {"text": "Hello", "start": 0.5, "end": 0.9, "confidence": 0.33},
            {"text": "there.", "start": 1.0, "end": 1.4, "confidence": 0.5},
            {"text": "General", "start": 2.0, "end": 2.4, "confidence": 0.6},
            {"text": "Kenobi.", "start": 2.5, "end": 3.0},
        ]
        if with_speakers:
            for i, w in enumerate(words):
                w["speaker"] = 0 if i < 2 else 1
    return {
        "text": "Hello there. General Kenobi.",
        "language": "",          # documented: currently ALWAYS empty
        "duration": 4.0,
        **({"words": words} if with_words else {}),
    }


class TestXaiSttValidate:
    """Mirrors ``test_xai_stt_p76.py``'s validator edge-case coverage."""

    def test_validate_clean_and_edge_cases(self):
        validate_response = _xai_stt_validate_response
        assert validate_response(_fake_raw(), expect_diarization=True) == []

        no_words = _fake_raw(with_words=False)
        issues = validate_response(no_words)
        assert any(i.code == "NO_WORDS" and i.severity == "warning"
                   for i in issues)
        assert not any(i.code == "NO_TEXT" for i in issues)   # text still present

        empty = {"text": "", "language": "", "duration": 1.0}
        assert any(i.code == "NO_TEXT" and i.severity == "error"
                   for i in validate_response(empty))

        neg = _fake_raw()
        neg["words"][0]["start"] = -0.5
        assert any(i.code == "NEGATIVE_TIMESTAMP" and i.severity == "error"
                   for i in validate_response(neg))

        inv = _fake_raw()
        inv["words"][0]["end"] = 0.1            # < start 0.5
        assert any(i.code == "TIMESTAMP_ORDER" for i in validate_response(inv))

    def test_validate_speaker_confidence_and_channels_flags(self):
        validate_response = _xai_stt_validate_response
        no_spk = _fake_raw(with_speakers=False)
        assert any(i.code == "NO_SPEAKER" for i in
                   validate_response(no_spk, expect_diarization=True))
        assert not any(i.code == "NO_SPEAKER" for i in
                       validate_response(no_spk, expect_diarization=False))

        no_conf = _fake_raw()
        for w in no_conf["words"]:
            w.pop("confidence", None)           # all omitted (=0 by contract)
        assert any(i.code == "NO_CONFIDENCE" for i in validate_response(no_conf))

        chan = _fake_raw()
        chan["channels"] = [{"index": 0, "text": "hi", "words": []}]
        assert any(i.code == "UNEXPECTED_CHANNELS" for i in
                   validate_response(chan))


# =============================================================================
# pyannote diarization — source: iron-benchmark/pipeline/tests/test_pyannote_hybrid_p84.py
# =============================================================================

def _job_payload(*, exclusive: bool = True) -> dict:
    """A succeeded GET /v1/jobs/{id} payload in the documented shape.

    Times SECONDS-float; speakers are SPEAKER_XX strings; the payload carries
    NO model echo (DiarizationJob schema, verified 2026-07-11).
    """
    out = {
        "diarization": [
            {"speaker": "SPEAKER_00", "start": 0.5, "end": 2.0},
            {"speaker": "SPEAKER_01", "start": 1.8, "end": 4.0},   # overlap zone
            {"speaker": "SPEAKER_00", "start": 4.0, "end": 5.0},
        ],
    }
    if exclusive:
        out["exclusiveDiarization"] = [
            {"speaker": "SPEAKER_00", "start": 0.5, "end": 1.9},
            {"speaker": "SPEAKER_01", "start": 1.9, "end": 4.0},
            {"speaker": "SPEAKER_00", "start": 4.0, "end": 5.0},
        ]
    return {"jobId": "job-p84", "status": "succeeded", "output": out,
            "_poll_count": 2}


class TestPyannoteDiarValidate:
    """Mirrors ``test_pyannote_hybrid_p84.py``'s validator coverage."""

    def test_validator_accepts_documented_payload(self):
        validate_response = _pyannote_validate_response
        issues = validate_response(_job_payload())
        assert [i for i in issues if i.severity == "error"] == []

    def test_validator_flags_empty_and_missing_diarization(self):
        validate_response = _pyannote_validate_response
        empty = _job_payload()
        empty["output"]["diarization"] = []
        codes = {i.code for i in validate_response(empty)}
        assert "empty_diarization" in codes
        missing = {"jobId": "j", "status": "succeeded", "output": {}}
        codes = {i.code for i in validate_response(missing)}
        assert "missing_diarization" in codes

    def test_validator_flags_exclusive_overlap_as_drift(self):
        validate_response = _pyannote_validate_response
        bad = _job_payload()
        bad["output"]["exclusiveDiarization"] = [
            {"speaker": "SPEAKER_00", "start": 0.0, "end": 2.0},
            {"speaker": "SPEAKER_01", "start": 1.5, "end": 3.0},
        ]
        codes = {i.code for i in validate_response(bad)}
        assert "exclusive_overlap" in codes
