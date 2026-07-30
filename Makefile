PYTHON ?= python3

.PHONY: migration-lint contract contract-check

# Expand/contract gate for Django migrations (release-management.md §3;
# stapel_tools.migration_lint). Requires stapel-tools importable (the
# workspace venv, or `pip install stapel-tools` once published).
migration-lint:
	$(PYTHON) -m stapel_tools.migration_lint . --strict


# The comm Function contracts under schemas/functions/. These are NOT docs:
# autoload_schemas() registers them at startup and they OVERRIDE the in-code
# schema, so the file is what validates a caller while functions.py is what a
# reader believes. Generated from the code so the two cannot part; the gate
# lives in tests/test_function_contracts.py.
#
# Second half: the `surface` section of docs/capabilities.json — the symbols a
# product is meant to CALL (discoverability-design.md §1.2). The safety gates
# are the reason this exists here: `redaction_gate` and `detect_pwned_markers`
# were released and never wired, and nothing in the module's contract could
# even name them. Entries are derived by AST from the roots declared in
# docs/capabilities.meta.json; a selected export with no curated intent line
# fails this target naming the symbol.
#
# NOTE the rest of docs/capabilities.json is still hand-written (this module
# has no gate registry and no docs/schema.json) — `--patch` refreshes only the
# derivable parts: module/version and `surface`.
contract:
	$(PYTHON) -m stapel_agent._contracts
	$(PYTHON) -m stapel_tools.surface . --patch

contract-check:
	$(PYTHON) -m stapel_agent._contracts --check
	$(PYTHON) -m stapel_tools.surface . --patch --check
