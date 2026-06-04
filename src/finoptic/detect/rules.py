"""Deterministic waste-detection rules.

Each rule is a pure callable ``(NormalizedRecord) -> FindingResult | None``. A rule
inspects ``record.state`` (the enrichment signals joined onto a billing row) and, when
its predicate holds, emits a :class:`FindingResult` *without* remediation — the
remediate stage attaches that later.

Design contract:
    * A record matches **at most one** rule (rules are provider/resource-type scoped).
    * Rules are **defensive**: missing or ``None`` signals never raise and never match.
    * ``evidence`` captures exactly the signals that triggered the match (plus the cost)
      so a reviewer can audit *why* the resource was flagged.

The module-level :data:`RULES` list is the ordered registry the engine applies.
"""

from __future__ import annotations

from collections.abc import Callable

from finoptic.domain import FindingResult, NormalizedRecord

#: Type alias for a detection rule.
Rule = Callable[[NormalizedRecord], FindingResult | None]


# --- helpers ----------------------------------------------------------------


def _scoped(record: NormalizedRecord, provider: str, resource_type: str) -> bool:
    """Return True when ``record`` belongs to ``provider``/``resource_type``.

    Comparison is case-insensitive on the provider so callers need not pre-normalize.
    """
    return (record.provider or "").lower() == provider and record.resource_type == resource_type


def _finding(
    record: NormalizedRecord,
    *,
    rule_id: str,
    title: str,
    category: str,
    severity: str,
    confidence: float,
    evidence: dict,
) -> FindingResult | None:
    """Build a :class:`FindingResult` from ``record`` + the triggering ``evidence``.

    Identity / cost / locality fields are copied straight from the record; remediation
    is intentionally left ``None`` for the remediate stage to fill in. A non-positive
    monthly cost (e.g. a credit/refund/adjustment line) is never "waste", so it returns
    ``None`` — this guards all rules in one place.
    """
    if (record.monthly_cost or 0.0) <= 0:
        return None
    return FindingResult(
        resource_id=record.resource_id,
        provider=record.provider,
        resource_type=record.resource_type,
        rule_id=rule_id,
        title=title,
        category=category,
        severity=severity,
        monthly_cost=record.monthly_cost,
        account_id=record.account_id,
        region=record.region,
        confidence=confidence,
        evidence=evidence,
        tags=dict(record.tags or {}),
        remediation=None,
    )


def _to_float(value: object) -> float | None:
    """Best-effort numeric coercion; ``None``/non-numeric -> ``None`` (no match)."""
    if isinstance(value, bool):  # bools are ints in Python; treat as non-numeric here
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


#: Tag keys that mark a resource as intentionally retained — never recommend deleting it.
_RETENTION_TAG_KEYS = {
    "retention",
    "keep",
    "donotdelete",
    "do-not-delete",
    "preserve",
    "compliance",
}


def _has_retention_tag(tags: dict | None) -> bool:
    """True when any tag key signals the resource is deliberately kept (compliance/backup)."""
    return any((str(k) or "").strip().lower() in _RETENTION_TAG_KEYS for k in (tags or {}))


# --- AWS rules --------------------------------------------------------------


def aws_ebs_unattached(record: NormalizedRecord) -> FindingResult | None:
    """AWS_EBS_UNATTACHED: an EBS volume sitting in the ``available`` state."""
    if not _scoped(record, "aws", "ebs_volume"):
        return None
    state = record.state or {}
    if state.get("attachment_state") != "available":
        return None
    return _finding(
        record,
        rule_id="AWS_EBS_UNATTACHED",
        title=f"Unattached EBS volume {record.resource_id}",
        category="orphaned_storage",
        severity="high",
        confidence=1.0,
        evidence={"attachment_state": "available", "monthly_cost": record.monthly_cost},
    )


