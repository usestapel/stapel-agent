"""STT seam unit tests — AudioRef matrix, normalized-transcript schema
helpers and the shared audio download. Django-free module, no db."""
import pytest
import requests

from stapel_agent.stt.base import (
    AudioRef,
    NormalizedTranscript,
    NormalizedWord,
    RetryableTranscriptionError,
    TranscriptionError,
    normalize_language,
    transcript_from_dict,
    utterances_from_words,
)


class TestAudioRefValidation:
    def test_url_only_is_valid(self):
        assert AudioRef(url="https://cdn.test/a.mp3").kind == "url"

    def test_path_only_is_valid(self):
        assert AudioRef(path="/tmp/a.wav").kind == "path"

    def test_data_only_is_valid(self):
        assert AudioRef(data=b"RIFF").kind == "data"

    def test_none_of_the_three_is_rejected(self):
        with pytest.raises(ValueError, match="exactly one of url/path/data"):
            AudioRef()

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"url": "https://x/a", "path": "/tmp/a"},
            {"url": "https://x/a", "data": b"x"},
            {"path": "/tmp/a", "data": b"x"},
            {"url": "https://x/a", "path": "/tmp/a", "data": b"x"},
        ],
    )
    def test_more_than_one_is_rejected(self, kwargs):
        with pytest.raises(ValueError, match="exactly one of url/path/data"):
            AudioRef(**kwargs)

    def test_mime_is_a_free_hint(self):
        ref = AudioRef(data=b"x", mime="audio/ogg")
        assert ref.mime == "audio/ogg"

    def test_from_payload_audio_url_key(self):
        ref = AudioRef.from_payload({"audio_url": "https://x/a.mp3"})
        assert ref.url == "https://x/a.mp3"

    def test_from_payload_url_key(self):
        assert AudioRef.from_payload({"url": "https://x/a.mp3"}).kind == "url"

    def test_from_payload_empty_is_rejected(self):
        with pytest.raises(ValueError):
            AudioRef.from_payload({})


class TestAudioRefAccessors:
    def test_require_url_returns_url(self):
        ref = AudioRef(url="https://x/a.mp3")
        assert ref.require_url(provider="p") == "https://x/a.mp3"

    def test_require_url_on_bytes_is_fatal_and_names_provider(self):
        ref = AudioRef(data=b"x")
        with pytest.raises(TranscriptionError, match="requires an audio URL") as e:
            ref.require_url(provider="assemblyai")
        assert e.value.provider == "assemblyai"
        assert not isinstance(e.value, RetryableTranscriptionError)

    def test_read_bytes_from_data(self):
        assert AudioRef(data=b"abc").read_bytes(provider="p") == b"abc"

    def test_read_bytes_from_path(self, tmp_path):
        f = tmp_path / "a.wav"
        f.write_bytes(b"RIFFdata")
        assert AudioRef(path=str(f)).read_bytes(provider="p") == b"RIFFdata"

    def test_read_bytes_unreadable_path_is_fatal(self, tmp_path):
        ref = AudioRef(path=str(tmp_path / "missing.wav"))
        with pytest.raises(TranscriptionError, match="not readable") as e:
            ref.read_bytes(provider="p")
        assert not isinstance(e.value, RetryableTranscriptionError)

    def test_read_bytes_from_url_downloads(self, monkeypatch):
        captured = {}

        class Resp:
            content = b"audio-bytes"

            def raise_for_status(self):
                pass

        def fake_get(url, timeout=None, stream=None):
            captured.update(url=url, timeout=timeout)
            return Resp()

        monkeypatch.setattr("requests.get", fake_get)
        ref = AudioRef(url="https://cdn.test/a.mp3?X-Sig=s3cr3t")
        assert ref.read_bytes(provider="p", timeout=33) == b"audio-bytes"
        assert captured["url"] == "https://cdn.test/a.mp3?X-Sig=s3cr3t"
        assert captured["timeout"] == 33

    def _mock_get(self, monkeypatch, *, status=None, exc=None):
        class Resp:
            status_code = status
            content = b""

            def raise_for_status(self):
                error = requests.HTTPError(f"{status}")
                error.response = self
                raise error

        def fake_get(url, timeout=None, stream=None):
            if exc is not None:
                raise exc
            return Resp()

        monkeypatch.setattr("requests.get", fake_get)

    def test_download_404_is_fatal(self, monkeypatch):
        self._mock_get(monkeypatch, status=404)
        with pytest.raises(TranscriptionError, match="not retrievable: 404") as e:
            AudioRef(url="https://x/a").read_bytes(provider="p")
        assert not isinstance(e.value, RetryableTranscriptionError)
        assert e.value.status_code == 404

    def test_download_500_is_retryable(self, monkeypatch):
        self._mock_get(monkeypatch, status=503)
        with pytest.raises(RetryableTranscriptionError):
            AudioRef(url="https://x/a").read_bytes(provider="p")

    def test_download_timeout_is_retryable(self, monkeypatch):
        self._mock_get(monkeypatch, exc=requests.Timeout("slow"))
        with pytest.raises(RetryableTranscriptionError, match="timed out"):
            AudioRef(url="https://x/a").read_bytes(provider="p")

    def test_download_connection_error_is_retryable(self, monkeypatch):
        self._mock_get(monkeypatch, exc=requests.ConnectionError("refused"))
        with pytest.raises(RetryableTranscriptionError):
            AudioRef(url="https://x/a").read_bytes(provider="p")


