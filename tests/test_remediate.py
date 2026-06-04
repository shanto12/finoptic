"""Remediation generator: correct CLI per rule, safe script rendering, defensiveness."""

from __future__ import annotations

from finoptic.domain import FindingResult
from finoptic.remediate import attach_remediations, build_remediation, render_script


def _finding(rule_id: str, resource_id: str = "vol-123", region: str | None = "us-east-1"):
    return FindingResult(
        resource_id=resource_id,
        provider="aws",
        resource_type="ebs_volume",
        rule_id=rule_id,
        title="t",
        category="orphaned_storage",
        severity="high",
        monthly_cost=42.0,
        region=region,
    )


def test_ebs_unattached_snapshots_before_delete() -> None:
    rem = build_remediation(_finding("AWS_EBS_UNATTACHED"))
    assert rem.cli_commands[0].startswith("aws ec2 create-snapshot")
    assert any(c.startswith("aws ec2 delete-volume") for c in rem.cli_commands)
    assert "vol-123" in rem.cli_commands[-1]
    assert rem.risk in {"low", "medium", "high"}


def test_region_falls_back_when_missing() -> None:
    rem = build_remediation(_finding("AWS_EIP_UNASSOCIATED", resource_id="eipalloc-1", region=None))
    assert "us-east-1" in " ".join(rem.cli_commands)


def test_azure_vm_deallocated_is_high_risk() -> None:
    f = FindingResult(
        resource_id="/subscriptions/s/resourceGroups/r/providers/Microsoft.Compute/virtualMachines/vm1",
        provider="azure",
        resource_type="vm",
        rule_id="AZURE_VM_DEALLOCATED",
        title="t",
        category="idle_compute",
        severity="medium",
        monthly_cost=60.0,
    )
    rem = build_remediation(f)
    assert rem.risk == "high"
    assert rem.reversible is False
    assert any("az vm delete" in c for c in rem.cli_commands)


def test_unknown_rule_is_generic_and_safe() -> None:
    rem = build_remediation(_finding("TOTALLY_UNKNOWN_RULE"))
    assert rem.cli_commands == []
    assert "review" in rem.strategy.lower() or "manual" in rem.strategy.lower()


def test_attach_remediations_mutates_in_place() -> None:
    findings = [_finding("AWS_EBS_UNATTACHED"), _finding("AWS_RDS_IDLE", "db-1")]
    returned = attach_remediations(findings)
    assert returned is findings
    assert all(f.remediation is not None for f in findings)


def test_render_script_is_safe_by_default() -> None:
    findings = attach_remediations([_finding("AWS_EBS_UNATTACHED")])
    script = render_script(findings)
    assert script.startswith("#!/usr/bin/env bash")
    assert "set -euo pipefail" in script
    assert "DRY_RUN" in script  # commands are guarded
    assert "aws ec2 delete-volume" in script  # the command is present...
    assert "run " in script  # ...but routed through the dry-run-aware run() helper


def test_render_script_handles_empty() -> None:
    assert isinstance(render_script([]), str)
