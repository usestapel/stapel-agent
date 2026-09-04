"""Turning a catalogue's feature definitions into the order to ask them in.

Three separate decisions live here, and each one was a silent drop before
it was written down:

**Which features are asked.** All of them, not only the mandatory ones. A
catalogue marks a field mandatory to make a form refuse to submit; that is
a statement about publication, not about what a model can see in a
photograph. Restricting the ask to mandatory fields hides colour and make
on any catalogue that spells them optional, and a cap on the number of
fields hides whatever sorts last — on a real motoring leaf that is exactly
colour, year and body type.

**In what order.** The composer's: sections in the order the catalogue
first mentions them, sections that carry a required field before ones that
do not, and inside a section the required fields first. The earliest
screens a person sees are then the ones that fill in first.

**In what order they are RESOLVED.** Not the same order. A reference field
may name a ``parentFeature`` — a model is looked up under its make — so
resolution is topological over that edge, and a cycle or a missing parent
is reported, never followed. Resolving in catalogue order looks right
until a catalogue happens to sort ``generation`` before ``model`` before
``make``, at which point every child is asked before its parent exists and
the whole cascade silently answers nothing.

**Nothing is dropped in silence.** A feature this module cannot ask about
comes back with :class:`Ask` ``kind=UNASKABLE`` and a ``reason``, so the
caller reports it as an explicit unknown instead of omitting it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

#: Config types this module knows how to turn into a question.
KIND_SELECT = "select"
KIND_REF_SELECT = "ref_select"

#: How a feature is asked.
ASK_INLINE = "inline"      # small option set, answered by index
ASK_TEXT = "text"          # backed by a dictionary, answered as free text
UNASKABLE = "unaskable"    # cannot be asked — the reason says why

#: Reasons. These reach the seller's screen as machine values, never prose.
REASON_NO_OPTIONS = "no_options"
REASON_NO_OPTIONS_REF = "no_options_ref"
REASON_UNSUPPORTED_TYPE = "unsupported_type"
REASON_PARENT_UNKNOWN = "parent_unknown"
REASON_PARENT_CYCLE = "parent_cycle"
#: Runtime reasons the caller stamps, declared here so there is one list.
REASON_NOT_VISIBLE = "not_visible"
REASON_NO_MATCH = "no_match"
REASON_MODEL_FAILED = "model_failed"

#: The section a feature with no declared group belongs to.
DEFAULT_BLOCK = ""


def _config(feature: Mapping) -> Mapping:
    config = feature.get("config")
    return config if isinstance(config, Mapping) else {}


def options(feature: Mapping) -> list[dict]:
    raw = _config(feature).get("options")
    return [o for o in raw if isinstance(o, Mapping)] if isinstance(raw, list) else []


def options_ref(feature: Mapping) -> dict:
    ref = _config(feature).get("optionsRef")
    return dict(ref) if isinstance(ref, Mapping) else {}


@dataclass
class Ask:
    """One feature and the decision about how (or whether) to ask it."""

    feature: dict
    kind: str
    reason: str | None = None

    @property
    def slug(self) -> str:
        return str(self.feature.get("slug") or "")

    @property
    def name(self) -> str:
        return str(self.feature.get("name") or "")

    @property
    def mandatory(self) -> bool:
        return bool(self.feature.get("mandatory"))


@dataclass
class Block:
    """One section of the composer, in the order it is rendered."""

    name: str
    asks: list = field(default_factory=list)

    @property
    def has_required(self) -> bool:
        return any(a.mandatory for a in self.asks)

    @property
    def askable(self) -> list:
        return [a for a in self.asks if a.kind != UNASKABLE]


def classify(feature: Mapping, *, inline_max: int) -> Ask:
    """Decide how one feature can be asked. Never returns ``None``."""
    row = dict(feature)
    kind = _config(row).get("type")
    if kind == KIND_SELECT:
        choices = options(row)
        if not choices:
            return Ask(row, UNASKABLE, REASON_NO_OPTIONS)
        if len(choices) <= inline_max:
            return Ask(row, ASK_INLINE)
        # Too many to show. Backed by a dictionary it can still be asked as
        # free text; otherwise there is nothing to match against.
        if options_ref(row):
            return Ask(row, ASK_TEXT)
        return Ask(row, UNASKABLE, REASON_NO_OPTIONS_REF)
    if kind == KIND_REF_SELECT or options_ref(row):
        # `optionsRef` on a non-ref type (an int year bounded by a
        # catalogue level, say) is still a dictionary-backed question.
        if options_ref(row):
            return Ask(row, ASK_TEXT)
        return Ask(row, UNASKABLE, REASON_NO_OPTIONS_REF)
    return Ask(row, UNASKABLE, REASON_UNSUPPORTED_TYPE)


def compose_blocks(features: Sequence[Mapping], *, inline_max: int = 40) -> list[Block]:
    """Group features into composer sections and order them for asking.

    Sections appear in the order the catalogue first mentions them, then
    the ones carrying a required field are moved to the front — a stable
    move, so two sections that both carry one keep their catalogue order.
    Inside a section the required fields come first, again stably.
    """
    ordered: list[str] = []
    grouped: dict[str, list] = {}
    for feature in features:
        if _config(feature).get("type") == "header":
            continue  # a visual separator, not a question
        name = str(feature.get("group") or DEFAULT_BLOCK)
        if name not in grouped:
            grouped[name] = []
            ordered.append(name)
        grouped[name].append(classify(feature, inline_max=inline_max))

    out = [
        Block(name=name, asks=sorted(grouped[name], key=lambda a: not a.mandatory))
        for name in ordered
    ]
    return sorted(out, key=lambda b: not b.has_required)


def resolution_order(asks: Sequence[Ask], *, known: Iterable[str] = ()) -> list[Ask]:
    """Order dictionary-backed asks so a parent resolves before its child.

    A depth-first walk over ``optionsRef.parentFeature`` restricted to the
    asks in hand. An ask whose parent is neither in hand nor already
    ``known``, or that sits on a cycle, is returned LAST and marked
    unaskable with a reason — resolving it would search a whole dictionary
    level unscoped, which is how a confident-looking wrong value gets
    written into somebody's listing.

    ``known`` is what an earlier block, or the seller's own hand, has
    already settled. Without it a make the person picked would orphan the
    model underneath it — the assistant refusing to answer BECAUSE the
    question was already answered.
    """
    settled = {str(s) for s in known}
    by_slug = {a.slug: a for a in asks if a.slug}
    ordered: list[Ask] = []
    placed: set[str] = set()
    broken: list[Ask] = []

    def visit(ask: Ask, seen: frozenset) -> bool:
        slug = ask.slug
        if slug in placed:
            return True
        if slug in seen:
            ask.kind, ask.reason = UNASKABLE, REASON_PARENT_CYCLE
            return False
        parent_slug = options_ref(ask.feature).get("parentFeature")
        if parent_slug and str(parent_slug) not in settled:
            parent = by_slug.get(str(parent_slug))
            if parent is None:
                ask.kind, ask.reason = UNASKABLE, REASON_PARENT_UNKNOWN
                return False
            if not visit(parent, seen | {slug}):
                ask.kind, ask.reason = UNASKABLE, REASON_PARENT_UNKNOWN
                return False
        placed.add(slug)
        ordered.append(ask)
        return True

    for ask in asks:
        if not visit(ask, frozenset()):
            broken.append(ask)
    return ordered + broken


def bounds_for(features: Sequence[Mapping], values: Mapping | None = None) -> dict:
    """``{slug: (min, max)}`` for the numeric fields, rules applied.

    The rules engine's ``limit`` effect is what narrows a year to the
    generation the seller's earlier answers already settled, so it is
    consulted when the module that owns it is installed. Where it is not,
    the config's own ``min``/``max`` still bound the question — a looser
    answer, never a wrong one.
    """
    out: dict = {}
    for feature in features:
        config = _config(feature)
        low, high = config.get("min"), config.get("max")
        if low is not None or high is not None:
            out[str(feature.get("slug") or "")] = (low, high)
    try:
        from stapel_attributes.rules import evaluate_rules
    except Exception:  # noqa: BLE001 - the module is optional by design
        return out
    for slug, state in evaluate_rules(features, values or {}).items():
        low = state.min if state.min is not None else out.get(slug, (None, None))[0]
        high = state.max if state.max is not None else out.get(slug, (None, None))[1]
        if low is not None or high is not None:
            out[slug] = (low, high)
    return out


def flatten(block_list: Iterable[Block]) -> list[Ask]:
    """Every ask of every block, in the order they will be asked."""
    return [ask for block in block_list for ask in block.asks]


__all__ = [
    "ASK_INLINE",
    "ASK_TEXT",
    "Ask",
    "Block",
    "DEFAULT_BLOCK",
    "REASON_MODEL_FAILED",
    "REASON_NO_MATCH",
    "REASON_NO_OPTIONS",
    "REASON_NO_OPTIONS_REF",
    "REASON_NOT_VISIBLE",
    "REASON_PARENT_CYCLE",
    "REASON_PARENT_UNKNOWN",
    "REASON_UNSUPPORTED_TYPE",
    "UNASKABLE",
    "compose_blocks",
    "bounds_for",
    "classify",
    "flatten",
    "options",
    "options_ref",
    "resolution_order",
]
