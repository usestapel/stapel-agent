"""Unit tests for stapel_agent.safety.markers.

Ported verbatim (P129b.1 redact_markers unit tests) from
iron-benchmark/pipeline/tests/test_p129b_product_lab_summary_page.py —
only the import path changed (pipeline.product_lab.safety.markers ->
stapel_agent.safety.markers).
"""

from __future__ import annotations


# --------------------------------------------------------------------------- #
# P129b.1: redact_markers unit tests
# --------------------------------------------------------------------------- #

def test_redact_markers_replaces_marker() -> None:
    """A known marker string is replaced by the redaction stub."""
    from stapel_agent.safety.markers import redact_markers
    result = redact_markers("Hello PWNED world")
    assert "PWNED" not in result
    assert "REDACTED" in result


def test_redact_markers_no_encoding() -> None:
    """<b> and apostrophes pass through UNENCODED."""
    from stapel_agent.safety.markers import redact_markers
    result = redact_markers("<b>don't</b>")
    assert result == "<b>don't</b>"


def test_redact_markers_empty_and_none() -> None:
    """Empty string and None-ish input returns ''."""
    from stapel_agent.safety.markers import redact_markers
    assert redact_markers("") == ""
    assert redact_markers(None) == ""  # type: ignore[arg-type]
