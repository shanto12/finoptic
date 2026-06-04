"""Detection engine: rule coverage, totals, ordering, and defensiveness."""

from __future__ import annotations

from pathlib import Path

import pytest

from finoptic.detect import RULES, detect, summarize
from finoptic.domain import SEVERITY_RANK, NormalizedRecord
from finoptic.ingest import parse_aws_cur, parse_azure_billing

from .conftest import EXPECTED

SAMPLE_DIR = Path(__file__).resolve().parents[1] / "sample_data"


@pytest.fixture(scope="module")
def sample_findings() -> list:
    records = parse_aws_cur((SAMPLE_DIR / "aws_cur_sample.csv").read_text())
    records += parse_azure_billing((SAMPLE_DIR / "azure_billing_sample.json").read_text())
    return detect(records)


def test_rule_count() -> None:
    assert len(RULES) == 11


def test_sample_finding_count(sample_findings: list) -> None:
    assert len(sample_findings) == EXPECTED["findings"]


def test_sample_total_waste(sample_findings: list) -> None:
    total = round(sum(f.monthly_cost for f in sample_findings), 2)
    assert total == pytest.approx(EXPECTED["monthly_waste"], abs=0.01)


def test_all_rules_fire_on_sample(sample_findings: list) -> None:
    fired = {f.rule_id for f in sample_findings}
    assert fired == EXPECTED["rule_ids"]


def test_findings_sorted_by_severity_then_cost(sample_findings: list) -> None:
    keys = [(SEVERITY_RANK[f.severity], f.monthly_cost) for f in sample_findings]
    assert keys == sorted(keys, reverse=True)


def test_summary_dimensions_each_sum_to_total(sample_findings: list) -> None:
    agg = summarize(sample_findings, resource_count=EXPECTED["records"])
    total = agg["total_monthly_waste"]
    for dim in ("by_severity", "by_category", "by_provider", "by_resource_type"):
        assert sum(agg[dim].values()) == pytest.approx(total, abs=0.05)
    assert agg["total_annual_savings"] == pytest.approx(total * 12, abs=0.01)
    assert agg["resource_count"] == EXPECTED["records"]


def test_by_provider_split(sample_findings: list) -> None:
    agg = summarize(sample_findings)
    assert agg["by_provider"]["aws"] == pytest.approx(EXPECTED["by_provider"]["aws"], abs=0.01)
    assert agg["by_provider"]["azure"] == pytest.approx(EXPECTED["by_provider"]["azure"], abs=0.01)


def test_detect_is_defensive_against_bad_state() -> None:
    bad = [
        NormalizedRecord(provider="aws", resource_id="vol-x", resource_type="ebs_volume", state={}),
        NormalizedRecord(
            provider="aws",
            resource_id="i-x",
            resource_type="ec2_instance",
            state={"power_state": None, "avg_cpu_percent": None},
        ),
        NormalizedRecord(provider="aws", resource_id="r", resource_type="unknown_type"),
    ]
    # Should not raise and should not flag any of these.
    assert detect(bad) == []


def test_healthy_resource_not_flagged() -> None:
    healthy = NormalizedRecord(
        provider="aws",
        resource_id="vol-ok",
        resource_type="ebs_volume",
        monthly_cost=10.0,
        state={"attachment_state": "attached"},
    )
    assert detect([healthy]) == []
