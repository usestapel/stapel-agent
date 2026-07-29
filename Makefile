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
contract:
	$(PYTHON) -m stapel_agent._contracts

contract-check:
	$(PYTHON) -m stapel_agent._contracts --check
