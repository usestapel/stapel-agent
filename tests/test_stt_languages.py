"""One spelling of a language code, decided at the boundary.

The audit these are written against found ``en``/``eng``, ``ru``/``rus``,
``es``/``spa`` and ``pt-BR`` arriving at one deployment inside a week, and
three consequences: a language route that did not fire for the
three-letter spelling, two checkpoint keys for one piece of audio, and a
ledger nobody could group by language.
"""
import pytest

from stapel_agent import services
from stapel_agent.models import PromptLog
from stapel_agent.stt import languages
from stapel_agent.stt.base import AudioRef, normalize_language
from stapel_agent.stt.languages import UnknownLanguageError, canonical_language
from stapel_agent.stt.router import select_chain
from stapel_agent.tests.fakes import FakeSttProvider, SecondSttProvider

AUDIO = AudioRef(url="https://minio.test/bucket/rec.mp3")


class TestAliasTable:
    def test_every_639_1_code_maps_back_from_its_639_2_codes(self):
        # The two lookups are derived from one table, so this is the
        # property that the derivation is right — not a restatement of it.
        for one, threes in languages.ISO_639_2_BY_1.items():
            assert len(one) == 2, one
            for three in threes:
                assert len(three) == 3, three
                assert languages.ISO_639_1_BY_2[three] == one

    def test_the_table_covers_the_whole_639_1_register(self):
        # 184 codes; a row silently dropped in an edit is a language that
        # starts being refused.
        assert len(languages.ISO_639_1) == 184

    @pytest.mark.parametrize(
        "code,expected",
        [
            # 639-2/T — the terminological code, what most SDKs send.
            ("eng", "en"), ("rus", "ru"), ("spa", "es"), ("deu", "de"),
            ("fra", "fr"), ("zho", "zh"), ("ces", "cs"), ("nld", "nl"),
            ("ell", "el"), ("isl", "is"), ("mkd", "mk"), ("ron", "ro"),
            ("slk", "sk"), ("sqi", "sq"), ("hye", "hy"), ("kat", "ka"),
            ("mri", "mi"), ("msa", "ms"), ("mya", "my"), ("fas", "fa"),
            ("bod", "bo"), ("cym", "cy"), ("eus", "eu"),
        ],
    )
    def test_639_2_t_resolves(self, code, expected):
        assert canonical_language(code) == expected

    @pytest.mark.parametrize(
        "code,expected",
        [
            # 639-2/B — the bibliographic code, which differs for exactly
            # twenty languages and is the half an ad-hoc "first two
            # letters" rule gets wrong every time (``ger`` is not ``ge``).
            ("ger", "de"), ("fre", "fr"), ("chi", "zh"), ("cze", "cs"),
            ("dut", "nl"), ("gre", "el"), ("ice", "is"), ("mac", "mk"),
            ("rum", "ro"), ("slo", "sk"), ("alb", "sq"), ("arm", "hy"),
            ("geo", "ka"), ("mao", "mi"), ("may", "ms"), ("bur", "my"),
            ("per", "fa"), ("tib", "bo"), ("wel", "cy"), ("baq", "eu"),
        ],
    )
    def test_639_2_b_resolves(self, code, expected):
        assert canonical_language(code) == expected

    def test_b_and_t_codes_agree(self):
        for one, threes in languages.ISO_639_2_BY_1.items():
            assert {canonical_language(t) for t in threes} == {one}

    @pytest.mark.parametrize(
        "code,expected",
        [("iw", "he"), ("in", "id"), ("ji", "yi"), ("jw", "jv"),
         ("mo", "ro"), ("mol", "ro")],
    )
    def test_withdrawn_codes_real_clients_still_send(self, code, expected):
        assert canonical_language(code) == expected


class TestCanonicalForm:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("en", "en"), ("EN", "en"), (" En ", "en"),
            ("ENG", "en"), ("Rus", "ru"),
            ("pt-BR", "pt-BR"), ("pt-br", "pt-BR"), ("pt_br", "pt-BR"),
            ("PT_BR", "pt-BR"),
            # The alias keeps the region it arrived with.
            ("por-br", "pt-BR"), ("spa-MX", "es-MX"),
            # Script subtags are title-cased, regions upper, per BCP-47.
            ("zh-hans", "zh-Hans"), ("zh-hans-cn", "zh-Hans-CN"),
            ("sr-latn-rs", "sr-Latn-RS"),
        ],
    )
    def test_shapes(self, raw, expected):
        assert canonical_language(raw) == expected

    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_absence_is_not_an_unknown_code(self, value):
        # No hint means auto-detect. Refusing it would break every caller
        # that does not know the language, which is most of them.
        assert canonical_language(value) is None

    @pytest.mark.parametrize("value", ["xx", "qqq", "english", "42", "zz-ZZ"])
    def test_unknown_is_refused_by_name(self, value):
        with pytest.raises(UnknownLanguageError) as exc:
            canonical_language(value)
        assert exc.value.code == value.strip()
        assert exc.value.reason == "language"
        # The refusal names the escape hatch — a message that only says
        # "unknown" leaves the operator with nothing to do about it.
        assert "STT_LANGUAGE_ALIASES" in str(exc.value)

    def test_only_the_primary_subtag_is_validated(self):
        # Deliberate: the primary subtag is what routes, what keys the
        # checkpoint and what a provider reads. Everything after it is
        # carried through cased but unjudged — this package is not a
        # BCP-47 registry, and refusing a private-use subtag it does not
        # recognise would break callers for no gain.
        assert canonical_language("en-x-whatever") == "en-x-whatever"

    def test_it_is_a_fatal_transcription_error(self):
        from stapel_agent.stt.base import (
            RetryableTranscriptionError,
            TranscriptionError,
        )

        assert issubclass(UnknownLanguageError, TranscriptionError)
        assert not issubclass(UnknownLanguageError, RetryableTranscriptionError)