def aws_ec2_idle(record: NormalizedRecord) -> FindingResult | None:
    """AWS_EC2_IDLE: a *running* instance with negligible CPU **and** network."""
    if not _scoped(record, "aws", "ec2_instance"):
        return None
    state = record.state or {}
    if state.get("power_state") != "running":
        return None
    cpu = _to_float(state.get("avg_cpu_percent"))
    if cpu is None or cpu >= 5.0:
        return None
    net = _to_float(state.get("avg_network_bytes", 0))
    if net is None or net >= 50000:
        return None
    return _finding(
        record,
        rule_id="AWS_EC2_IDLE",
        title=f"Idle EC2 instance {record.resource_id} (avg CPU {cpu:.1f}%)",
        category="idle_compute",
        severity="high",
        confidence=0.85,
        evidence={
            "power_state": "running",
            "avg_cpu_percent": cpu,
            "avg_network_bytes": net,
            "monthly_cost": record.monthly_cost,
        },
    )


def aws_eip_unassociated(record: NormalizedRecord) -> FindingResult | None:
    """AWS_EIP_UNASSOCIATED: an Elastic IP not bound to any resource."""
    if not _scoped(record, "aws", "elastic_ip"):
        return None
    state = record.state or {}
    if state.get("associated") is not False:
        return None
    return _finding(
        record,
        rule_id="AWS_EIP_UNASSOCIATED",
        title=f"Unassociated Elastic IP {record.resource_id}",
        category="unassociated_network",
        severity="medium",
        confidence=1.0,
        evidence={"associated": False, "monthly_cost": record.monthly_cost},
    )


def aws_rds_idle(record: NormalizedRecord) -> FindingResult | None:
    """AWS_RDS_IDLE: an RDS instance with zero average connections."""
    if not _scoped(record, "aws", "rds_instance"):
        return None
    state = record.state or {}
    connections = _to_float(state.get("connections_avg"))
    if connections is None or connections != 0:
        return None
    return _finding(
        record,
        rule_id="AWS_RDS_IDLE",
        title=f"Idle RDS instance {record.resource_id} (0 connections)",
        category="idle_database",
        severity="high",
        confidence=0.8,
        evidence={"connections_avg": 0, "monthly_cost": record.monthly_cost},
    )


def aws_elb_idle(record: NormalizedRecord) -> FindingResult | None:
    """AWS_ELB_IDLE: a load balancer serving zero requests.

    Idle means sustained *zero traffic*. Zero healthy targets is deliberately NOT a trigger:
    a busy load balancer can momentarily report 0 healthy targets mid-deploy, and deleting it
    on that signal would take down production. ``healthy_targets`` is kept only as evidence.
    """
    if not _scoped(record, "aws", "load_balancer"):
        return None
    state = record.state or {}
    request_count = _to_float(state.get("request_count"))
    healthy_targets = _to_float(state.get("healthy_targets"))
    if not (request_count is not None and request_count == 0):
        return None
    evidence: dict = {"request_count": 0, "monthly_cost": record.monthly_cost}
    if healthy_targets is not None:
        evidence["healthy_targets"] = healthy_targets
    return _finding(
        record,
        rule_id="AWS_ELB_IDLE",
        title=f"Idle load balancer {record.resource_id}",
        category="idle_network",
        severity="medium",
        confidence=0.8,
        evidence=evidence,
    )


def aws_snapshot_stale(record: NormalizedRecord) -> FindingResult | None:
    """AWS_SNAPSHOT_STALE: an old snapshot **or** one whose source volume is gone."""
    if not _scoped(record, "aws", "snapshot"):
        return None
    state = record.state or {}
    age_days = _to_float(state.get("age_days"))
    source_exists = state.get("source_volume_exists")
    is_old = age_days is not None and age_days > 90
    orphaned = source_exists is False
    if not (is_old or orphaned):
        return None
    # Never recommend touching a snapshot deliberately tagged for retention/compliance.
    if _has_retention_tag(record.tags):
        return None
    evidence: dict = {"monthly_cost": record.monthly_cost}
    if age_days is not None:
        evidence["age_days"] = age_days
    if source_exists is not None:
        evidence["source_volume_exists"] = source_exists
    return _finding(
        record,
        rule_id="AWS_SNAPSHOT_STALE",
        title=f"Stale snapshot {record.resource_id}",
        category="stale_snapshot",
        severity="low",
        confidence=0.7,
        evidence=evidence,
    )


