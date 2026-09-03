"""Declared properties of a generated TEXT, and the violations it commits.

A constrained decoder guarantees a shape. It guarantees nothing about the
register the prose inside that shape is written in, and the two failures look
identical from the caller's side: a well-formed string in the right field.

The case this module was written from, observed on a live client fleet's
listing composer. Asked for a sale description, the model returned a caption
of the photograph it had been shown («on the photo you can see three cameras
and a lidar»), assessed the item «as far as can be judged from the image»,
and closed by offering to keep the conversation going («if you need any more
details from the photo, I'll tell you»). All of it validated. None of it is
a marketplace listing — a listing states condition, contents and the things a
buyer decides on, and it does not address the reader as a chat partner.

Two halves close that, and this module is the second one. The first is the
prompt: say what the field is for. The prompt alone is not a mechanism — it
is a request, and the model declines it often enough to matter. So the answer
is CHECKED against the same statement of intent, and a caller that asks for a
revision gets one (see ``services.complete_json``'s ``validate`` /
``max_revisions``).

What belongs here and what does not: the CONTRACT is a mechanism and lives in
the library; the phrases are a fact about a product's language and market and
live in the caller's settings. A library that ships a banned-phrase list in
Russian has guessed at somebody's product.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = ["ProseContract", "check_prose"]

#: Trailing punctuation and whitespace an ending is allowed to hide behind.
_TAIL_NOISE = " \t\r\n.!…»\"'）)"


def _fold(text: str) -> str:
    """Case-fold and normalise «ё»→«е» for matching.

    The Cyrillic fold is not decoration: «ё» is optional in written Russian,
    a model emits it inconsistently, and a banned phrase that misses half its
    spellings is a check that reports clean because it cannot see.
    """
    return str(text or "").lower().replace("ё", "е")


@dataclass(frozen=True)
class ProseContract:
    """What a generated text must not be.

    Every field is opt-in and an empty contract rejects nothing, so adding
    the parameter to an existing call changes no behaviour until something
    is actually declared.

    :param max_chars: the field's own limit, in CHARACTERS. A one-line title
        on a storefront is bounded by glyphs, not bytes, and a Cyrillic title
        is not two thirds the length of its Latin equivalent.
    :param banned_phrases: substrings that must not appear anywhere. Matched
        case- and «ё»-insensitively.
    :param reject_trailing_question: refuse a text that ends on a question
        mark. A document does not interrogate its reader; a chat turn does.
    :param banned_patterns: regular expressions that must not match ANYWHERE.
        The escalation from ``banned_phrases``, and the reason it exists: a
        phrase list bans what somebody thought of. A live composer routed
        around «на фото» inside one attempt — «по предоставленному фото
        определить невозможно», «по фото не указаны» — and every variant is
        the same register, the text treating the photograph as its source of
        knowledge instead of describing the item. Answering that with more
        literals is whack-a-mole against a model with more spellings than the
        list has rows; a pattern states the SHAPE once. Both fields are kept
        because an exact string is easier to read and impossible to get
        wrong, and is right wherever it is enough.
    :param banned_endings: regular expressions that must not match at the
        END of the text. Separate from ``banned_phrases`` on purpose — an
        offer to keep talking is only a defect where it closes the text, and
        the same words mid-sentence are ordinary prose.

    Every pattern field is compiled in ``__post_init__``, so a malformed
    regex raises where the contract is DECLARED — at import, next to the
    settings that state it — and not an hour later on the first generated
    text that happened to reach it.
    """

    max_chars: int | None = None
    banned_phrases: tuple[str, ...] = ()
    reject_trailing_question: bool = False
    banned_patterns: tuple[str, ...] = ()
    banned_endings: tuple[str, ...] = ()

    #: Compiled ``banned_patterns``, built once per contract.
    _patterns: tuple = field(default=(), init=False, repr=False, compare=False)
    #: Compiled ``banned_endings``, built once per contract.
    _endings: tuple = field(default=(), init=False, repr=False, compare=False)

    def __post_init__(self):
        # Folded before compiling, like every other rule here: «ё» is
        # optional in written Russian and a model emits it inconsistently,
        # so a pattern that respects it is a check that cannot see half the
        # spellings it was written for.
        anywhere = tuple(
            (pattern, re.compile(_fold(pattern))) for pattern in self.banned_patterns
        )
        object.__setattr__(self, "_patterns", anywhere)
        compiled = tuple(
            (pattern, re.compile(_fold(pattern) + r"[" + re.escape(_TAIL_NOISE) + r"]*$"))
            for pattern in self.banned_endings
        )
        object.__setattr__(self, "_endings", compiled)


def check_prose(text: str, contract: ProseContract) -> tuple[str, ...]:
    """Return the violation codes *text* commits against *contract*.

    An empty tuple is a pass. The codes are stable strings meant to travel:
    they go into the revision prompt so the model is told what was wrong
    rather than merely asked again, and into the failure envelope so an
    operator reading a log learns which rule fired.

    Codes: ``too_long``, ``trailing_question``, ``banned_phrase:<phrase>``,
    ``banned_pattern:<pattern>``, ``banned_ending:<pattern>``.
    """
    violations: list[str] = []
    raw = str(text or "")
    folded = _fold(raw)

    if contract.max_chars is not None and len(raw) > int(contract.max_chars):
        violations.append("too_long")

    for phrase in contract.banned_phrases:
        if phrase and _fold(phrase) in folded:
            violations.append(f"banned_phrase:{phrase}")

    for pattern, compiled in contract._patterns:
        if compiled.search(folded):
            violations.append(f"banned_pattern:{pattern}")

    stripped = raw.rstrip(" \t\r\n")
    if contract.reject_trailing_question and stripped.endswith(("?", "？")):
        violations.append("trailing_question")

    for pattern, compiled in contract._endings:
        if compiled.search(folded):
            violations.append(f"banned_ending:{pattern}")

    return tuple(violations)