class TestHostAliases:
    def test_a_host_alias_resolves(self, settings):
        settings.STAPEL_AGENT = {
            **getattr(settings, "STAPEL_AGENT", {}),
            "STT_LANGUAGE_ALIASES": {"cmn": "zh", "yue": "zh-HK"},
        }
        assert canonical_language("cmn") == "zh"
        assert canonical_language("yue") == "zh-HK"
        # And is held to the same shape rules as anything else.
        assert canonical_language("YUE") == "zh-HK"

    def test_a_host_alias_on_the_primary_subtag_keeps_the_region(self, settings):
        settings.STAPEL_AGENT = {
            **getattr(settings, "STAPEL_AGENT", {}),
            "STT_LANGUAGE_ALIASES": {"cmn": "zh"},
        }
        assert canonical_language("cmn-hans") == "zh-Hans"

    def test_a_broken_alias_still_refuses(self, settings):
        settings.STAPEL_AGENT = {
            **getattr(settings, "STAPEL_AGENT", {}),
            "STT_LANGUAGE_ALIASES": {"cmn": "not-a-language"},
        }
        with pytest.raises(UnknownLanguageError):
            canonical_language("cmn")


class TestAdapterHelper:
    """``normalize_language`` is what the adapters call; it must not start
    raising, and it must stop handing three-letter codes to providers that
    only understand two."""

    @pytest.mark.parametrize(
        "raw,expected",
        [("eng", "en"), ("ger", "de"), ("pt-BR", "pt"), ("en_US", "en"),
         ("RUS", "ru"), (None, None), ("", None)],
    )
    def test_it_resolves_aliases_now(self, raw, expected):
        assert normalize_language(raw) == expected

    def test_it_stays_lenient(self):
        # The refusal lives at the boundary. An adapter reached with an
        # odd code degrades exactly as it did before this release.
        assert normalize_language("xx") == "xx"
        assert normalize_language("qqq-ZZ") == "qqq"


class TestRouting:
    def test_a_three_letter_code_takes_the_same_route(self, settings, fake_stt):
        settings.STAPEL_AGENT = {
            **settings.STAPEL_AGENT,
            "STT_LANGUAGE_ROUTES": {"ru": ["fake-stt-2"]},
        }
        # THE DEFECT: before 0.25.0 only the first of these fired, and the
        # other two fell through to the default chain unremarked.
        assert select_chain("ru") == ["fake-stt-2"]
        assert select_chain("rus") == ["fake-stt-2"]
        assert select_chain("RUS") == ["fake-stt-2"]

    def test_the_routes_dict_is_canonicalised_too(self, settings, fake_stt):
        settings.STAPEL_AGENT = {
            **settings.STAPEL_AGENT,
            "STT_LANGUAGE_ROUTES": {"eng": ["fake-stt-2"]},
        }
        assert select_chain("en") == ["fake-stt-2"]

    def test_a_region_can_route_on_its_own(self, settings, fake_stt):
        settings.STAPEL_AGENT = {
            **settings.STAPEL_AGENT,
            "STT_LANGUAGE_ROUTES": {"pt-BR": ["fake-stt-2"], "pt": ["fake-stt"]},
        }
        assert select_chain("pt-BR") == ["fake-stt-2"]
        assert select_chain("pt-PT") == ["fake-stt"]
        assert select_chain("por") == ["fake-stt"]

    def test_an_unroutable_code_does_not_crash_the_router(self, settings, fake_stt):
        settings.STAPEL_AGENT = {
            **settings.STAPEL_AGENT,
            "STT_LANGUAGE_ROUTES": {"ru": ["fake-stt-2"]},
        }
        # The boundary refuses it; the router must not turn a routing
        # question into an exception on the way there.
        assert select_chain("xx") == ["fake-stt"]

    def test_an_explicit_provider_still_wins(self, settings, fake_stt):
        settings.STAPEL_AGENT = {
            **settings.STAPEL_AGENT,
            "STT_LANGUAGE_ROUTES": {"ru": ["fake-stt-2"]},
        }
        assert select_chain("rus", provider="fatal-stt") == ["fatal-stt"]


@pytest.mark.django_db
class TestBoundary:
    def test_the_adapter_and_the_row_both_see_the_canonical_code(self, fake_stt):
        result = services.transcribe(AUDIO, language="ENG")

        assert result["status"] == "ok"
        assert FakeSttProvider.calls[0]["language"] == "en"
        row = PromptLog.objects.get()
        assert row.metadata["language"] == "en"

    def test_the_region_survives_to_the_row(self, fake_stt):
        services.transcribe(AUDIO, language="pt_br")
        assert PromptLog.objects.get().metadata["language"] == "pt-BR"

    def test_two_spellings_share_one_checkpoint(self, fake_stt):
        digest = "sha256:" + "b" * 64
        first = services.transcribe(AUDIO, language="en", audio_content_hash=digest)
        second = services.transcribe(AUDIO, language="eng", audio_content_hash=digest)

        assert first["cached"] is False
        # One piece of audio, one language, one paid call — whichever
        # spelling the caller happened to use.
        assert second["cached"] is True
        assert len(FakeSttProvider.calls) == 1

    def test_an_unknown_code_is_refused_before_any_provider_is_called(
        self, fake_stt
    ):
        result = services.transcribe(AUDIO, language="xx")

        assert result["status"] == "failure"
        assert "unknown language code 'xx'" in result["reason"]
        # Not "transcribed in the wrong language": nothing was bought.
        assert FakeSttProvider.calls == []
        assert SecondSttProvider.calls == []
