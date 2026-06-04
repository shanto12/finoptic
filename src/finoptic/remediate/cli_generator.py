"""Remediation / CLI generator for FinOptic findings.

Maps each detector ``rule_id`` onto a concrete, safe-by-default
:class:`~finoptic.domain.RemediationResult` built from provider CLI templates, and
renders an aggregate, DRY-RUN-guarded bash script for an entire batch of findings.

This module is a *sibling* feature stage: it imports only the framework-free spine
(:mod:`finoptic.domain`) plus the stdlib, never another feature module.
"""

from __future__ import annotations

import shlex
from collections.abc import Callable, Iterable, Mapping

from finoptic.domain import RemediationResult
from finoptic.logging_config import get_logger

__all__ = ["build_remediation", "attach_remediations", "render_script"]

logger = get_logger(__name__)

#: Region substituted into CLI templates when a finding carries no region.
DEFAULT_REGION = "us-east-1"


def _ebs_unattached(rid: str, region: str) -> RemediationResult:
    return RemediationResult(
        strategy="Snapshot then delete the unattached EBS volume",
        cli_commands=[
            f"aws ec2 create-snapshot --volume-id {rid} "
            f"--description 'finoptic-backup' --region {region}",
            # create-snapshot is async — block until it completes before deleting the source.
            f"aws ec2 wait snapshot-completed "
            f"--filters Name=volume-id,Values={rid} --region {region}",
            f"aws ec2 delete-volume --volume-id {rid} --region {region}",
        ],
        api_logic=("boto3 ec2.create_snapshot(VolumeId=...), wait, then ec2.delete_volume(...)."),
        risk="medium",
        reversible=False,
        notes="Snapshot is taken AND waited on (snapshot-completed) before the volume is deleted.",
    )


def _ec2_idle(rid: str, region: str) -> RemediationResult:
    return RemediationResult(
        strategy="Stop the idle EC2 instance",
        cli_commands=[
            f"aws ec2 stop-instances --instance-ids {rid} --region {region}",
        ],
        api_logic="boto3 ec2.stop_instances(InstanceIds=[...]).",
        risk="medium",
        reversible=True,
        notes="Consider rightsizing or terminate if no longer needed.",
    )


def _eip_unassociated(rid: str, region: str) -> RemediationResult:
    return RemediationResult(
        strategy="Release the unassociated Elastic IP",
        cli_commands=[
            f"aws ec2 release-address --allocation-id {rid} --region {region}",
        ],
        api_logic="boto3 ec2.release_address(AllocationId=...).",
        risk="low",
        reversible=False,
    )


def _rds_idle(rid: str, region: str) -> RemediationResult:
    return RemediationResult(
        strategy="Stop the idle RDS instance",
        cli_commands=[
            f"aws rds stop-db-instance --db-instance-identifier {rid} --region {region}",
        ],
        api_logic="boto3 rds.stop_db_instance(DBInstanceIdentifier=...).",
        risk="medium",
        reversible=True,
        notes=(
            "`stop-db-instance` is temporary (AWS auto-starts a stopped instance after 7 days) "
            "and is REJECTED on read replicas. For replicas or durable savings, snapshot then "
            "delete the instance instead."
        ),
    )


def _elb_idle(rid: str, region: str) -> RemediationResult:
    return RemediationResult(
        strategy="Delete the idle load balancer",
        cli_commands=[
            f"aws elbv2 delete-load-balancer --load-balancer-arn {rid} --region {region}",
        ],
        api_logic="boto3 elbv2.delete_load_balancer(LoadBalancerArn=...).",
        risk="medium",
        reversible=False,
    )


def _snapshot_stale(rid: str, region: str) -> RemediationResult:
    return RemediationResult(
        strategy="Delete the stale EBS snapshot",
        cli_commands=[
            f"aws ec2 delete-snapshot --snapshot-id {rid} --region {region}",
        ],
        api_logic="boto3 ec2.delete_snapshot(SnapshotId=...).",
        risk="low",
        reversible=False,
        notes="Confirm this snapshot is not a required/retained backup before deleting.",
    )


def _natgw_idle(rid: str, region: str) -> RemediationResult:
    return RemediationResult(
        strategy="Delete the idle NAT gateway",
        cli_commands=[
            f"aws ec2 delete-nat-gateway --nat-gateway-id {rid} --region {region}",
        ],
        api_logic="boto3 ec2.delete_nat_gateway(NatGatewayId=...).",
        risk="medium",
        reversible=False,
        notes=(
            "delete-nat-gateway is asynchronous and does NOT release the gateway's Elastic IP — "
            "that EIP returns to the account unassociated and keeps incurring the idle-EIP charge. "
            "After deletion completes, release it with `aws ec2 release-address`."
        ),
    )


