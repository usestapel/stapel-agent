"""One canonical spelling for a language code, decided at the boundary.

THE DEFECT THIS EXISTS FOR
    A production audit of a client stand found the same language arriving
    under four spellings inside one week: ``en`` from the web client,
    ``eng`` from a mobile SDK that speaks ISO 639-2, ``ru`` from one
    uploader and ``rus`` from another, ``pt-BR`` from a third. Nothing in
    the package reconciled them, and three separate things went wrong:

    * ``STT_LANGUAGE_ROUTES`` is keyed by the host in one spelling. A
      route stated as ``{"ru": [...]}`` simply did not fire for ``rus``:
      the request fell through to the default chain and was transcribed
      by the wrong engine, silently and correctly-looking.
    * the checkpoint key carries the language (:mod:`stapel_agent.checkpoint`),
      so ``eng`` and ``en`` are two different paid calls for one piece of
      audio.
    * the ledger stored whatever arrived, so "how much Russian did we
      transcribe" is a question the table cannot answer without a
      spelling-variant list nobody maintains.

    Adapters were already lenient about this — every one of them calls
    ``normalize_language``, which lowercased and dropped the region — but
    leniency at the LAST layer only cleans up what the provider sees. The
    routing decision, the checkpoint key and the telemetry row are all
    taken BEFORE that, from the raw string.

THE RULE
    One canonical form, produced once, at the function boundary
    (:func:`stapel_agent.services.transcribe`) and again inside the route
    lookup: ISO 639-1, lowercase, with the region kept as ``xx-YY`` when
    the caller sent one. Every subtag after the first is cased by the
    BCP-47 convention — region upper (``pt-BR``), script title
    (``zh-Hans``), everything else lower. ``eng`` routes exactly like
    ``en`` because by the time anything looks at it, it IS ``en``.

    A code that is neither a 639-1 code, a known 639-2 alias nor a host
    alias is REFUSED — :class:`UnknownLanguageError`, a fatal
    ``TranscriptionError`` with ``reason="language"``. Guessing is the
    one option not on the table: a typo silently transcribed as
    auto-detect is how a Russian meeting comes back in English with no
    error anywhere.

THE ESCAPE HATCH
    ``STAPEL_AGENT["STT_LANGUAGE_ALIASES"]`` maps any extra code a
    deployment's clients or providers use onto a canonical one, e.g.
    ``{"cmn": "zh", "yue": "zh-HK"}``. ISO 639-3 has thousands of codes
    with no 639-1 equivalent and this package is not their registry; a
    host that needs one states it where a reviewer can see it.
"""
from __future__ import annotations

from typing import Optional

from .base import TranscriptionError

