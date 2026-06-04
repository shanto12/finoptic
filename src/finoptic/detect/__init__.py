"""Detection stage: turn normalized records into prioritized waste findings.

Public surface:
    detect(records)            -> list[FindingResult]   (rules applied, sorted)
    summarize(findings, n=0)   -> dict                  (cost rollups)
    RULES                      -> list of rule callables (the registry)
"""

from __future__ import annotations

from finoptic.detect.engine import detect, summarize
from finoptic.detect.rules import RULES

__all__ = ["detect", "summarize", "RULES"]
