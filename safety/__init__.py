"""Safety helpers — injection-marker detection + consumer-specific sanitizers.

Reader FLAGS, UI/RAG ESCAPES. This module is the single place where
context-specific encoding lives; the reader stays consumer-agnostic (Spark
Q4 defense-in-depth split).

Version: 0 · Date: 22 Jul 2026 (P129a)
"""

from __future__ import annotations