#: Every ISO 639-1 code, mapped to its ISO 639-2 code(s): the 639-2/T
#: (terminological) code first, and the 639-2/B (bibliographic) code
#: second for the twenty languages where the two differ. Written as one
#: table in this direction because that is the direction a human can
#: check against the ISO register; both lookups below are derived from it,
#: so the two can never disagree.
ISO_639_2_BY_1: dict[str, tuple[str, ...]] = {
    "aa": ("aar",), "ab": ("abk",), "ae": ("ave",), "af": ("afr",),
    "ak": ("aka",), "am": ("amh",), "an": ("arg",), "ar": ("ara",),
    "as": ("asm",), "av": ("ava",), "ay": ("aym",), "az": ("aze",),
    "ba": ("bak",), "be": ("bel",), "bg": ("bul",), "bh": ("bih",),
    "bi": ("bis",), "bm": ("bam",), "bn": ("ben",), "bo": ("bod", "tib"),
    "br": ("bre",), "bs": ("bos",), "ca": ("cat",), "ce": ("che",),
    "ch": ("cha",), "co": ("cos",), "cr": ("cre",), "cs": ("ces", "cze"),
    "cu": ("chu",), "cv": ("chv",), "cy": ("cym", "wel"), "da": ("dan",),
    "de": ("deu", "ger"), "dv": ("div",), "dz": ("dzo",), "ee": ("ewe",),
    "el": ("ell", "gre"), "en": ("eng",), "eo": ("epo",), "es": ("spa",),
    "et": ("est",), "eu": ("eus", "baq"), "fa": ("fas", "per"),
    "ff": ("ful",), "fi": ("fin",), "fj": ("fij",), "fo": ("fao",),
    "fr": ("fra", "fre"), "fy": ("fry",), "ga": ("gle",), "gd": ("gla",),
    "gl": ("glg",), "gn": ("grn",), "gu": ("guj",), "gv": ("glv",),
    "ha": ("hau",), "he": ("heb",), "hi": ("hin",), "ho": ("hmo",),
    "hr": ("hrv",), "ht": ("hat",), "hu": ("hun",), "hy": ("hye", "arm"),
    "hz": ("her",), "ia": ("ina",), "id": ("ind",), "ie": ("ile",),
    "ig": ("ibo",), "ii": ("iii",), "ik": ("ipk",), "io": ("ido",),
    "is": ("isl", "ice"), "it": ("ita",), "iu": ("iku",), "ja": ("jpn",),
    "jv": ("jav",), "ka": ("kat", "geo"), "kg": ("kon",), "ki": ("kik",),
    "kj": ("kua",), "kk": ("kaz",), "kl": ("kal",), "km": ("khm",),
    "kn": ("kan",), "ko": ("kor",), "kr": ("kau",), "ks": ("kas",),
    "ku": ("kur",), "kv": ("kom",), "kw": ("cor",), "ky": ("kir",),
    "la": ("lat",), "lb": ("ltz",), "lg": ("lug",), "li": ("lim",),
    "ln": ("lin",), "lo": ("lao",), "lt": ("lit",), "lu": ("lub",),
    "lv": ("lav",), "mg": ("mlg",), "mh": ("mah",), "mi": ("mri", "mao"),
    "mk": ("mkd", "mac"), "ml": ("mal",), "mn": ("mon",), "mr": ("mar",),
    "ms": ("msa", "may"), "mt": ("mlt",), "my": ("mya", "bur"),
    "na": ("nau",), "nb": ("nob",), "nd": ("nde",), "ne": ("nep",),
    "ng": ("ndo",), "nl": ("nld", "dut"), "nn": ("nno",), "no": ("nor",),
    "nr": ("nbl",), "nv": ("nav",), "ny": ("nya",), "oc": ("oci",),
    "oj": ("oji",), "om": ("orm",), "or": ("ori",), "os": ("oss",),
    "pa": ("pan",), "pi": ("pli",), "pl": ("pol",), "ps": ("pus",),
    "pt": ("por",), "qu": ("que",), "rm": ("roh",), "rn": ("run",),
    "ro": ("ron", "rum"), "ru": ("rus",), "rw": ("kin",), "sa": ("san",),
    "sc": ("srd",), "sd": ("snd",), "se": ("sme",), "sg": ("sag",),
    "si": ("sin",), "sk": ("slk", "slo"), "sl": ("slv",), "sm": ("smo",),
    "sn": ("sna",), "so": ("som",), "sq": ("sqi", "alb"), "sr": ("srp",),
    "ss": ("ssw",), "st": ("sot",), "su": ("sun",), "sv": ("swe",),
    "sw": ("swa",), "ta": ("tam",), "te": ("tel",), "tg": ("tgk",),
    "th": ("tha",), "ti": ("tir",), "tk": ("tuk",), "tl": ("tgl",),
    "tn": ("tsn",), "to": ("ton",), "tr": ("tur",), "ts": ("tso",),
    "tt": ("tat",), "tw": ("twi",), "ty": ("tah",), "ug": ("uig",),
    "uk": ("ukr",), "ur": ("urd",), "uz": ("uzb",), "ve": ("ven",),
    "vi": ("vie",), "vo": ("vol",), "wa": ("wln",), "wo": ("wol",),
    "xh": ("xho",), "yi": ("yid",), "yo": ("yor",), "za": ("zha",),
    "zh": ("zho", "chi"), "zu": ("zul",),
}

#: The 639-1 codes themselves — what a canonical primary subtag may be.
ISO_639_1: frozenset[str] = frozenset(ISO_639_2_BY_1)

#: Every three-letter code (both 639-2/T and 639-2/B) → its 639-1 code.
#: Derived, never hand-maintained: a row added above is routable here the
#: same instant.
ISO_639_1_BY_2: dict[str, str] = {
    three: one for one, codes in ISO_639_2_BY_1.items() for three in codes
}

#: Codes withdrawn or replaced by ISO/IETF that real clients still send.
#: ``iw``/``in``/``ji``/``jw`` are the pre-1989 codes the JDK emitted for
#: decades (and Android still does in places); ``mo`` was withdrawn in
#: favour of ``ro``. Kept apart from the ISO table above so the table
#: stays a copy of the register rather than a copy plus folklore.
LEGACY_ALIASES: dict[str, str] = {
    "iw": "he",
    "in": "id",
    "ji": "yi",
    "jw": "jv",
    "mo": "ro",
    "mol": "ro",
    "scr": "hr",
    "scc": "sr",
}


class UnknownLanguageError(TranscriptionError):
    """The caller named a language this package cannot resolve.

    Fatal by construction: no provider in the fallback chain would make
    sense of a code we could not, and the alternative — dropping the hint
    and letting the provider auto-detect — is the silent wrong answer
    this module exists to prevent. ``reason="language"``.

    A deployment whose clients legitimately use a code outside ISO 639-1
    (a 639-3 macrolanguage member, a provider's own spelling) states it
    in ``STAPEL_AGENT["STT_LANGUAGE_ALIASES"]``; the message says so.
    """

    def __init__(self, code: str, *, provider: str = ""):
        self.code = code
        super().__init__(
            f"unknown language code {code!r} — expected an ISO 639-1 code "
            "(optionally with a region, e.g. 'pt-BR') or an ISO 639-2 "
            "alias of one. Map deployment-specific codes in "
            "STAPEL_AGENT['STT_LANGUAGE_ALIASES'].",
            provider=provider,
            reason="language",
        )


