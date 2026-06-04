"""API contract tests via FastAPI TestClient."""

from __future__ import annotations

from fastapi.testclient import TestClient

from .conftest import EXPECTED


def test_healthz(client: TestClient) -> None:
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["llm_enabled"] is False


def test_ingest_sample_returns_expected_totals(client: TestClient, sample_batch: str) -> None:
    r = client.get("/api/v1/summary", params={"batch_uid": sample_batch})
    assert r.status_code == 200
    s = r.json()
    assert s["finding_count"] == EXPECTED["findings"]
    assert s["resource_count"] == EXPECTED["records"]
    assert s["total_monthly_waste"] == EXPECTED["monthly_waste"]
    assert s["total_annual_savings"] == EXPECTED["annual_waste"]
    assert s["by_provider"]["aws"] == EXPECTED["by_provider"]["aws"]
    assert s["by_provider"]["azure"] == EXPECTED["by_provider"]["azure"]
    assert len(s["top_findings"]) == 10


def test_findings_list_and_total(client: TestClient, sample_batch: str) -> None:
    r = client.get("/api/v1/findings", params={"batch_uid": sample_batch, "limit": 100})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == EXPECTED["findings"]
    assert len(body["items"]) == EXPECTED["findings"]
    first = body["items"][0]
    assert {"finding_uid", "resource_id", "severity", "remediation", "annual_savings"} <= set(first)
    assert "cli_commands" in first["remediation"]


def test_findings_filter_by_severity(client: TestClient, sample_batch: str) -> None:
    r = client.get(
        "/api/v1/findings", params={"batch_uid": sample_batch, "severity": "high", "limit": 100}
    )
    items = r.json()["items"]
    assert items and all(i["severity"] == "high" for i in items)


def test_get_single_finding_and_404(client: TestClient, sample_batch: str) -> None:
    listing = client.get("/api/v1/findings", params={"batch_uid": sample_batch}).json()["items"]
    uid = listing[0]["finding_uid"]
    assert client.get(f"/api/v1/findings/{uid}").status_code == 200
    assert client.get("/api/v1/findings/does-not-exist").status_code == 404


def test_analyze_endpoint_deterministic(client: TestClient, sample_batch: str) -> None:
    r = client.post("/api/v1/analyze", json={"batch_uid": sample_batch})
    assert r.status_code == 200
    a = r.json()
    assert a["mode"] == "deterministic"
    assert a["provider"] == "none"
    assert len(a["executive_summary"]) > 40
    assert len(a["prioritized_runbook"]) >= 1
    assert a["total_monthly_waste"] == EXPECTED["monthly_waste"]


def test_export_remediation_script(client: TestClient, sample_batch: str) -> None:
    r = client.get("/api/v1/export/remediation.sh", params={"batch_uid": sample_batch})
    assert r.status_code == 200
    assert "text/x-shellscript" in r.headers["content-type"]
    assert r.text.startswith("#!/usr/bin/env bash")
    assert "DRY_RUN" in r.text


def test_export_findings_csv(client: TestClient, sample_batch: str) -> None:
    r = client.get("/api/v1/export/findings.csv", params={"batch_uid": sample_batch})
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    lines = r.text.strip().splitlines()
    assert lines[0].startswith("finding_uid,provider,account_id,")
    assert len(lines) == EXPECTED["findings"] + 1  # header + one row per finding
    assert "AWS_RDS_IDLE" in r.text


def test_upload_billing_file(client: TestClient) -> None:
    csv = (
        "identity_line_item_id,bill_payer_account_id,line_item_usage_account_id,product_code,"
        "line_item_resource_id,resource_type,product_region,line_item_usage_type,"
        "line_item_unblended_cost,billing_period_start_date,billing_period_end_date,resource_tags,state\n"
        "li-1,111,111,AmazonEC2,vol-deadbeef,ebs_volume,us-east-1,EBS:VolumeUsage,30.00,"
        '2026-05-01,2026-05-31,"{}","{""attachment_state"":""available""}"\n'
    )
    r = client.post(
        "/api/v1/ingest",
        files={"file": ("my_cur.csv", csv, "text/csv")},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["record_count"] == 1
    assert body["finding_count"] == 1
    assert body["total_monthly_waste"] == 30.0


def test_metrics_endpoint(client: TestClient) -> None:
    r = client.get("/metrics")
    assert r.status_code == 200
    assert "finoptic_requests_total" in r.text


def test_dashboard_served(client: TestClient) -> None:
    assert client.get("/").status_code == 200
    assert client.get("/app.js").status_code == 200
    assert client.get("/styles.css").status_code == 200
