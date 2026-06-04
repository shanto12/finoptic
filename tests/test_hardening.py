"""Regression tests for the adversarial-review hardening fixes."""

from __future__ import annotations

from fastapi.testclient import TestClient

from finoptic.config import get_settings
from finoptic.detect import detect
from finoptic.domain import FindingResult, NormalizedRecord
from finoptic.remediate import attach_remediations, render_script

# --------------------------------------------------------------------------- #
# Detection safety
# --------------------------------------------------------------------------- #


def test_negative_cost_is_not_waste() -> None:
    """Credit/refund lines (negative cost) must never become negative-waste findings."""
    rec = NormalizedRecord(
        provider="aws", resource_id="vol-credit", resource_type="ebs_volume",
        monthly_cost=-5.0, state={"attachment_state": "available"},
    )
    assert detect([rec]) == []


def test_busy_lb_with_zero_healthy_targets_not_flagged() -> None:
    """A trafficked LB momentarily reporting 0 healthy targets must NOT be flagged for deletion."""
    rec = NormalizedRecord(
        provider="aws", resource_id="arn:aws:elb:...:lb", resource_type="load_balancer",
        monthly_cost=20.0, state={"request_count": 100000, "healthy_targets": 0},
    )
    assert detect([rec]) == []


def test_idle_lb_zero_requests_is_flagged() -> None:
    rec = NormalizedRecord(
        provider="aws", resource_id="arn:aws:elb:...:lb", resource_type="load_balancer",
        monthly_cost=20.0, state={"request_count": 0, "healthy_targets": 0},
    )
    found = detect([rec])
    assert len(found) == 1 and found[0].rule_id == "AWS_ELB_IDLE"


def test_retention_tagged_snapshot_is_spared() -> None:
    """An old, orphaned snapshot tagged for retention must not be recommended for deletion."""
    rec = NormalizedRecord(
        provider="aws", resource_id="snap-keep", resource_type="snapshot", monthly_cost=8.0,
        state={"age_days": 400, "source_volume_exists": False}, tags={"retention": "7y"},
    )
    assert detect([rec]) == []


# --------------------------------------------------------------------------- #
# Remediation safety
# --------------------------------------------------------------------------- #


def _ebs_finding(resource_id: str = "vol-1") -> FindingResult:
    return FindingResult(
        resource_id, "aws", "ebs_volume", "AWS_EBS_UNATTACHED", "t",
        "orphaned_storage", "high", 10.0, region="us-east-1",
    )


def test_ebs_remediation_waits_before_delete() -> None:
    rem = attach_remediations([_ebs_finding()])[0].remediation
    cmds = rem.cli_commands
    assert any("aws ec2 wait snapshot-completed" in c for c in cmds)
    snap_i = next(i for i, c in enumerate(cmds) if "create-snapshot" in c)
    del_i = next(i for i, c in enumerate(cmds) if "delete-volume" in c)
    assert snap_i < del_i  # snapshot is created (and waited on) before the volume is deleted


def test_render_script_header_is_injection_safe() -> None:
    """A newline-laced resource id must not break out of the comment into an executable line."""
    evil = "vol-1\nrm -rf /\n# pwned"
    f = _ebs_finding(evil)
    attach_remediations([f])
    script = render_script([f])
    # The injected command never appears as a standalone line...
    assert "\nrm -rf /\n" not in script
    # ...and the resource id's newlines were collapsed into exactly one comment header.
    headers = [ln for ln in script.splitlines() if ln.startswith("# [HIGH] AWS_EBS_UNATTACHED")]
    assert len(headers) == 1


# --------------------------------------------------------------------------- #
# API: unknown-batch leak guard + upload size cap
# --------------------------------------------------------------------------- #


def test_unknown_batch_uid_never_leaks_whole_db(client: TestClient, sample_batch: str) -> None:
    """A bogus batch_uid must return empty results, NOT a whole-database aggregate."""
    bogus = {"batch_uid": "definitely-not-a-real-batch"}

    summary = client.get("/api/v1/summary", params=bogus).json()
    assert summary["total_monthly_waste"] == 0.0
    assert summary["finding_count"] == 0
    assert summary["batch_uid"] is None

    findings = client.get("/api/v1/findings", params=bogus).json()
    assert findings["total"] == 0 and findings["items"] == []

    analysis = client.post("/api/v1/analyze", json=bogus).json()
    assert analysis["total_monthly_waste"] == 0.0

    script = client.get("/api/v1/export/remediation.sh", params=bogus).text
    assert "aws " not in script and "az " not in script  # no cross-batch command leak


def test_oversized_upload_is_rejected(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "max_upload_bytes", 1024)
    payload = b"x" * 4096
    r = client.post("/api/v1/ingest", files={"file": ("big.csv", payload, "text/csv")})
    assert r.status_code == 413
