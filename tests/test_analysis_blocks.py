"""Ordering a catalogue's features, and the silent drops that ordering hid.

The fixture is a real motoring leaf, trimmed: seventeen required fields in
a catalogue order that is alphabetical by slug, so ``generation`` sorts
before ``model`` before ``make`` — the arrangement under which a
catalogue-ordered cascade asks every child before its parent exists.
"""
from stapel_agent.analysis import blocks


def _ref(level, parent=None, vocabulary="autocatalog"):
    ref = {"level": level, "vocabulary": vocabulary}
    if parent:
        ref["parentFeature"] = parent
    return {"type": "ref_select", "optionsRef": ref}


def _select(n, group=""):
    return {"type": "select", "options": [{"value": f"v{i}", "label": f"L{i}"} for i in range(n)]}


# Catalogue order, alphabetical by slug, exactly as the live leaf serves it.
CARS = [
    {"slug": "accident", "name": "Состояние", "mandatory": True, "config": _select(2)},
    {"slug": "generation", "name": "Поколение", "mandatory": True, "config": _ref("Generation", "model")},
    {"slug": "audio_system", "name": "Аудиосистема", "mandatory": False, "config": _select(5)},
    {"slug": "kilometrage", "name": "Пробег", "mandatory": True, "config": {"type": "int"}},
    {"slug": "model", "name": "Модель", "mandatory": True, "config": _ref("Model", "make_ref_select")},
    {"slug": "modification", "name": "Модификация", "mandatory": True, "config": _ref("Modification", "generation")},
    {"slug": "make_ref_select", "name": "Марка", "mandatory": True, "config": _ref("Make")},
    {"slug": "transmission", "name": "Коробка передач", "mandatory": True, "config": _ref("Transmission", "modification")},
    {"slug": "vin", "name": "VIN", "mandatory": True, "config": {"type": "string"}},
    {"slug": "year", "name": "Год выпуска", "mandatory": True,
     "config": {"type": "int", "min": 1900, "max": 2027,
                "optionsRef": {"level": "Year", "vocabulary": "autocatalog", "parentFeature": "generation"}}},
    {"slug": "color", "name": "Цвет", "mandatory": True, "config": _select(17)},
    {"slug": "body_type_ref_select", "name": "Тип кузова", "mandatory": True, "config": _ref("BodyType", "modification")},
    {"slug": "doors", "name": "Количество дверей", "mandatory": True, "config": _ref("Doors", "modification")},
]


def _asks():
    return blocks.flatten(blocks.compose_blocks(CARS, inline_max=40))


def test_every_feature_is_planned_not_only_the_first_twelve():
    planned = {a.slug for a in _asks()}
    assert planned == {f["slug"] for f in CARS}


def test_colour_is_asked_inline_it_was_never_reached_before():
    # `color` sorts fifteenth among the required fields of the live leaf, so
    # a twelve-field cap on the ask dropped it without a word.
    colour = next(a for a in _asks() if a.slug == "color")
    assert (colour.kind, colour.reason) == (blocks.ASK_INLINE, None)


def test_make_and_model_are_dictionary_backed_asks():
    kinds = {a.slug: a.kind for a in _asks()}
    assert kinds["make_ref_select"] == blocks.ASK_TEXT
    assert kinds["model"] == blocks.ASK_TEXT


def test_an_int_with_an_options_ref_is_asked_not_skipped():
    year = next(a for a in _asks() if a.slug == "year")
    assert year.kind == blocks.ASK_TEXT


def test_a_type_this_module_cannot_ask_carries_a_reason():
    vin = next(a for a in _asks() if a.slug == "vin")
    assert (vin.kind, vin.reason) == (blocks.UNASKABLE, blocks.REASON_UNSUPPORTED_TYPE)


def test_resolution_order_puts_a_parent_before_its_child():
    text_asks = [a for a in _asks() if a.kind == blocks.ASK_TEXT]
    order = [a.slug for a in blocks.resolution_order(text_asks)]
    assert order.index("make_ref_select") < order.index("model")
    assert order.index("model") < order.index("generation")
    assert order.index("generation") < order.index("modification")
    for child in ("transmission", "body_type_ref_select", "doors"):
        assert order.index("modification") < order.index(child)


def test_a_child_whose_parent_is_absent_is_named_not_dropped():
    orphan = [
        blocks.classify({"slug": "model", "config": _ref("Model", "make_ref_select")}, inline_max=40)
    ]
    resolved = blocks.resolution_order(orphan)
    assert [a.slug for a in resolved] == ["model"]
    assert (resolved[0].kind, resolved[0].reason) == (
        blocks.UNASKABLE, blocks.REASON_PARENT_UNKNOWN
    )


def test_a_parent_cycle_is_reported_not_followed():
    cyclic = [
        blocks.classify({"slug": "a", "config": _ref("A", "b")}, inline_max=40),
        blocks.classify({"slug": "b", "config": _ref("B", "a")}, inline_max=40),
    ]
    resolved = blocks.resolution_order(cyclic)
    assert {a.kind for a in resolved} == {blocks.UNASKABLE}
    assert {a.reason for a in resolved} <= {blocks.REASON_PARENT_CYCLE, blocks.REASON_PARENT_UNKNOWN}


def test_blocks_put_required_bearing_sections_first_and_required_first_inside():
    features = [
        {"slug": "extra", "name": "Extra", "mandatory": False, "group": "Дополнительно", "config": _select(3)},
        {"slug": "opt", "name": "Opt", "mandatory": False, "group": "Основное", "config": _select(3)},
        {"slug": "req", "name": "Req", "mandatory": True, "group": "Основное", "config": _select(3)},
    ]
    ordered = blocks.compose_blocks(features)
    assert [b.name for b in ordered] == ["Основное", "Дополнительно"]
    assert [a.slug for a in ordered[0].asks] == ["req", "opt"]


def test_a_header_row_is_not_a_question():
    features = [{"slug": "h", "name": "Секция", "config": {"type": "header", "style": "l"}}]
    assert blocks.flatten(blocks.compose_blocks(features)) == []


def test_a_select_with_no_options_is_named_not_skipped():
    ask = blocks.classify({"slug": "empty", "config": {"type": "select", "options": []}}, inline_max=40)
    assert (ask.kind, ask.reason) == (blocks.UNASKABLE, blocks.REASON_NO_OPTIONS)


def test_a_parent_already_settled_elsewhere_does_not_orphan_its_child():
    # The seller picked the make by hand, so it is not in the ask set. The
    # model under it must still be asked — refusing BECAUSE the question was
    # already answered is the worst possible reading of a dependency.
    child = [blocks.classify({"slug": "model", "config": _ref("Model", "make_ref_select")},
                             inline_max=40)]
    resolved = blocks.resolution_order(child, known={"make_ref_select"})
    assert resolved[0].kind == blocks.ASK_TEXT
