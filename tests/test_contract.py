"""Drift gate for ``docs/llms.txt``, the fifth contract artifact
(stapel_tools.llms_txt) — same discipline as the ``docs/capabilities.json``
gate in ``test_capabilities_surface.py``, but for the file rendered FROM it.

stapel-agent has no ``docs/schema.json`` / ``errors.json`` / ``flows.json``
(no gate registry, no OpenAPI surface) — ``llms.txt`` here is rendered purely
from ``docs/capabilities.json``'s axes / surface / extension_points / requires.
Regenerate with ``make contract`` after any capabilities.json change; drift
here means the committed file no longer describes the module's own surface,
the exact rot this artifact exists to catch (see stapel_tools/llms_txt.py
module docstring).
"""
from pathlib import Path

try:
    import stapel_tools  # noqa: F401  (probe: the emitter must be importable)
except ImportError as exc:  # pragma: no cover - environment failure, not a branch
    # NOT pytest.importorskip. A drift gate that skips when its emitter is
    # missing reports `1 skipped`, exits 0, and disappears among a hundred
    # green tests — making "the tool is absent" indistinguishable from "there
    # is no drift". A gate that cannot run has FAILED; it has not passed.
    raise RuntimeError(
        "llms.txt drift gate cannot run: stapel-tools is not importable, and "
        "it carries the emitter this gate measures drift against. Install it "
        "(workspace venv, or `pip install stapel-tools`) and re-run. This is "
        "a hard failure on purpose — a skipped drift gate is silently no "
        "gate."
    ) from exc

from stapel_tools.llms_txt import load_inputs, render  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
COMMITTED = REPO / "docs" / "llms.txt"


def test_llms_txt_committed():
    assert COMMITTED.is_file(), "missing docs/llms.txt — run `make contract`"


def test_llms_txt_has_no_drift():
    rendered = render(load_inputs(REPO))
    assert COMMITTED.read_text() == rendered, (
        "docs/llms.txt is stale — run `make contract` and commit it"
    )


def test_llms_txt_emission_is_deterministic():
    """Two independent renders are byte-identical (the drift gate is meaningful)."""
    a = render(load_inputs(REPO))
    b = render(load_inputs(REPO))
    assert a == b