class TestAudioRefDescribe:
    def test_url_describe_drops_signed_query(self):
        ref = AudioRef(url="https://minio.test:9000/bucket/a.mp3?X-Sig=s3cr3t")
        assert ref.describe() == "url:minio.test:9000"
        assert "s3cr3t" not in ref.describe()

    def test_path_describe_is_basename_only(self):
        assert AudioRef(path="/very/private/dir/a.wav").describe() == "path:a.wav"

    def test_data_describe_is_length_only(self):
        assert AudioRef(data=b"12345").describe() == "data:5b"


class TestSttProviderAbc:
    def test_base_transcribe_is_abstract(self):
        from stapel_agent.stt.base import SttProvider
        from stapel_agent.tests.fakes import FakeSttProvider

        with pytest.raises(NotImplementedError):
            SttProvider.transcribe(FakeSttProvider(), audio=AudioRef(data=b"x"))


class TestSpeechModelPin:
    """G6 — per-registration ``speech_model`` pin on the SttProvider ABC."""

    def test_base_defaults_are_unpinned(self):
        from stapel_agent.tests.fakes import FakeSttProvider

        p = FakeSttProvider()
        # no pin, no settings-backed default → effective model is None
        assert p.speech_model is None
        assert p.default_speech_model() is None
        assert p.effective_model() is None

    def test_pin_overrides_configured_default(self):
        from stapel_agent.tests.fakes import PinnedSttProvider

        p = PinnedSttProvider()
        assert p.default_speech_model() == "configured-default"
        # the class-attr pin wins over the configured default
        assert p.effective_model() == "pinned-model-x"

    def test_clearing_the_pin_falls_back_to_default(self):
        from stapel_agent.tests.fakes import PinnedSttProvider

        class Unpinned(PinnedSttProvider):
            speech_model = None

        assert Unpinned().effective_model() == "configured-default"

    def test_pin_is_per_registration_not_global(self):
        from stapel_agent.tests.fakes import PinnedSttProvider

        class OtherPin(PinnedSttProvider):
            speech_model = "other-model-y"

        # two registrations of the same adapter carry different models
        assert PinnedSttProvider().effective_model() == "pinned-model-x"
        assert OtherPin().effective_model() == "other-model-y"


class TestNormalizeLanguage:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (None, None),
            ("", None),
            ("en", "en"),
            ("EN", "en"),
            ("en-US", "en"),
            ("en_us", "en"),
            ("ru-RU", "ru"),
        ],
    )
    def test_bcp47_to_iso639(self, raw, expected):
        assert normalize_language(raw) == expected


class TestTranscriptSchema:
    def _transcript(self):
        words = [
            NormalizedWord(text="Hi", start=0.0, end=0.4, speaker="A"),
            NormalizedWord(text="there", start=0.4, end=0.9, speaker="A"),
            NormalizedWord(text="Hello", start=1.0, end=1.5, speaker="B"),
        ]
        return NormalizedTranscript(
            provider="fake-stt",
            language="en",
            duration_seconds=1.5,
            words=words,
            utterances=utterances_from_words(words),
            speakers_detected=["A", "B"],
            raw={"upstream": True},
        )

    def test_utterances_from_words_groups_by_speaker(self):
        utts = self._transcript().utterances
        assert [(u.speaker, u.text) for u in utts] == [
            ("A", "Hi there"),
            ("B", "Hello"),
        ]
        assert utts[0].word_indexes == [0, 1]
        assert utts[1].word_indexes == [2]
        assert (utts[0].start, utts[0].end) == (0.0, 0.9)

    def test_utterances_from_words_empty(self):
        assert utterances_from_words([]) == []

    def test_text_prefers_utterances(self):
        assert self._transcript().text == "Hi there\nHello"

    def test_text_falls_back_to_words(self):
        t = self._transcript()
        t.utterances = []
        assert t.text == "Hi there Hello"

    def test_to_dict_from_dict_roundtrip(self):
        t = self._transcript()
        back = transcript_from_dict(t.to_dict())
        assert back == t

    def test_from_dict_tolerates_missing_optionals(self):
        t = transcript_from_dict({"provider": "x"})
        assert t.language is None
        assert t.words == [] and t.utterances == []
        assert t.raw == {}

    def test_from_dict_rejects_unknown_word_keys(self):
        with pytest.raises(TypeError):
            transcript_from_dict(
                {"provider": "x", "words": [{"text": "hi", "beep": 1}]}
            )
