"""Application service layer — orchestrates the ingest -> detect -> remediate -> persist pipeline
and the read/analyze paths used by the API and CLI.

This is the single place where the framework-free pipeline (domain dataclasses) meets the
persistence layer (SQLAlchemy ORM) and the API contract (Pydantic schemas).
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from finoptic import models
from finoptic.analyze import analyze
from finoptic.detect import detect, summarize
from finoptic.domain import FindingResult
from finoptic.ingest import parse, parse_aws_cur, parse_azure_billing
from finoptic.logging_config import get_logger
from finoptic.observability import metrics
from finoptic.remediate import attach_remediations, render_script
from finoptic.schemas import AnalyzeResponse, FindingOut, IngestResponse, SummaryOut

logger = get_logger("finoptic.service")

# sample_data lives at the project root (src/finoptic/service.py -> parents[2]).
SAMPLE_DIR = Path(__file__).resolve().parents[2] / "sample_data"


# --------------------------------------------------------------------------- #
# Write path                                                                   #
# --------------------------------------------------------------------------- #
def _run_pipeline(records: list[FindingResult]) -> list[FindingResult]:
    findings = detect(records)
    attach_remediations(findings)
    return findings


def _finding_to_orm(f: FindingResult, batch_id: int) -> models.Finding:
    remediation = f.remediation.as_dict() if f.remediation else {}
    # Round once, and derive every dependent figure from the rounded value so the
    # persisted/served numbers are self-consistent (annual == 12 * monthly).
    monthly = round(f.monthly_cost, 2)
    return models.Finding(
        batch_id=batch_id,
        finding_uid=uuid4().hex,
        resource_id=f.resource_id,
        provider=f.provider,
        account_id=f.account_id,
        region=f.region,
        resource_type=f.resource_type,
        rule_id=f.rule_id,
        title=f.title,
        category=f.category,
        severity=f.severity,
        confidence=f.confidence,
        monthly_cost=monthly,
        annual_savings=round(monthly * 12.0, 2),
        evidence=f.evidence,
        remediation=remediation,
        tags=f.tags,
    )


def ingest_records(
    session: Session, provider: str, records: list, filename: str
) -> models.IngestBatch:
    """Persist a batch with its records and detected findings, returning the batch."""
    findings = _run_pipeline(records)
    # Sum the SAME rounded per-finding values that are persisted/served, so the stored
    # batch total agrees with summarize() and by_severity across every endpoint.
    total_waste = round(sum(round(f.monthly_cost, 2) for f in findings), 2)

    batch = models.IngestBatch(
        batch_uid=uuid4().hex,
        source_filename=filename,
        provider=provider[:16],
        record_count=len(records),
        finding_count=len(findings),
        total_monthly_waste=total_waste,
    )
    session.add(batch)
    session.flush()  # assign batch.id

    for r in records:
        session.add(
            models.ResourceRecord(
                batch_id=batch.id,
                provider=r.provider,
                account_id=r.account_id,
                region=r.region,
                service=r.service,
                resource_id=r.resource_id,
                resource_type=r.resource_type,
                usage_type=r.usage_type,
                monthly_cost=r.monthly_cost,
                currency=r.currency,
                tags=r.tags,
                state=r.state,
                period_start=r.period_start,
                period_end=r.period_end,
            )
        )
    for f in findings:
        session.add(_finding_to_orm(f, batch.id))

    session.commit()
    session.refresh(batch)

    metrics.RECORDS_INGESTED.inc(len(records))
    for f in findings:
        metrics.FINDINGS_DETECTED.labels(f.severity).inc()
    logger.info(
        "ingest_complete",
        extra={
            "batch_uid": batch.batch_uid,
            "records": len(records),
            "findings": len(findings),
            "monthly_waste": total_waste,
        },
    )
    return batch


def ingest_content(session: Session, content: bytes | str, filename: str) -> models.IngestBatch:
    provider, records = parse(content, filename)
    return ingest_records(session, provider, records, filename)


def ingest_sample(session: Session) -> models.IngestBatch:
    """Ingest the bundled AWS + Azure sample exports as a single combined batch."""
    records: list = []
    aws = _read_sample("aws_cur_sample.csv")
    azure = _read_sample("azure_billing_sample.json")
    if aws:
        records.extend(parse_aws_cur(aws))
    if azure:
        records.extend(parse_azure_billing(azure))
    if not records:
        raise FileNotFoundError(f"No sample data found under {SAMPLE_DIR}")
    return ingest_records(session, "aws+azure", records, "sample_data (aws+azure)")


# --------------------------------------------------------------------------- #
# Read path                                                                    #
# --------------------------------------------------------------------------- #
def _resolve_batch(session: Session, batch_uid: str | None) -> models.IngestBatch | None:
    if batch_uid:
        return session.scalar(
            select(models.IngestBatch).where(models.IngestBatch.batch_uid == batch_uid)
        )
    return session.scalar(select(models.IngestBatch).order_by(models.IngestBatch.id.desc()))


def _finding_rows(session: Session, batch_id: int | None) -> list[models.Finding]:
    stmt = select(models.Finding)
    if batch_id is not None:
        stmt = stmt.where(models.Finding.batch_id == batch_id)
    return list(session.execute(stmt).scalars().all())


def _resource_count(session: Session, batch_id: int | None) -> int:
    stmt = select(func.count()).select_from(models.ResourceRecord)
    if batch_id is not None:
        stmt = stmt.where(models.ResourceRecord.batch_id == batch_id)
    return int(session.scalar(stmt) or 0)


def _rows_to_results(rows: list[models.Finding]) -> list[FindingResult]:
    return [
        FindingResult(
            resource_id=r.resource_id,
            provider=r.provider,
            resource_type=r.resource_type,
            rule_id=r.rule_id,
            title=r.title,
            category=r.category,
            severity=r.severity,
            monthly_cost=r.monthly_cost,
            account_id=r.account_id,
            region=r.region,
            confidence=r.confidence,
            evidence=r.evidence or {},
            tags=r.tags or {},
        )
        for r in rows
    ]


def _to_finding_out(row: models.Finding) -> FindingOut:
    return FindingOut.model_validate(row)


def list_batches(session: Session) -> list[dict]:
    rows = (
        session.execute(select(models.IngestBatch).order_by(models.IngestBatch.id.desc()))
        .scalars()
        .all()
    )
    return [
        {
            "batch_uid": b.batch_uid,
            "source_filename": b.source_filename,
            "provider": b.provider,
            "record_count": b.record_count,
            "finding_count": b.finding_count,
            "total_monthly_waste": b.total_monthly_waste,
            "created_at": b.created_at.isoformat() if b.created_at else None,
        }
        for b in rows
    ]


def batch_to_ingest_response(session: Session, batch: models.IngestBatch) -> IngestResponse:
    by_severity: dict[str, float] = {}
    for row in _finding_rows(session, batch.id):
        by_severity[row.severity] = round(by_severity.get(row.severity, 0.0) + row.monthly_cost, 2)
    return IngestResponse(
        batch_uid=batch.batch_uid,
        source_filename=batch.source_filename,
        provider=batch.provider,
        record_count=batch.record_count,
        finding_count=batch.finding_count,
        total_monthly_waste=batch.total_monthly_waste,
        total_annual_savings=round(batch.total_monthly_waste * 12, 2),
        by_severity=by_severity,
    )


def get_summary(session: Session, batch_uid: str | None = None) -> SummaryOut:
    batch = _resolve_batch(session, batch_uid)
    if batch_uid and batch is None:
        # Unknown batch_uid must NOT fall through to a whole-database aggregate.
        return SummaryOut(batch_uid=None, generated_at=datetime.now(timezone.utc))
    batch_id = batch.id if batch else None
    rows = _finding_rows(session, batch_id)
    results = _rows_to_results(rows)
    agg = summarize(results, _resource_count(session, batch_id))
    top = sorted(rows, key=lambda r: r.monthly_cost, reverse=True)[:10]
    return SummaryOut(
        batch_uid=batch.batch_uid if batch else None,
        total_monthly_waste=agg["total_monthly_waste"],
        total_annual_savings=agg["total_annual_savings"],
        resource_count=agg["resource_count"],
        finding_count=agg["finding_count"],
        by_severity=agg["by_severity"],
        by_category=agg["by_category"],
        by_provider=agg["by_provider"],
        by_resource_type=agg["by_resource_type"],
        top_findings=[_to_finding_out(r) for r in top],
        generated_at=datetime.now(timezone.utc),
    )


def list_findings(
    session: Session,
    *,
    batch_uid: str | None = None,
    severity: str | None = None,
    category: str | None = None,
    provider: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[FindingOut], int]:
    batch = _resolve_batch(session, batch_uid)
    if batch_uid and batch is None:
        return [], 0

    stmt = select(models.Finding)
    if batch is not None:
        stmt = stmt.where(models.Finding.batch_id == batch.id)
    if severity:
        stmt = stmt.where(models.Finding.severity == severity)
    if category:
        stmt = stmt.where(models.Finding.category == category)
    if provider:
        stmt = stmt.where(models.Finding.provider == provider)

    total = int(session.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    rows = (
        session.execute(
            # id.asc() is a stable tiebreaker so pagination is deterministic across ties/engines.
            stmt.order_by(models.Finding.monthly_cost.desc(), models.Finding.id.asc())
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    return [_to_finding_out(r) for r in rows], total


def get_finding(session: Session, finding_uid: str) -> FindingOut | None:
    row = session.scalar(select(models.Finding).where(models.Finding.finding_uid == finding_uid))
    return _to_finding_out(row) if row else None


def run_analysis(session: Session, batch_uid: str | None = None) -> AnalyzeResponse:
    batch = _resolve_batch(session, batch_uid)
    if batch_uid and batch is None:
        return AnalyzeResponse(
            batch_uid=None,
            mode="deterministic",
            provider="none",
            executive_summary="No batch found for the supplied batch_uid.",
            generated_at=datetime.now(timezone.utc),
        )
    batch_id = batch.id if batch else None
    rows = _finding_rows(session, batch_id)
    results = _rows_to_results(rows)
    agg = summarize(results, _resource_count(session, batch_id))
    result = analyze(results, agg)
    return AnalyzeResponse(
        batch_uid=batch.batch_uid if batch else None,
        mode=result.mode,
        provider=result.provider,
        executive_summary=result.executive_summary,
        prioritized_runbook=result.prioritized_runbook,
        total_monthly_waste=agg["total_monthly_waste"],
        total_annual_savings=agg["total_annual_savings"],
        generated_at=datetime.now(timezone.utc),
    )


def export_remediation_script(session: Session, batch_uid: str | None = None) -> str:
    batch = _resolve_batch(session, batch_uid)
    if batch_uid and batch is None:
        # Don't emit commands for the whole database on a bogus batch_uid (cross-batch leak).
        return render_script([])
    rows = _finding_rows(session, batch.id if batch else None)
    results = _rows_to_results(rows)
    attach_remediations(results)
    return render_script(results)


_CSV_COLUMNS = [
    "finding_uid", "provider", "account_id", "region", "resource_type", "resource_id",
    "rule_id", "severity", "category", "confidence", "monthly_cost", "annual_savings",
    "title", "remediation_cli", "risk", "reversible",
]


def export_findings_csv(session: Session, batch_uid: str | None = None) -> str:
    """Render the batch's findings as CSV (one row per finding, sorted by monthly cost)."""
    batch = _resolve_batch(session, batch_uid)
    if batch_uid and batch is None:
        rows: list[models.Finding] = []
    else:
        rows = sorted(
            _finding_rows(session, batch.id if batch else None),
            key=lambda r: r.monthly_cost,
            reverse=True,
        )

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(_CSV_COLUMNS)
    for r in rows:
        remediation = r.remediation or {}
        commands = remediation.get("cli_commands") or []
        writer.writerow(
            [
                r.finding_uid, r.provider, r.account_id, r.region or "", r.resource_type,
                r.resource_id, r.rule_id, r.severity, r.category, r.confidence,
                f"{r.monthly_cost:.2f}", f"{r.annual_savings:.2f}", r.title,
                " && ".join(commands), remediation.get("risk", ""),
                remediation.get("reversible", ""),
            ]
        )
    return buffer.getvalue()


# --------------------------------------------------------------------------- #
# Helpers                                                                       #
# --------------------------------------------------------------------------- #
def _read_sample(name: str) -> str | None:
    for base in (SAMPLE_DIR, Path.cwd() / "sample_data", Path.cwd()):
        candidate = base / name
        if candidate.exists():
            return candidate.read_text(encoding="utf-8")
    return None
