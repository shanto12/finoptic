"""SQLAlchemy ORM models — the persisted data contract.

Three tables:
  * ``ingest_batches`` — one row per uploaded billing export.
  * ``resource_records`` — normalized per-resource billing line items.
  * ``findings``        — detected waste with attached remediation.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from finoptic.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class IngestBatch(Base):
    __tablename__ = "ingest_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_uid: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    source_filename: Mapped[str] = mapped_column(String(512), default="upload")
    provider: Mapped[str] = mapped_column(String(16), default="aws")
    record_count: Mapped[int] = mapped_column(Integer, default=0)
    finding_count: Mapped[int] = mapped_column(Integer, default=0)
    total_monthly_waste: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    records: Mapped[list[ResourceRecord]] = relationship(
        back_populates="batch", cascade="all, delete-orphan"
    )
    findings: Mapped[list[Finding]] = relationship(
        back_populates="batch", cascade="all, delete-orphan"
    )


class ResourceRecord(Base):
    """A normalized billing line item enriched with resource-state signals.

    ``state`` carries the detection signals an enrichment pipeline joins onto raw billing
    data (e.g. from AWS Config / Azure Resource Graph): attachment_state, vm_power_state,
    avg_cpu_percent, ip_associated, age_days, has_targets, etc.
    """

    __tablename__ = "resource_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("ingest_batches.id"), index=True)

    provider: Mapped[str] = mapped_column(String(16))
    account_id: Mapped[str] = mapped_column(String(128), default="")
    region: Mapped[str | None] = mapped_column(String(64), nullable=True)
    service: Mapped[str] = mapped_column(String(128), default="")
    resource_id: Mapped[str] = mapped_column(String(512), index=True)
    resource_type: Mapped[str] = mapped_column(String(64), index=True)
    usage_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    monthly_cost: Mapped[float] = mapped_column(Float, default=0.0)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    tags: Mapped[dict] = mapped_column(JSON, default=dict)
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    period_start: Mapped[str | None] = mapped_column(String(32), nullable=True)
    period_end: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    batch: Mapped[IngestBatch] = relationship(back_populates="records")


class Finding(Base):
    __tablename__ = "findings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("ingest_batches.id"), index=True)

    finding_uid: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    resource_id: Mapped[str] = mapped_column(String(512), index=True)
    provider: Mapped[str] = mapped_column(String(16))
    account_id: Mapped[str] = mapped_column(String(128), default="")
    region: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resource_type: Mapped[str] = mapped_column(String(64), index=True)

    rule_id: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(256))
    category: Mapped[str] = mapped_column(String(64), index=True)
    severity: Mapped[str] = mapped_column(String(16), index=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    monthly_cost: Mapped[float] = mapped_column(Float, default=0.0)
    annual_savings: Mapped[float] = mapped_column(Float, default=0.0)

    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    remediation: Mapped[dict] = mapped_column(JSON, default=dict)
    tags: Mapped[dict] = mapped_column(JSON, default=dict)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    batch: Mapped[IngestBatch] = relationship(back_populates="findings")
