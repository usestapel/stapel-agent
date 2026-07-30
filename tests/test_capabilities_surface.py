"""Drift gate for the `surface` section of ``docs/capabilities.json``.

The safety gates are why this section exists at all. ``redaction_gate`` and
``detect_pwned_markers`` were built, reviewed and released — and then a port
kept the import and dropped the call, and nothing anywhere could say that a
gate had gone unwired, because the module's contract document could describe
what you may switch on (``axes``) and what you may replace
(``extension_points``) and had no way at all to name a function you are
supposed to CALL (discoverability-design.md §1.2).

``surface`` names them, with one curated line each saying when to reach for
them. The entry set is derived by AST from the roots in
``docs/capabilities.meta.json`` — a new public function in ``safety/`` shows up
here by itself and fails emission until somebody explains it.

Honest boundary: the REST of this module's ``capabilities.json`` is still
hand-written (no gate registry, no ``docs/schema.json``), so only
``module``/``version``/``surface`` are gated below.
"""
import json
from pathlib import Path

import pytest

pytest.importorskip(
    "stapel_tools",
    reason="stapel-tools carries the capabilities emitter this gate runs.",
)

from stapel_tools.surface import _stable_json, load_meta, patch_capabilities  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
COMMITTED = REPO / "docs" / "capabilities.json"

SAFETY_GATES = {
    "redaction_gate",
    "detect_pwned_markers",
    "redact_markers",
    "sanitize_for_rag",
}


def _emitted() -> dict:
    try:
        return patch_capabilities(REPO, load_meta(REPO))
    except SystemExit as exc:  # the LOUD rule — report it, don't bury it
        pytest.fail(f"capabilities emission refused: {exc}", pytrace=False)


def test_no_drift():
    assert COMMITTED.read_text() == _stable_json(_emitted()), (
        "docs/capabilities.json is stale — run `make contract` and commit it"
    )


def test_version_tracks_pyproject():
    """The document carries the module version. It had rotted to 0.2.10 against
    a 0.8.0 package while it was hand-written; deriving it is the fix."""
    import tomllib

    pyproject = tomllib.loads((REPO / "pyproject.toml").read_text())
    assert json.loads(COMMITTED.read_text())["version"] == (
        pyproject["project"]["version"]
    )


def test_every_safety_gate_is_named_and_explained():
    surface = json.loads(COMMITTED.read_text())["surface"]
    by_name = {e["name"]: e for e in surface}
    assert SAFETY_GATES <= set(by_name)
    for name in SAFETY_GATES:
        entry = by_name[name]
        assert entry["kind"] == "gate_function", entry
        assert entry["intent"].strip(), entry


def test_a_new_public_safety_function_cannot_slip_in_unexplained():
    """The set is derived, so the gate is not "did somebody remember to list
    it" but "does every public function in the declared roots have a line"."""
    from stapel_tools.surface import scan_functions

    declared = {e["name"] for e in json.loads(COMMITTED.read_text())["surface"]}
    for module in ("safety/redaction.py", "safety/markers.py"):
        assert set(scan_functions(REPO / module)) <= declared
