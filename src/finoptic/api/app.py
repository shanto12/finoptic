"""FastAPI application factory and ASGI entrypoint.

Run with::

    uvicorn finoptic.api.app:app --reload
    # or
    python -m finoptic        # convenience launcher
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from starlette.responses import FileResponse, JSONResponse

from finoptic.api.middleware import ObservabilityMiddleware
from finoptic.api.routes import router
from finoptic.config import get_settings
from finoptic.db import SessionLocal, init_db
from finoptic.logging_config import configure_logging, get_logger
from finoptic.observability import metrics
from finoptic.schemas import HealthResponse

logger = get_logger("finoptic.app")

_DASHBOARD_DIR = Path(__file__).resolve().parent.parent / "dashboard"

DESCRIPTION = (
    "FinOptic — Cloud Cost Optimizer & Remediation Engine.\n\n"
    "Ingest AWS/Azure billing exports, detect orphaned and idle resources, generate the exact "
    "CLI/API remediation to reclaim spend, and produce a GenAI executive runbook."
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Bootstrap the schema on startup. A DB failure logs and degrades to not-ready
    (surfaced by /readyz) rather than crashing the process at import time."""
    try:
        init_db()
        logger.info("db_initialized")
    except Exception:  # noqa: BLE001 - stay up; readiness will report the problem
        logger.exception("db_init_failed")
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging("DEBUG" if settings.debug else "INFO")

    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        description=DESCRIPTION,
        contact={"name": "Shanto Mathew"},
        license_info={"name": "MIT"},
        lifespan=lifespan,
    )

    # Middleware (added inner-first; CORS added last so it wraps outermost).
    app.add_middleware(ObservabilityMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(router)

    # --- Operational endpoints ---
    @app.get("/healthz", response_model=HealthResponse, tags=["ops"])
    def healthz() -> HealthResponse:
        return HealthResponse(
            status="ok",
            version=settings.version,
            llm_enabled=settings.resolved_llm().enabled,
        )

    @app.get("/readyz", tags=["ops"])
    def readyz() -> JSONResponse:
        session = SessionLocal()
        try:
            session.execute(text("SELECT 1"))
            return JSONResponse({"status": "ready"})
        except Exception:  # noqa: BLE001 - readiness probe must not raise
            return JSONResponse(
                {"status": "not ready", "reason": "database unavailable"}, status_code=503
            )
        finally:
            session.close()

    @app.get("/metrics", include_in_schema=False)
    def metrics_endpoint():
        return metrics.metrics_response()

    # --- Dashboard (single-page, served from the dashboard/ assets) ---
    @app.get("/", include_in_schema=False)
    def dashboard_index():
        index = _DASHBOARD_DIR / "index.html"
        if index.exists():
            return FileResponse(index)
        return JSONResponse(
            {"service": settings.app_name, "version": settings.version, "docs": "/docs"}
        )

    @app.get("/app.js", include_in_schema=False)
    def dashboard_js():
        return _asset("app.js", "application/javascript")

    @app.get("/styles.css", include_in_schema=False)
    def dashboard_css():
        return _asset("styles.css", "text/css")

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        icon = _DASHBOARD_DIR / "favicon.ico"
        if icon.exists():
            return FileResponse(icon)
        return JSONResponse({}, status_code=204)

    logger.info(
        "app_created",
        extra={
            "version": settings.version,
            "llm_enabled": settings.resolved_llm().enabled,
            "llm_provider": settings.resolved_llm().provider,
        },
    )
    return app


def _asset(name: str, media_type: str):
    path = _DASHBOARD_DIR / name
    if path.exists():
        return FileResponse(path, media_type=media_type)
    return JSONResponse({"detail": f"{name} not found"}, status_code=404)


app = create_app()