def aws_natgw_idle(record: NormalizedRecord) -> FindingResult | None:
    """AWS_NATGW_IDLE: a NAT gateway with zero active connections."""
    if not _scoped(record, "aws", "nat_gateway"):
        return None
    state = record.state or {}
    active = _to_float(state.get("active_connection_count"))
    if active is None or active != 0:
        return None
    return _finding(
        record,
        rule_id="AWS_NATGW_IDLE",
        title=f"Idle NAT gateway {record.resource_id} (0 active connections)",
        category="idle_network",
        severity="medium",
        confidence=0.8,
        evidence={"active_connection_count": 0, "monthly_cost": record.monthly_cost},
    )


# --- Azure rules ------------------------------------------------------------


def azure_disk_unattached(record: NormalizedRecord) -> FindingResult | None:
    """AZURE_DISK_UNATTACHED: a managed disk in the ``available`` state."""
    if not _scoped(record, "azure", "managed_disk"):
        return None
    state = record.state or {}
    if state.get("attachment_state") != "available":
        return None
    return _finding(
        record,
        rule_id="AZURE_DISK_UNATTACHED",
        title=f"Unattached managed disk {record.resource_id}",
        category="orphaned_storage",
        severity="high",
        confidence=1.0,
        evidence={"attachment_state": "available", "monthly_cost": record.monthly_cost},
    )


def azure_public_ip_unassociated(record: NormalizedRecord) -> FindingResult | None:
    """AZURE_PUBLIC_IP_UNASSOCIATED: a public IP not bound to any resource."""
    if not _scoped(record, "azure", "public_ip"):
        return None
    state = record.state or {}
    if state.get("associated") is not False:
        return None
    return _finding(
        record,
        rule_id="AZURE_PUBLIC_IP_UNASSOCIATED",
        title=f"Unassociated public IP {record.resource_id}",
        category="unassociated_network",
        severity="low",
        confidence=1.0,
        evidence={"associated": False, "monthly_cost": record.monthly_cost},
    )


def azure_vm_deallocated(record: NormalizedRecord) -> FindingResult | None:
    """AZURE_VM_DEALLOCATED: a VM left in the ``deallocated`` power state."""
    if not _scoped(record, "azure", "vm"):
        return None
    state = record.state or {}
    if state.get("power_state") != "deallocated":
        return None
    return _finding(
        record,
        rule_id="AZURE_VM_DEALLOCATED",
        title=f"Deallocated VM {record.resource_id}",
        category="idle_compute",
        severity="medium",
        confidence=0.9,
        evidence={"power_state": "deallocated", "monthly_cost": record.monthly_cost},
    )


def azure_snapshot_stale(record: NormalizedRecord) -> FindingResult | None:
    """AZURE_SNAPSHOT_STALE: a disk snapshot older than 90 days."""
    if not _scoped(record, "azure", "disk_snapshot"):
        return None
    state = record.state or {}
    age_days = _to_float(state.get("age_days"))
    if age_days is None or age_days <= 90:
        return None
    if _has_retention_tag(record.tags):
        return None
    return _finding(
        record,
        rule_id="AZURE_SNAPSHOT_STALE",
        title=f"Stale disk snapshot {record.resource_id} ({int(age_days)} days old)",
        category="stale_snapshot",
        severity="low",
        confidence=0.7,
        evidence={"age_days": age_days, "monthly_cost": record.monthly_cost},
    )


#: Ordered registry of every detection rule the engine applies.
RULES: list[Rule] = [
    aws_ebs_unattached,
    aws_ec2_idle,
    aws_eip_unassociated,
    aws_rds_idle,
    aws_elb_idle,
    aws_snapshot_stale,
    aws_natgw_idle,
    azure_disk_unattached,
    azure_public_ip_unassociated,
    azure_vm_deallocated,
    azure_snapshot_stale,
]
