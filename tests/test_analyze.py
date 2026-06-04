"""GenAI analyst: deterministic fallback + mocked LLM dispatch/parse (no network)."""

from __future__ import annotations

import json

from finoptic.analyze import LLMClient, analyze
from finoptic.analyze import llm as llm_module
from finoptic.config import ResolvedLLM
from finoptic.detect import summarize
from finoptic.domain import FindingResult


def _findings() -> list[FindingResult]:
    return [
        FindingResult(
            "vol-1",
            "aws",
            "ebs_volume",
            "AWS_EBS_UNATTACHED",
            "Unattached vol-1",
            "orphaned_storage",
            "high",
            42.0,
        ),
        FindingResult(
            "db-1",
            "aws",
            "rds_instance",
            "AWS_RDS_IDLE",
            "Idle RDS db-1",
            "idle_database",
            "high",
            310.0,
        ),
        FindingResult(
            "eipalloc-1",
            "aws",
            "elastic_ip",
            "AWS_EIP_UNASSOCIATED",
            "Unassoc EIP",
            "unassociated_network",
            "medium",
            3.6,
        ),
    ]


def test_offline_client_is_disabled() -> None:
    client = LLMClient.from_settings()
    assert client.enabled is False
    assert client.complete("sys", "user") is None


def test_analyze_falls_back_to_deterministic() -> None:
    findings = _findings()
    result = analyze(findings, summarize(findings, resource_count=10))
    assert result.mode == "deterministic"
    assert result.provider == "none"
    assert len(result.executive_summary) > 40
    assert len(result.prioritized_runbook) >= 1
    # Largest finding should be prioritized near the top.
    assert "db-1" in " ".join(result.prioritized_runbook[:2])


def test_analyze_handles_empty_findings() -> None:
    result = analyze([], summarize([], resource_count=0))
    assert result.mode == "deterministic"
    assert isinstance(result.executive_summary, str) and result.executive_summary


def test_analyze_ignores_non_finding_junk() -> None:
    findings = _findings()
    result = analyze([*findings, "garbage", None], summarize(findings, 10))  # type: ignore[list-item]
    assert result.mode == "deterministic"
    assert result.executive_summary


# --------------------------------------------------------------------------- #
# Mocked LLM transport (no network): exercises llm.py dispatch + analyst llm path
# --------------------------------------------------------------------------- #
class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    """Stand-in for httpx.Client capturing the request and returning a canned body."""

    captured: dict = {}

    def __init__(self, *args, **kwargs) -> None:
        pass

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def post(self, url, headers=None, json=None):
        _FakeClient.captured = {"url": url, "headers": headers or {}, "body": json or {}}
        content = (
            "Executive Summary:\nReclaim ~$355/mo across 3 findings.\n\n"
            "Runbook:\n1. Stop idle RDS db-1.\n2. Release the unassociated EIP."
        )
        return _FakeResponse({"choices": [{"message": {"content": content}}]})


def test_llm_openai_dispatch_and_parse(monkeypatch) -> None:
    monkeypatch.setattr(llm_module.httpx, "Client", _FakeClient)
    client = LLMClient(ResolvedLLM(provider="openai", api_key="sk-test", model="gpt-4o-mini"))
    assert client.enabled is True

    text = client.complete("system", "user")
    assert text is not None and "Runbook" in text
    assert _FakeClient.captured["url"].endswith("/chat/completions")
    assert _FakeClient.captured["headers"]["Authorization"] == "Bearer sk-test"

    findings = _findings()
    result = analyze(findings, summarize(findings, 10), client=client)
    assert result.mode == "llm"
    assert result.provider == "openai"
    assert "Reclaim" in result.executive_summary
    assert len(result.prioritized_runbook) == 2


def test_llm_non_2xx_falls_back(monkeypatch) -> None:
    class _ErrClient(_FakeClient):
        def post(self, url, headers=None, json=None):
            return _FakeResponse({"error": "boom"}, status_code=500)

    monkeypatch.setattr(llm_module.httpx, "Client", _ErrClient)
    client = LLMClient(ResolvedLLM(provider="openai", api_key="k", model="gpt-4o-mini"))
    assert client.complete("s", "u") is None

    findings = _findings()
    result = analyze(findings, summarize(findings, 10), client=client)
    assert result.mode == "deterministic"
