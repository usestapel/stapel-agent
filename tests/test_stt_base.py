"""STT seam unit tests — AudioRef matrix, normalized-transcript schema
helpers and the shared audio download. Django-free module, no db."""
import socket

import pytest
from stapel_core.net import safe_fetch

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
from stapel_agent.tests.fakes import (
    FakeHttpResponse,
    addrinfo,
    allow_any_audio_host,
    serve_audio,
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
        seen = serve_audio(monkeypatch, b"audio-bytes")
        ref = AudioRef(url="https://cdn.test/a.mp3?X-Sig=s3cr3t")
        assert ref.read_bytes(provider="p", timeout=33) == b"audio-bytes"
        assert seen == [("cdn.test", "93.184.216.34", "/a.mp3?X-Sig=s3cr3t")]

    def _serve_status(self, monkeypatch, status):
        serve_audio(monkeypatch, lambda: FakeHttpResponse(status, {}, b""))

    def test_download_404_is_fatal(self, monkeypatch):
        self._serve_status(monkeypatch, 404)
        with pytest.raises(TranscriptionError, match="not retrievable: 404") as e:
            AudioRef(url="https://x.test/a").read_bytes(provider="p")
        assert not isinstance(e.value, RetryableTranscriptionError)
        assert e.value.status_code == 404

    def test_download_500_is_retryable(self, monkeypatch):
        self._serve_status(monkeypatch, 503)
        with pytest.raises(RetryableTranscriptionError):
            AudioRef(url="https://x.test/a").read_bytes(provider="p")

    def test_download_timeout_is_retryable(self, monkeypatch):
        def stall():
            raise TimeoutError("slow")

        serve_audio(monkeypatch, stall)
        with pytest.raises(RetryableTranscriptionError, match="timed out"):
            AudioRef(url="https://x.test/a").read_bytes(provider="p")

    def test_download_connection_error_is_retryable(self, monkeypatch):
        def refused():
            raise ConnectionRefusedError("refused")

        serve_audio(monkeypatch, refused)
        with pytest.raises(RetryableTranscriptionError):
            AudioRef(url="https://x.test/a").read_bytes(provider="p")


class TestAudioDownloadGuards:
    """AGENT-01: the audio URL is caller-supplied, so the download is an
    SSRF and memory sink. Every guard below belongs to
    ``stapel_core.net.fetch_bytes``; these tests prove this package
    actually routes through it instead of hand-rolling ``requests.get``.
    """

    def test_non_https_url_is_refused_without_touching_the_network(
        self, monkeypatch
    ):
        def no_dns(*a, **kw):
            raise AssertionError("resolution attempted for a rejected scheme")

        allow_any_audio_host(monkeypatch)  # the scheme is the subject here
        monkeypatch.setattr(socket, "getaddrinfo", no_dns)
        with pytest.raises(TranscriptionError, match="scheme_not_https") as e:
            AudioRef(url="http://cdn.test/a.mp3").read_bytes(provider="p")
        assert not isinstance(e.value, RetryableTranscriptionError)

    @pytest.mark.parametrize(
        "ip", ["127.0.0.1", "10.0.0.9", "169.254.169.254", "::1"]
    )
    def test_private_and_metadata_addresses_are_refused(self, monkeypatch, ip):
        def open_should_not_run(*a, **kw):
            raise AssertionError("connected to a forbidden address")

        serve_audio(monkeypatch, ip=ip)
        monkeypatch.setattr(safe_fetch, "_open", open_should_not_run)
        with pytest.raises(TranscriptionError, match="blocked_ip") as e:
            AudioRef(url="https://internal.test/a.mp3").read_bytes(provider="p")
        assert not isinstance(e.value, RetryableTranscriptionError)

    def test_oversize_body_is_refused(self, monkeypatch, settings):
        settings.STAPEL_AGENT = {"STT_DOWNLOAD_MAX_BYTES": 1024}
        serve_audio(monkeypatch, b"A" * 5000)
        with pytest.raises(TranscriptionError, match="too_large") as e:
            AudioRef(url="https://cdn.test/big.mp3").read_bytes(provider="p")
        assert not isinstance(e.value, RetryableTranscriptionError)

    def test_redirect_into_private_space_is_refused(self, monkeypatch):
        import socket as _socket

        hops = iter(["93.184.216.34", "10.0.0.9"])
        allow_any_audio_host(monkeypatch)  # the redirect hop is the subject
        monkeypatch.setattr(
            _socket,
            "getaddrinfo",
            lambda host, port, **kw: addrinfo(next(hops), port),
        )
        monkeypatch.setattr(
            safe_fetch,
            "_open",
            lambda *a, **kw: FakeHttpResponse(
                302, {"Location": "https://internal.test/a.mp3"}, b""
            ),
        )
        with pytest.raises(TranscriptionError, match="blocked_ip"):
            AudioRef(url="https://cdn.test/a.mp3").read_bytes(provider="p")

    def test_caller_timeout_cannot_raise_the_configured_deadline(
        self, monkeypatch, settings
    ):
        settings.STAPEL_AGENT = {"STT_DOWNLOAD_TOTAL_DEADLINE": 12.0}
        captured = {}
        real_fetch = safe_fetch.fetch_bytes

        def spy(url, **kwargs):
            captured.update(kwargs)
            return real_fetch(url, **kwargs)

        monkeypatch.setattr("stapel_core.net.fetch_bytes", spy)
        serve_audio(monkeypatch, b"ok")
        # 600s is what the pre-audit adapters asked for.
        AudioRef(url="https://cdn.test/a.mp3").read_bytes(provider="p", timeout=600)
        assert captured["total_deadline"] == 12.0
        assert captured["timeout"] <= 12.0
        assert captured["max_bytes"] > 0

    def test_allowed_hosts_setting_pins_the_origin(self, monkeypatch, settings):
        settings.STAPEL_AGENT = {"STT_DOWNLOAD_ALLOWED_HOSTS": ["store.test"]}
        serve_audio(monkeypatch, b"ok")
        assert (
            AudioRef(url="https://store.test/a.mp3").read_bytes(provider="p") == b"ok"
        )
        with pytest.raises(TranscriptionError, match="host_not_allowed"):
            AudioRef(url="https://elsewhere.test/a.mp3").read_bytes(provider="p")

    def test_total_deadline_breach_is_retryable(self, monkeypatch):
        """A worker held past the deadline is released, not condemned: the
        ref may be fine and the next provider deserves its turn."""
        from stapel_core.net import SafeFetchError

        def too_slow(*a, **kw):
            raise SafeFetchError("deadline_exceeded", "exceeded 1.0s total deadline")

        serve_audio(monkeypatch)
        monkeypatch.setattr(safe_fetch, "_open", too_slow)
        with pytest.raises(RetryableTranscriptionError, match="timed out"):
            AudioRef(url="https://cdn.test/a.mp3").read_bytes(provider="p")

    def test_unresolvable_host_is_retryable_not_a_refusal(self, monkeypatch):
        """Only a refusal by the guard is a verdict on the ref. DNS failure is
        transport — treating it as fatal would stop the fallback chain on a
        resolver hiccup."""
        def no_such_host(*a, **kw):
            raise socket.gaierror("nodename nor servname provided")

        allow_any_audio_host(monkeypatch)  # DNS is the subject here
        monkeypatch.setattr(socket, "getaddrinfo", no_such_host)
        with pytest.raises(
            RetryableTranscriptionError, match="dns_resolution_failed"
        ):
            AudioRef(url="https://gone.test/a.mp3").read_bytes(provider="p")

    def test_caps_still_apply_when_settings_are_unavailable(self, monkeypatch):
        """Outside a configured Django process the caps fall back to the
        shipped defaults — an unconfigured process fetches under a cap rather
        than under none."""
        from stapel_agent import conf
        from stapel_agent.stt.base import _limit

        class Unconfigured:
            defaults = conf.agent_settings.defaults

            def __getattr__(self, name):
                raise RuntimeError("settings are not configured")

        monkeypatch.setattr(conf, "agent_settings", Unconfigured())
        assert _limit("STT_DOWNLOAD_MAX_BYTES") == 128 * 1024 * 1024
        assert _limit("STT_DOWNLOAD_TOTAL_DEADLINE") == 300.0


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
