"""The wheel must contain every subpackage that exists in the tree.

`[tool.setuptools] packages` is a hand-written list. Forgetting an entry
breaks nothing locally — tests import from the source tree — and ships a
wheel with the module simply absent. The first symptom is an ImportError
in someone else's deployment, one release later.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# `build/` and `dist/` hold COPIES of the tree left by a previous wheel
# build. Counting them as packages made this check fail on a stale artifact
# that has nothing to do with the source — a gate that cries wolf gets
# switched off, so it must only ever look at real source directories.
IGNORED = {
    "tests", "migrations_test", "__pycache__", ".venv",
    "docs", "port", "build", "dist", ".eggs",
}


def _declared() -> set[str]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return set(data["tool"]["setuptools"]["packages"])


def _on_disk() -> set[str]:
    found = {"stapel_agent"}
    for init in ROOT.rglob("__init__.py"):
        rel = init.parent.relative_to(ROOT)
        parts = rel.parts
        if not parts or parts[0].startswith("."):
            continue
        if set(parts) & IGNORED:
            continue
        found.add("stapel_agent." + ".".join(parts))
    return found


def test_every_subpackage_is_declared_for_the_wheel():
    missing = _on_disk() - _declared()
    assert not missing, (
        "these packages exist in the tree but would NOT ship in the wheel: "
        + ", ".join(sorted(missing))
    )


def test_no_declared_package_is_a_ghost():
    """The reverse drift: an entry left behind after a module was deleted
    makes the build fail with a confusing error."""
    ghosts = _declared() - _on_disk()
    assert not ghosts, (
        "these packages are declared but do not exist: " + ", ".join(sorted(ghosts))
    )
