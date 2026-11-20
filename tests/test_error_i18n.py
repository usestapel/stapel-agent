"""Localized error catalogs (``translations/errors.<lang>.json``) + provenance gate.

The ``stapel_core.i18n`` catalog contour stapel-auth and stapel-notifications
ship, applied to the error keys this module owns: the en canon lives in
``errors.py``, each target language ships as a flat
``translations/errors.<lang>.json`` catalog with a shared
``translations/.state.json`` provenance sidecar, and
:func:`check_translation_catalogs` gates coverage, staleness, params and
byte-stability. Without them a client rendering one of these keys in ru or es
fell back to the English literal.

Provenance: keys the curated ``stapel-translate`` builtin corpus carries are
**seeded** from it (``origin: seed:stapel-builtin``); the rest are **machine
translations** recorded here per language in :data:`_MACHINE` and written with
``origin: llm`` (unreviewed). In a live deployment ``translate_catalogs
--domain errors --lang <lang> --llm`` produces these through the
``STAPEL_I18N["TRANSLATOR"]`` comm seam; offline they come from that map so the
module regenerates deterministically without a live LLM.

Regenerate after adding/changing an error key or a translation:

    STAPEL_REGEN_ERROR_I18N=1 python -m pytest tests/test_error_i18n.py::test_regen

then commit ``translations/errors.<lang>.json`` + ``translations/.state.json``.
Without the env var the same module is the CI gate.
"""
import os
from pathlib import Path

from stapel_core.i18n import (
    check_translation_catalogs,
    owned_keys,
    owner_of_dir,
    source_owners,
    source_texts,
    summarize,
    translate_catalog,
)
from stapel_core.i18n.catalogs import load_catalog_file

REPO = Path(__file__).resolve().parent.parent
TRANSLATIONS = REPO / "translations"
#: Languages this module ships error catalogs in. en is the canon.
LANGUAGES = ["en", "ru", "es"]
TARGET_LANGUAGES = [lang for lang in LANGUAGES if lang != "en"]

#: stapel-translate builtin fixtures (the curated seed corpus). Overridable for
#: an out-of-tree checkout via STAPEL_TRANSLATE_FIXTURES.
_FIXTURES = Path(
    os.environ.get(
        "STAPEL_TRANSLATE_FIXTURES",
        REPO.parent / "stapel-translate" / "fixtures" / "builtin",
    )
)

#: Machine translations (origin: llm) of the owned keys the corpus does not
#: carry. All param-free; edit here + regen when the en changes.
_MACHINE_RU: dict[str, str] = {
    "error.400.empty_documents": "documents должен быть непустым списком непустых строк",
    "error.400.empty_query": "query должен быть непустой строкой",
    "error.400.empty_texts": "texts должен быть непустым списком непустых строк",
    "error.400.invalid_image": "Для каждого изображения нужно ровно одно из полей: url, data_b64 (корректный base64)",
    "error.400.invalid_image_count": "n должно быть от 1 до 10",
    "error.400.invalid_model_size": "Модель должна быть одной из: small, medium, large, xlarge",
    "error.400.invalid_num_speakers": "num_speakers должно быть положительным целым числом (>= 1)",
    "error.400.invalid_timeout": "timeout_seconds должно быть положительным целым числом (>= 1)",
    "error.400.invalid_top_n": "top_n должно быть положительным целым числом (>= 1)",
    "error.400.summarize_input": "Укажите ровно одно из полей: text, transcript",
}

_MACHINE_ES: dict[str, str] = {
    "error.400.empty_documents": "documents debe ser una lista no vacía de cadenas no vacías",
    "error.400.empty_query": "query debe ser una cadena no vacía",
    "error.400.empty_texts": "texts debe ser una lista no vacía de cadenas no vacías",
    "error.400.invalid_image": "Cada imagen necesita exactamente uno de: url, data_b64 (base64 válido)",
    "error.400.invalid_image_count": "n debe estar entre 1 y 10",
    "error.400.invalid_model_size": "El modelo debe ser uno de: small, medium, large, xlarge",
    "error.400.invalid_num_speakers": "num_speakers debe ser un entero positivo (>= 1)",
    "error.400.invalid_timeout": "timeout_seconds debe ser un entero positivo (>= 1)",
    "error.400.invalid_top_n": "top_n debe ser un entero positivo (>= 1)",
    "error.400.summarize_input": "Indica exactamente uno de: text, transcript",
}

