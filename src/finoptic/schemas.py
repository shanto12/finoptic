"""Pydantic v2 schemas — the API request/response contract.

These are the single source of truth that the API layer, SDK, dashboard and tests all
agree on. ORM rows are serialized through ``from_attributes``.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class Provider(str, Enum):
    aws = "aws"
    azure = "azure"


class Severity(str, Enum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"


# Ordering used for sorting/prioritization (higher = more urgent).
SEVERITY_RANK: dict[str, int] = {"critical": 4, "high": 3, "medium": 2, "low": 1}


class Remediation(BaseModel):
    """The concrete, safe-by-default action to reclaim a wasted resource."""

    strategy: str = Field(..., description="Human-readable remediation strategy.")
    cli_commands: list[str] = Field(default_factory=list, description="Exact CLI to run.")
    api_logic: str | None = Field(None, description="Equivalent SDK/API call description.")
    risk: str = Field("low", description="Operational risk: low | medium | high.")
    reversible: bool = Field(True, description="Whether the action can be undone.")
    notes: str | None = None


class FindingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    finding_uid: str
    resource_id: str
    provider: str
    account_id: str = ""
    region: str | None = None
    resource_type: str
    rule_id: str
    title: str
    category: str
    severity: str
    confidence: float = 1.0
    monthly_cost: float = 0.0
    annual_savings: float = 0.0
    evidence: dict = Field(default_factory=dict)
    remediation: Remediation
    tags: dict = Field(default_factory=dict)
    detected_at: datetime | None = None


class ResourceRecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    provider: str
    account_id: str = ""
    region: str | None = None
    service: str = ""
    resource_id: str
    resource_type: str
    usage_type: str | None = None
    monthly_cost: float = 0.0
    currency: str = "USD"
    tags: dict = Field(default_factory=dict)
    state: dict = Field(default_factory=dict)


class IngestResponse(BaseModel):
    batch_uid: str
    source_filename: str
    provider: str
    record_count: int
    finding_count: int
    total_monthly_waste: float
    total_annual_savings: float
    by_severity: dict[str, float] = Field(default_factory=dict)


class SummaryOut(BaseModel):
    batch_uid: str | None = None
    total_monthly_waste: float = 0.0
    total_annual_savings: float = 0.0
    resource_count: int = 0
    finding_count: int = 0
    by_severity: dict[str, float] = Field(default_factory=dict)
    by_category: dict[str, float] = Field(default_factory=dict)
    by_provider: dict[str, float] = Field(default_factory=dict)
    by_resource_type: dict[str, float] = Field(default_factory=dict)
    top_findings: list[FindingOut] = Field(default_factory=list)
    generated_at: datetime


class AnalyzeRequest(BaseModel):
    batch_uid: str | None = Field(
        None, description="Analyze a specific batch; defaults to the latest ingest."
    )


class AnalyzeResponse(BaseModel):
    batch_uid: str | None = None
    mode: str = Field(..., description="'llm' when GenAI was used, else 'deterministic'.")
    provider: str = "none"
    executive_summary: str
    prioritized_runbook: list[str] = Field(default_factory=list)
    total_monthly_waste: float = 0.0
    total_annual_savings: float = 0.0
    generated_at: datetime


class HealthResponse(BaseModel):
    status: str
    version: str
    llm_enabled: bool = False


class ErrorResponse(BaseModel):
    detail: str