def _azure_disk_unattached(rid: str, region: str) -> RemediationResult:
    return RemediationResult(
        strategy="Delete the unattached managed disk",
        cli_commands=[f"az disk delete --ids {rid} --yes"],
        api_logic="azure-mgmt-compute DisksOperations.begin_delete(...).",
        risk="medium",
        reversible=False,
    )


def _azure_public_ip_unassociated(rid: str, region: str) -> RemediationResult:
    return RemediationResult(
        strategy="Delete the unassociated public IP",
        cli_commands=[f"az network public-ip delete --ids {rid}"],
        api_logic="azure-mgmt-network PublicIPAddressesOperations.begin_delete(...).",
        risk="low",
        reversible=False,
    )


def _azure_vm_deallocated(rid: str, region: str) -> RemediationResult:
    return RemediationResult(
        strategy="Delete the deallocated VM",
        cli_commands=[f"az vm delete --ids {rid} --yes"],
        api_logic="azure-mgmt-compute VirtualMachinesOperations.begin_delete(...).",
        risk="high",
        reversible=False,
        notes=(
            "VM compute is already free while deallocated; the cost is the attached OS/data disks. "
            "`az vm delete` does NOT delete those disks — they remain and keep billing. After "
            "deleting the VM, remove its orphaned disks separately "
            '(e.g. `az disk list --query "[?managedBy==null]"` then `az disk delete`).'
        ),
    )


def _azure_snapshot_stale(rid: str, region: str) -> RemediationResult:
    return RemediationResult(
        strategy="Delete the stale disk snapshot",
        cli_commands=[f"az snapshot delete --ids {rid}"],
        api_logic="azure-mgmt-compute SnapshotsOperations.begin_delete(...).",
        risk="low",
        reversible=False,
        notes="Confirm this snapshot is not a required/retained backup before deleting.",
    )


#: Registry mapping ``rule_id`` -> builder(resource_id, region) -> RemediationResult.
_BUILDERS: Mapping[str, Callable[[str, str], RemediationResult]] = {
    "AWS_EBS_UNATTACHED": _ebs_unattached,
    "AWS_EC2_IDLE": _ec2_idle,
    "AWS_EIP_UNASSOCIATED": _eip_unassociated,
    "AWS_RDS_IDLE": _rds_idle,
    "AWS_ELB_IDLE": _elb_idle,
    "AWS_SNAPSHOT_STALE": _snapshot_stale,
    "AWS_NATGW_IDLE": _natgw_idle,
    "AZURE_DISK_UNATTACHED": _azure_disk_unattached,
    "AZURE_PUBLIC_IP_UNASSOCIATED": _azure_public_ip_unassociated,
    "AZURE_VM_DEALLOCATED": _azure_vm_deallocated,
    "AZURE_SNAPSHOT_STALE": _azure_snapshot_stale,
}

#: Fallback for unknown / unmapped rule ids — never emits a destructive command.
_GENERIC = RemediationResult(
    strategy="Review and decommission manually",
    cli_commands=[],
    api_logic=None,
    risk="medium",
    reversible=False,
    notes="No automated remediation template is registered for this rule id.",
)


def build_remediation(finding) -> RemediationResult:  # noqa: ANN001 - duck-typed FindingResult
    """Build a :class:`RemediationResult` for ``finding`` from its ``rule_id``.

    Looks the rule id up in the registry and fills ``{id}`` with
    ``finding.resource_id`` and ``{region}`` with ``finding.region`` (falling back to
    :data:`DEFAULT_REGION`). Unknown rule ids yield a safe generic result. Never raises
    on a malformed finding — any failure degrades to the generic result.
    """
    try:
        rule_id = getattr(finding, "rule_id", None)
        resource_id = getattr(finding, "resource_id", "") or ""
        region = getattr(finding, "region", None) or DEFAULT_REGION

        builder = _BUILDERS.get(rule_id) if rule_id else None
        if builder is None:
            logger.warning("no remediation builder for rule_id=%r", rule_id)
            return RemediationResult(
                strategy=_GENERIC.strategy,
                cli_commands=[],
                api_logic=_GENERIC.api_logic,
                risk=_GENERIC.risk,
                reversible=_GENERIC.reversible,
                notes=_GENERIC.notes,
            )
        return builder(resource_id, region)
    except Exception:  # pragma: no cover - defensive; pipeline must not crash
        logger.exception("failed to build remediation; using generic fallback")
        return RemediationResult(
            strategy=_GENERIC.strategy,
            cli_commands=[],
            api_logic=_GENERIC.api_logic,
            risk=_GENERIC.risk,
            reversible=_GENERIC.reversible,
            notes=_GENERIC.notes,
        )