_MACHINE = {"ru": _MACHINE_RU, "es": _MACHINE_ES}


class _DictTranslator:
    """Offline translator seam — returns fixed machine translations by key."""

    def __init__(self, table):
        self._table = table

    def translate(self, entries, source_language, target_language):
        return {k: self._table[k] for k in entries if k in self._table}


def _seed_from_fixtures(lang: str) -> dict[str, str]:
    """Flat ``{error.*: text}`` seed from the builtin fixtures for *lang*."""
    import json

    path = _FIXTURES / f"{lang}.json"
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        k: v for k, v in data.items()
        if isinstance(k, str) and k.startswith("error.")
        and isinstance(v, str) and v
    }


def _regen(lang: str):
    """Materialize one target-language catalog from corpus + machine map."""
    return translate_catalog(
        "errors", lang, TRANSLATIONS,
        source_texts=source_texts("errors"),
        seed=_seed_from_fixtures(lang),
        seed_label="stapel-builtin",
        llm=True,
        translator=_DictTranslator(_MACHINE.get(lang, {})),
    )


def _owned() -> dict[str, str]:
    source = owned_keys(
        source_texts("errors"), source_owners("errors"), owner_of_dir(TRANSLATIONS),
    )
    assert source, "ownership resolved to nothing — is stapel_agent installed?"
    return source


def test_regen():
    """Regenerate (env-gated) or assert every catalog is a no-op regen (drift)."""
    if os.environ.get("STAPEL_REGEN_ERROR_I18N"):
        for lang in TARGET_LANGUAGES:
            result = _regen(lang)
            assert not result.missing, f"{lang}: still missing: {result.missing}"
        return

    for lang in TARGET_LANGUAGES:
        path = TRANSLATIONS / f"errors.{lang}.json"
        before = path.read_bytes()
        _regen(lang)
        assert path.read_bytes() == before, (
            f"errors.{lang}.json drifted — run "
            f"STAPEL_REGEN_ERROR_I18N=1 pytest tests/test_error_i18n.py::test_regen"
        )


def test_catalog_gate_green():
    """E: missing / stale / params-mismatch / not-byte-stable — all zero."""
    issues = check_translation_catalogs(
        "errors", TRANSLATIONS,
        source_texts=source_texts("errors"),
        languages=LANGUAGES,
    )
    errors, _warnings = summarize(issues)
    blocking = [i for i in issues if i.level == "error"]
    assert not blocking, "\n".join(f"[{i.code}] {i.message}" for i in blocking)
    assert errors == 0


def test_every_language_covers_every_key_this_module_owns():
    owned = _owned()
    for lang in TARGET_LANGUAGES:
        catalog = load_catalog_file(TRANSLATIONS / f"errors.{lang}.json")
        missing = [k for k in owned if k not in catalog]
        assert not missing, f"{lang} catalog missing {len(missing)} key(s): {missing[:8]}"


def test_the_catalogs_carry_only_owned_keys():
    owned = _owned()
    for lang in TARGET_LANGUAGES:
        catalog = load_catalog_file(TRANSLATIONS / f"errors.{lang}.json")
        stray = [k for k in catalog if k not in owned]
        assert not stray, f"{lang}: not this module's keys: {stray}"


def test_translations_preserve_placeholders():
    """Every localized text keeps exactly the canon's ``{param}`` slots."""
    from stapel_core.i18n.domains import params_of

    source = source_texts("errors")
    for lang in TARGET_LANGUAGES:
        catalog = load_catalog_file(TRANSLATIONS / f"errors.{lang}.json")
        for key, text in catalog.items():
            if key in source:
                assert set(params_of(text)) == set(params_of(source[key])), f"{lang}: {key}"


def test_the_wheel_ships_the_catalogs():
    """A catalog that stays in the repo translates nothing for a consumer."""
    import tomllib

    data = tomllib.loads((REPO / "pyproject.toml").read_text())
    package_data = next(iter(data["tool"]["setuptools"]["package-data"].values()))
    assert "translations/*.json" in package_data
