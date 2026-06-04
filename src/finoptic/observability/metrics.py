"""Prometheus metrics for FinOptic.

Exposed at ``GET /metrics`` in the standard text exposition format so any Prometheus /
OpenTelemetry collector can scrape it.
"""

from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.responses import Response

REQUESTS = Counter(
    "finoptic_requests_total",
    "Total HTTP requests processed",
    ["method", "endpoint", "status"],
)
LATENCY = Histogram(
    "finoptic_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "endpoint"],
)
RECORDS_INGESTED = Counter(
    "finoptic_records_ingested_total",
    "Total resource records ingested",
)
FINDINGS_DETECTED = Counter(
    "finoptic_findings_total",
    "Total findings detected",
    ["severity"],
)


def metrics_response() -> Response:
    """Render the Prometheus exposition payload."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