def attach_remediations(findings: list) -> list:
    """Attach ``build_remediation(f)`` to each finding in place; return the list.

    One bad finding never aborts the batch — its remediation is simply left untouched.
    """
    for finding in findings:
        try:
            finding.remediation = build_remediation(finding)
        except Exception:  # pragma: no cover - defensive
            logger.exception("failed to attach remediation to a finding")
    return findings


_BANNER = (
    "# =============================================================================\n"
    "# FinOptic — generated remediation script\n"
    "# -----------------------------------------------------------------------------\n"
    "# This file was AUTO-GENERATED. Review every command before running.\n"
    "# It is SAFE BY DEFAULT: with DRY_RUN=1 (the default) each command is only\n"
    "# printed, never executed. Export DRY_RUN=0 to actually run the commands:\n"
    "#\n"
    "#     DRY_RUN=0 bash remediate.sh\n"
    "#\n"
    "# Commands are ordered by finding. Destructive actions (delete/release) cannot\n"
    "# run unless you explicitly opt in.\n"
    "# =============================================================================\n"
)

_PREAMBLE = (
    'DRY_RUN="${DRY_RUN:-1}"\n'
    "\n"
    "run() {\n"
    '  if [[ "${DRY_RUN}" == "1" ]]; then\n'
    '    echo "[DRY_RUN] $*"\n'
    "  else\n"
    '    echo "[RUN] $*"\n'
    '    "$@"\n'
    "  fi\n"
    "}\n"
)


def render_script(findings: list) -> str:
    """Render a safe, DRY-RUN-guarded bash script aggregating all remediation commands.

    The script starts with ``#!/usr/bin/env bash`` / ``set -euo pipefail``, an
    explanatory banner, a ``DRY_RUN`` guard and a ``run()`` helper. Each finding's
    commands are grouped under a comment header and invoked via ``run <command>`` so
    nothing destructive executes unless the operator exports ``DRY_RUN=0``.
    """
    lines: list[str] = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "",
        _BANNER.rstrip("\n"),
        "",
        _PREAMBLE.rstrip("\n"),
        "",
    ]

    emitted_any = False
    for finding in _iter_safe(findings):
        remediation = getattr(finding, "remediation", None) or build_remediation(finding)
        commands = list(getattr(remediation, "cli_commands", None) or [])

        severity = str(getattr(finding, "severity", "") or "unknown").upper()
        rule_id = getattr(finding, "rule_id", "") or "UNKNOWN_RULE"
        resource_id = getattr(finding, "resource_id", "") or "?"
        risk = getattr(remediation, "risk", "") or "unknown"
        try:
            monthly_cost = float(getattr(finding, "monthly_cost", 0.0) or 0.0)
        except (TypeError, ValueError):
            monthly_cost = 0.0

        # Route every interpolated field through _inline(): a newline in a billing-derived
        # resource_id must not break out of the comment and reach an executable line.
        header = (
            f"# [{_inline(severity)}] {_inline(rule_id)} {_inline(resource_id)} "
            f"— ${monthly_cost:.2f}/mo (risk: {_inline(risk)})"
        )
        lines.append(header)

        if not commands:
            strategy_text = _inline(getattr(remediation, "strategy", "review manually"))
            lines.append(f"#   (no automated command — {strategy_text})")
        else:
            for command in commands:
                lines.append(f"run {_quote_command(command)}")
        lines.append("")
        emitted_any = True

    if not emitted_any:
        lines.append("# No findings to remediate.")
        lines.append("")

    return "\n".join(lines) + "\n"


def _iter_safe(findings) -> Iterable:
    """Yield findings, tolerating a non-iterable / ``None`` input."""
    if not findings:
        return
    try:
        iterator = iter(findings)
    except TypeError:  # pragma: no cover - defensive
        logger.warning("render_script received a non-iterable; ignoring")
        return
    yield from iterator


def _quote_command(command: str) -> str:
    """Tokenize a command string and re-quote each token safely for ``run``.

    Falls back to a single shell-quoted argument if the command cannot be parsed
    (e.g. unbalanced quotes), so the rendered script is always syntactically valid.
    """
    text = str(command)
    try:
        tokens = shlex.split(text)
    except ValueError:
        return shlex.quote(text)
    if not tokens:
        return shlex.quote(text)
    return " ".join(shlex.quote(token) for token in tokens)


def _inline(text) -> str:
    """Collapse arbitrary text to a single safe comment line."""
    return " ".join(str(text).split())
