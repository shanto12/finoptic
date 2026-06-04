"""Framework-free domain types shared across ingest -> detect -> remediate.

These plain dataclasses are the internal contract that decouples the pipeline stages:

    ingest      : raw bytes            -> list[NormalizedRecord]
    detect      : list[NormalizedRecord] -> list[FindingResult]   (no remediation yet)
    remediate   : FindingResult          -> RemediationResult     (attached in-place)

Keeping them dependency-free means every stage (and the tests) can import them cheaply
without pulling in FastAPI/SQLAlchemy.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

# --- Controlled vocabularies -------------------------------------------------

#: Normalized resource types emitted by the ingest layer and consumed by detectors.
RESOURCE_TYPES: frozenset[str] = frozenset(
    {
        # AWS
        "ebs_volume",
        "ec2_instance",
        "elastic_ip",
        "rds_instance",
        "load_balancer",
        "snapshot",
        "nat_gateway",
        # Azure
        "managed_disk",
        "public_ip",
        "vm",
        "disk_snapshot",
    }
)

#: Waste categories used for aggregation and dashboard grouping.
CATEGORIES: frozenset[str] = frozenset(
    {
        "orphaned_storage",
        "idle_compute",
        "unassociated_network",
        "idle_network",
        "idle_database",
        "stale_snapshot",
    }
)

SEVERITIES: tuple[str, ...] = ("critical", "high", "medium", "low")
SEVERITY_RANK: dict[str, int] = {"critical": 4, "high": 3, "medium": 2, "low": 1}


@dataclass
class NormalizedRecord:
    """A single billing line item normalized across providers.

    ``state`` carries the detection signals an enrichment pipeline joins onto raw billing
    rows (AWS Config / Azure Resource Graph), e.g.::

        ebs_volume     -> {"attachment_state": "available" | "attached"}
        ec2_instance   -> {"power_state": "running"|"stopped", "avg_cpu_percent": 0.4,
                            "avg_network_bytes": 1200}
        elastic_ip     -> {"associated": false}
        rds_instance   -> {"connections_avg": 0, "power_state": "available"}
        load_balancer  -> {"request_count": 0, "healthy_targets": 0}
        snapshot       -> {"age_days": 210, "source_volume_exists": false}
        nat_gateway    -> {"active_connection_count": 0, "bytes_processed": 0}
        managed_disk   -> {"attachment_state": "available"}
        public_ip      -> {"associated": false}
        vm             -> {"power_state": "deallocated", "avg_cpu_percent": 0.0}
        disk_snapshot  -> {"age_days": 365}
    """

    provider: str  # "aws" | "azure"
    resource_id: str
    resource_type: str
    service: str = ""
    account_id: str = ""
    region: str | None = None
    usage_type: str | None = None
    monthly_cost: float = 0.0
    currency: str = "USD"
    tags: dict = field(default_factory=dict)
    state: dict = field(default_factory=dict)
    period_start: str | None = None
    period_end: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class RemediationResult:
    """Concrete, safe-by-default remediation for a finding."""

    strategy: str
    cli_commands: list[str] = field(default_factory=list)
    api_logic: str | None = None
    risk: str = "low"  # low | medium | high
    reversible: bool = True
    notes: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class FindingResult:
    """A detected unit of waste. ``remediation`` is attached by the remediate stage."""

    resource_id: str
    provider: str
    resource_type: str
    rule_id: str
    title: str
    category: str
    severity: str
    monthly_cost: float
    account_id: str = ""
    region: str | None = None
    confidence: float = 1.0
    evidence: dict = field(default_factory=dict)
    tags: dict = field(default_factory=dict)
    remediation: RemediationResult | None = None

    @property
    def annual_savings(self) -> float:
        return round(self.monthly_cost * 12.0, 2)

    def as_dict(self) -> dict:
        data = asdict(self)
        data["annual_savings"] = self.annual_savings
        return data
