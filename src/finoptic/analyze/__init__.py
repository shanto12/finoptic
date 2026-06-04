"""Analyze package: GenAI FinOps analyst with a deterministic fallback.

Public surface:
    analyze(findings, summary, client=None) -> AnalyzeResult
    AnalyzeResult  (mode, provider, executive_summary, prioritized_runbook)
    LLMClient      (provider-agnostic httpx client; ``from_settings`` / ``complete``)
"""

from __future__ import annotations

from .analyst import AnalyzeResult, analyze
from .llm import LLMClient

__all__ = ["analyze", "AnalyzeResult", "LLMClient"]
