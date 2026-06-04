"""HTTP API routes (``/api/v1``)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from pydantic import BaseModel
from starlette.responses import Response

from finoptic import service
from finoptic.api.deps import rate_limit, require_api_key
from finoptic.config import get_settings
from finoptic.db import get_session
from finoptic.logging_config import get_logger
from finoptic.schemas import (
    AnalyzeRequest,
    AnalyzeResponse,
    FindingOut,
    IngestResponse,
    SummaryOut,
)

logger = get_logger("finoptic.api")

router = APIRouter(
    prefix="/api/v1",
    tags=["finoptic"],
    dependencies=[Depends(rate_limit), Depends(require_api_key)],
)


class FindingList(BaseModel):
    items: list[FindingOut]
    total: int
    limit: int
    offset: int


class BatchInfo(BaseModel):
    batch_uid: str
    source_filename: str
    provider: str
    record_count: int
    finding_count: int
    total_monthly_waste: float
    created_at: str | None = None


@router.post("/ingest", response_model=IngestResponse, status_code=status.HTTP_201_CREATED)
def ingest(
    request: Request, file: UploadFile = File(...), session=Depends(get_session)
) -> IngestResponse:
    """Ingest a cloud billing export (AWS CUR CSV or Azure billing JSON)."""
    max_bytes = get_settings().max_upload_bytes
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > max_bytes:
        raise HTTPException(status_code=413, detail="Upload too large")
    # Read at most max_bytes+1 so an oversized body is rejected, not buffered whole.
    content = file.file.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise HTTPException(status_code=413, detail="Upload too large")
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    try:
        batch = service.ingest_content(session, content, file.filename or "upload")
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - log internally, return a generic message
        logger.exception("ingest_failed", extra={"filename": file.filename})
        raise HTTPException(
            status_code=422, detail="Could not process export: invalid or unsupported format"
        ) from exc
    return service.batch_to_ingest_response(session, batch)


@router.post("/ingest/sample", response_model=IngestResponse, status_code=status.HTTP_201_CREATED)
def ingest_sample(session=Depends(get_session)) -> IngestResponse:
    """Ingest the bundled AWS + Azure sample exports (for demos and first-run UX)."""
    try:
        batch = service.ingest_sample(session)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return service.batch_to_ingest_response(session, batch)


@router.get("/batches", response_model=list[BatchInfo])
def list_batches(session=Depends(get_session)) -> list[dict]:
    return service.list_batches(session)


@router.get("/summary", response_model=SummaryOut)
def summary(batch_uid: str | None = Query(None), session=Depends(get_session)) -> SummaryOut:
    """Aggregate waste summary for a batch (defaults to the most recent ingest)."""
    return service.get_summary(session, batch_uid)


@router.get("/findings", response_model=FindingList)
def findings(
    batch_uid: str | None = Query(None),
    severity: str | None = Query(None),
    category: str | None = Query(None),
    provider: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session=Depends(get_session),
) -> FindingList:
    items, total = service.list_findings(
        session,
        batch_uid=batch_uid,
        severity=severity,
        category=category,
        provider=provider,
        limit=limit,
        offset=offset,
    )
    return FindingList(items=items, total=total, limit=limit, offset=offset)


@router.get("/findings/{finding_uid}", response_model=FindingOut)
def finding(finding_uid: str, session=Depends(get_session)) -> FindingOut:
    found = service.get_finding(session, finding_uid)
    if not found:
        raise HTTPException(status_code=404, detail="Finding not found")
    return found


@router.post("/analyze", response_model=AnalyzeResponse)
def analyze_endpoint(
    req: AnalyzeRequest | None = None, session=Depends(get_session)
) -> AnalyzeResponse:
    """Generate a GenAI executive summary + prioritized remediation runbook."""
    batch_uid = req.batch_uid if req else None
    return service.run_analysis(session, batch_uid)


@router.get("/export/remediation.sh")
def export_script(batch_uid: str | None = Query(None), session=Depends(get_session)) -> Response:
    """Download a safe, DRY-RUN-guarded bash remediation script for the batch."""
    script = service.export_remediation_script(session, batch_uid)
    return Response(
        content=script,
        media_type="text/x-shellscript",
        headers={"Content-Disposition": "attachment; filename=remediation.sh"},
    )


@router.get("/export/findings.csv")
def export_findings_csv(
    batch_uid: str | None = Query(None), session=Depends(get_session)
) -> Response:
    """Download the findings as a CSV spreadsheet (FinOps-friendly)."""
    csv_text = service.export_findings_csv(session, batch_uid)
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=findings.csv"},
    )
