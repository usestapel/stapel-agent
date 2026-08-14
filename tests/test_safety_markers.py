"""Unit tests for stapel_agent.safety.markers.

Ported verbatim (P129b.1 redact_markers unit tests) from
iron-benchmark/pipeline/tests/test_p129b_product_lab_summary_page.py —
only the import path changed (pipeline.product_lab.safety.markers ->
stapel_agent.safety.markers).
"""

from __future__ import annotations

import pytest


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


# --------------------------------------------------------------------------- #
# AI-01: structured-output leakage canary
#
# The schema-constrained path proves its shape by construction; the prose path
# cannot, so it carries a canary. These assert the detector's own contract —
# services.summarize is what wires it to the product boundary (test_summarize).
# --------------------------------------------------------------------------- #

def test_detect_structured_output_leak_clean_prose() -> None:
    """Ordinary prose — including angle brackets — is not a leak."""
    from stapel_agent.safety.markers import detect_structured_output_leak
    assert detect_structured_output_leak("The team agreed to ship on <date>.") == []


@pytest.mark.parametrize(
    "text,expected",
    [
        ("<function_calls>", "TOOL_CALL_ENVELOPE"),
        ("</invoke>", "TOOL_CALL_ENVELOPE"),
        ('<tool_use name="x">', "TOOL_CALL_ENVELOPE"),
        ('<parameter name="q">', "PARAMETER_TAG"),
        ("<|im_start|>assistant", "CHAT_TEMPLATE_TOKEN"),
        ("<|eot_id|>", "CHAT_TEMPLATE_TOKEN"),
        ('{"additionalProperties": false}', "SCHEMA_ECHO"),
    ],
)
def test_detect_structured_output_leak_flags_scaffolding(text, expected) -> None:
    """Each family the model can spill into a plain answer is named."""
    from stapel_agent.safety.markers import detect_structured_output_leak
    assert detect_structured_output_leak(f"Summary. {text} More text.") == [expected]


def test_detect_structured_output_leak_names_every_family_found() -> None:
    """Several scaffolds in one answer are reported together, sorted."""
    from stapel_agent.safety.markers import detect_structured_output_leak
    leaked = detect_structured_output_leak('<invoke><parameter name="a">')
    assert leaked == ["PARAMETER_TAG", "TOOL_CALL_ENVELOPE"]


def test_detect_structured_output_leak_empty_and_non_string() -> None:
    """A detector on an untrusted boundary never raises on odd input."""
    from stapel_agent.safety.markers import detect_structured_output_leak
    assert detect_structured_output_leak("") == []
    assert detect_structured_output_leak(None) == []  # type: ignore[arg-type]
