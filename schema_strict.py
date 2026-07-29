"""Re-export of :mod:`stapel_core.schema_strict`.

The transform moved to core in `stapel-core` 0.15.11: it is a pure JSON Schema
transform with no provider knowledge, and a caller that wants to inspect what
will really go on the wire — before paying for the call — should not have to
import the LLM library to do it.

Kept as a re-export because 0.6.6 shipped it here a day earlier. Import from
``stapel_core.schema_strict`` in new code.
"""

from stapel_core.schema_strict import (  # noqa: F401
    DROPPED_KEYS,
    to_strict_subset,
)

__all__ = ["DROPPED_KEYS", "to_strict_subset"]
