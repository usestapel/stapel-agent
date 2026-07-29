"""Emit ``schemas/functions/*.json`` from the schemas in ``functions.py``.

Those JSON files are not documentation. ``autoload_schemas()`` registers them
at app startup and they **override** the in-code schema, so the file is what
actually validates a caller's payload while the Python is what a reader
believes. Two copies of one truth, and the failure when they part is quiet and
misleading: the field is right there in the source, and every call through comm
is rejected for a property that "does not exist".

So the file is generated, the code is the source, and
``tests/test_function_contracts.py`` fails when they disagree. Run:

    make contract        # rewrite the files
    make contract-check  # fail if they would change

``$schema`` and the top-level ``title``/``description`` are preserved from the
existing file: they are written for humans reviewing a contract diff and have
no counterpart in the in-code dict.
"""

from __future__ import annotations

import json
import pathlib

#: Top-level keys the committed file carries that the in-code dict does not.
DOC_KEYS = ("$schema", "title", "description")

_JSON_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"


def contract_dir() -> pathlib.Path:
    return pathlib.Path(__file__).parent / "schemas" / "functions"


def in_code_schemas() -> dict[str, dict]:
    """Every ``llm.*`` function schema, keyed by function name.

    Names come from the live registry, the dicts from the module constants:
    the registry's own copy is exactly what the JSON already overrode, so
    reading it here would compare a file against itself.
    """
    from stapel_core.comm import function_registry

    from . import functions

    out: dict[str, dict] = {}
    for name in function_registry.names():
        if not name.startswith("llm."):
            continue
        const = name.replace("llm.", "").upper() + "_SCHEMA"
        schema = getattr(functions, const, None)
        if isinstance(schema, dict):
            out[name] = schema
    return out


def render(name: str, schema: dict, previous: dict | None = None) -> dict:
    """The file content for one function, doc keys carried over."""
    previous = previous or {}
    doc = {
        "$schema": previous.get("$schema") or _JSON_SCHEMA_DIALECT,
        "title": previous.get("title") or name,
    }
    if previous.get("description"):
        doc["description"] = previous["description"]
    return {**doc, **schema}


def emit(*, check: bool = False) -> list[str]:
    """Write (or, with ``check``, compare) every contract file.

    Returns the names whose file changed or would change — empty means the
    committed contracts match the code.
    """
    directory = contract_dir()
    directory.mkdir(parents=True, exist_ok=True)
    drifted: list[str] = []

    for name, schema in sorted(in_code_schemas().items()):
        path = directory / f"{name}.json"
        previous = json.loads(path.read_text()) if path.exists() else None
        content = json.dumps(render(name, schema, previous), indent=2, ensure_ascii=False) + "\n"
        if path.exists() and path.read_text() == content:
            continue
        drifted.append(name)
        if not check:
            path.write_text(content)
    return drifted


def main(argv: list[str] | None = None) -> int:
    import argparse

    import django
    from django.conf import settings

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail instead of writing")
    args = parser.parse_args(argv)

    if not settings.configured:
        # The smallest settings that make the app's ready() run, which is what
        # registers the functions this reads. Deliberately not the test
        # settings: emitting a contract must not depend on the test suite's
        # configuration, or the contract quietly documents the test rig.
        settings.configure(
            SECRET_KEY="contract-emit",
            INSTALLED_APPS=[
                "django.contrib.contenttypes",
                "django.contrib.auth",
                "stapel_agent",
            ],
            DATABASES={},
            USE_TZ=True,
        )
    django.setup()

    drifted = emit(check=args.check)
    if not drifted:
        print("function contracts match functions.py")
        return 0
    if args.check:
        print("committed contracts differ from functions.py:")
        for name in drifted:
            print(f"  {name}")
        print("run `make contract` and commit the result")
        return 1
    for name in drifted:
        print(f"wrote schemas/functions/{name}.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
