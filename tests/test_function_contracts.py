"""The committed function contracts must match the code that serves them.

Every comm Function declares its payload schema twice: as a dict in
``functions.py``, and as a committed JSON file under ``schemas/functions/``.
The JSON is not documentation — ``autoload_schemas()`` registers it at app
startup and it **overrides** the in-code schema. So the file is what actually
validates a caller's payload, and the Python is what a reader believes.

When those two disagree the failure is quiet and misleading in a very specific
way: you add a field to the Python schema, the tests you wrote pass in a plain
interpreter, and every call through comm is rejected with "additional
properties are not allowed" for a field you are looking at in the source. That
cost real time to diagnose once (`schema` on `llm.complete`, 29 Jul 2026),
which is why the rule is machine-checked now instead of remembered.

Both directions are checked. A contract file with no function is a leftover
that will silently validate nothing; a function with no contract file gets its
in-code schema, which is fine until someone assumes every function has a
reviewable contract.
"""

import json
import pathlib

import pytest

import stapel_agent.functions as functions

CONTRACT_DIR = pathlib.Path(functions.__file__).parent / "schemas" / "functions"

#: Keys the committed file carries for human readers and JSON-Schema tooling,
#: which the in-code dict has no reason to repeat.
DOC_KEYS = {"$schema", "title", "description"}


def _in_code_schemas() -> dict[str, dict]:
    """Every ``@function`` schema, keyed by function name.

    Read off the module's own constants rather than the live registry: the
    registry is exactly what the JSON has already overridden, so asking it
    would compare the file against itself and pass no matter what.
    """
    from stapel_core.comm import function_registry

    found = {}
    for name in function_registry.names():
        if not name.startswith("llm."):
            continue
        const = name.replace("llm.", "").upper() + "_SCHEMA"
        schema = getattr(functions, const, None)
        if isinstance(schema, dict):
            found[name] = schema
    return found


def _contract_files() -> dict[str, dict]:
    return {p.stem: json.loads(p.read_text()) for p in CONTRACT_DIR.glob("*.json")}


def test_there_are_contracts_to_check():
    """Guard against the check silently covering nothing."""
    assert len(_contract_files()) >= 5
    assert len(_in_code_schemas()) >= 5


@pytest.mark.parametrize("name", sorted(_contract_files()))
def test_every_contract_has_a_function(name):
    assert name in _in_code_schemas(), (
        f"{name}.json has no matching schema constant in functions.py — "
        "a contract nobody serves validates nothing"
    )


@pytest.mark.parametrize("name", sorted(_contract_files()))
def test_contract_matches_the_code(name):
    """The file wins at runtime, so a mismatch means the code is a lie."""
    committed = _contract_files()[name]
    in_code = _in_code_schemas()[name]

    stripped = {k: v for k, v in committed.items() if k not in DOC_KEYS}
    assert stripped == in_code, (
        f"{name}: the committed contract and functions.py disagree.\n"
        f"Only in the file: {sorted(set(stripped) - set(in_code))}\n"
        f"Only in the code: {sorted(set(in_code) - set(stripped))}\n"
        "The file is what autoload_schemas() registers, so callers are "
        "validated against the file while readers believe the code."
    )


@pytest.mark.parametrize("name", sorted(_in_code_schemas()))
def test_every_function_has_a_contract(name):
    assert name in _contract_files(), (
        f"{name} has an in-code schema but no schemas/functions/{name}.json — "
        "add one so the wire contract is reviewable in a diff"
    )
