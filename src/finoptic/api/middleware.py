"""ASGI middleware: request-id correlation, structured access logs, and metrics."""

from __future__ import annotations

import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from finoptic.logging_config import get_logger, request_id_ctx
from finoptic.observability import metrics

logger = get_logger("finoptic.access")


def _route_template(request: Request) -> str:
    """Bounded metric label: the matched route's path template, not the raw URL.

    Prevents per-id paths (e.g. /api/v1/findings/<uuid>) from exploding metric cardinality.
    """
    route = request.scope.get("route")
    return getattr(route, "path", None) or "unmatched"


class ObservabilityMiddleware(BaseHTTPMiddleware):
    """Bind a request id, emit one structured access log line, and record metrics."""

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[override]
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        token = request_id_ctx.set(request_id)
        method = request.method
        path = request.url.path  # full path for the human-readable access log only
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration = time.perf_counter() - start
            endpoint = _route_template(request)
            metrics.REQUESTS.labels(method, endpoint, "500").inc()
            metrics.LATENCY.labels(method, endpoint).observe(duration)
            logger.exception("request_failed", extra={"method": method, "path": path})
            request_id_ctx.reset(token)
            raise

        duration = time.perf_counter() - start
        endpoint = _route_template(request)
        metrics.REQUESTS.labels(method, endpoint, str(response.status_code)).inc()
        metrics.LATENCY.labels(method, endpoint).observe(duration)
        response.headers["x-request-id"] = request_id
        logger.info(
            "request",
            extra={
                "method": method,
                "path": path,
                "status": response.status_code,
                "duration_ms": round(duration * 1000, 2),
            },
        )
        request_id_ctx.reset(token)
        return response