def host_aliases() -> dict[str, str]:
    """``STT_LANGUAGE_ALIASES``, lowercased, or ``{}`` outside Django."""
    try:
        from ..conf import agent_settings

        raw = getattr(agent_settings, "STT_LANGUAGE_ALIASES", None) or {}
    except Exception:  # Django absent or settings not configured
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        str(k).strip().lower().replace("_", "-"): str(v)
        for k, v in raw.items()
        if k and v
    }


def _case_subtag(index: int, subtag: str) -> str:
    """BCP-47 casing for a non-primary subtag (§2.1.1 of RFC 5646).

    Convention only — tags are case-insensitive — but a convention is
    exactly what a canonical form is for: two spellings that compare
    unequal as strings are two rows in every table that stores one.
    """
    if index and len(subtag) == 2 and subtag.isalpha():
        return subtag.upper()           # region: BR, US
    if index and len(subtag) == 4 and subtag.isalpha():
        return subtag.capitalize()      # script: Hans, Cyrl
    return subtag


def canonical_language(
    language: Optional[str], *, provider: str = ""
) -> Optional[str]:
    """The one spelling of *language* this package stores and routes on.

    ``None``/empty → ``None`` (auto-detect: the absence of a hint is not
    an unknown code). ``"eng"`` → ``"en"``; ``"RUS"`` → ``"ru"``;
    ``"pt_br"`` → ``"pt-BR"``; ``"zh-hans-cn"`` → ``"zh-Hans-CN"``.
    Anything else raises :class:`UnknownLanguageError`.
    """
    if language is None:
        return None
    raw = str(language).strip()
    if not raw:
        return None

    normalized = raw.lower().replace("_", "-")

    # A host alias may name the whole tag ("pt-br-x-foo") or just the
    # primary subtag ("cmn"); the whole tag is tried first so a
    # deployment can pin a region for one of them ("yue" → "zh-HK").
    aliases = host_aliases()
    if normalized in aliases:
        # Re-enter with the alias' value, so a host alias is held to the
        # same shape as anything else — and a typo in settings is a
        # refusal at the first call, not a bad row a quarter later.
        return canonical_language(aliases[normalized], provider=provider)

    subtags = [part for part in normalized.split("-") if part]
    if not subtags:
        return None
    primary = subtags[0]

    if primary in aliases:
        resolved = canonical_language(aliases[primary], provider=provider)
        primary = (resolved or "").split("-")[0]
    elif primary in ISO_639_1:
        pass
    elif primary in ISO_639_1_BY_2:
        primary = ISO_639_1_BY_2[primary]
    elif primary in LEGACY_ALIASES:
        primary = LEGACY_ALIASES[primary]
    else:
        raise UnknownLanguageError(raw, provider=provider)

    rest = [_case_subtag(i, part) for i, part in enumerate(subtags[1:], start=1)]
    return "-".join([primary, *rest])


def base_language(language: Optional[str]) -> Optional[str]:
    """The canonical primary subtag alone — ``"pt-BR"`` → ``"pt"``.

    Lenient on purpose, and the reason :func:`normalize_language` can keep
    its signature: this is what the ADAPTERS need (a provider parameter),
    and an adapter is downstream of a boundary that has already refused
    anything unresolvable. Given something it cannot resolve it returns
    the lowercased primary subtag rather than raising, which is byte-for-
    byte what the package did before this module existed.
    """
    if not language:
        return None
    try:
        canonical = canonical_language(language)
    except UnknownLanguageError:
        return str(language).strip().lower().replace("_", "-").split("-")[0] or None
    return canonical.split("-")[0] if canonical else None


def route_keys(language: Optional[str]) -> list[str]:
    """Keys to try in ``STT_LANGUAGE_ROUTES``, most specific first.

    ``"pt-BR"`` → ``["pt-BR", "pt"]``. A host that routes all Portuguese
    to one engine writes ``pt``; one that sends Brazilian Portuguese
    somewhere else writes ``pt-BR`` and keeps ``pt`` for the rest. Before
    this, the region was dropped before the lookup and the second host
    could not be expressed at all.
    """
    if not language:
        return []
    keys = [language]
    base = language.split("-")[0]
    if base != language:
        keys.append(base)
    return keys


__all__ = [
    "ISO_639_1",
    "ISO_639_1_BY_2",
    "ISO_639_2_BY_1",
    "LEGACY_ALIASES",
    "UnknownLanguageError",
    "base_language",
    "canonical_language",
    "host_aliases",
    "route_keys",
]
