"""Thin, typed HTTP client for the FinOptic API.

``FinOpticClient`` wraps the same-origin JSON API exposed by the FinOptic service
(see the HTTP contract in the API layer) with a small, ergonomic surface. It is a
synchronous client built on :mod:`httpx`; every call performs ``raise_for_status``
so HTTP errors surface as :class:`httpx.HTTPStatusError`.

The client is intentionally transport-only: it returns plain ``dict``/``list``
JSON payloads that mirror the Pydantic response models (``IngestResponse``,
``SummaryOut``, ``FindingOut``, ``AnalyzeResponse``, ...). It does not re-validate
or re-shape responses, so it stays forward-compatible as the API evolves.

Usage
-----
Run a server (``uvicorn finoptic.api.app:app``) then::

    from finoptic.sdk import FinOpticClient

    client = FinOpticClient("http://localhost:8000")
    print(client.health())                 # {"status": "ok", "version": ..., "llm_enabled": ...}

    ingest = client.load_sample()          # bundled AWS + Azure sample data
    batch_uid = ingest["batch_uid"]

    summary = client.summary(batch_uid)
    print(summary["total_monthly_waste"], summary["finding_count"])

    page = client.list_findings(batch_uid=batch_uid, severity="critical", limit=10)
    for finding in page["items"]:
        print(finding["title"], finding["monthly_cost"])

    plan = client.analyze(batch_uid)
    print(plan["executive_summary"])

    script = client.remediation_script(batch_uid)   # ready-to-run shell script (text)

The client is a context manager and owns its underlying connection pool::

    with FinOpticClient(api_key="secret") as client:
        client.load_sample()
"""

from __future__ import annotations

import os
from types import TracebackType
from typing import Any

import httpx

__version__ = "0.1.0"

__all__ = ["FinOpticClient", "__version__"]

# Default per-request timeout (seconds) and base URL for a locally running server.
DEFAULT_BASE_URL = "http://localhost:8000"
DEFAULT_TIMEOUT = 30.0

# API route prefix shared by every data endpoint.
_API_PREFIX = "/api/v1"


class FinOpticClient:
    """Synchronous, typed client over the FinOptic HTTP API.

    Parameters
    ----------
    base_url:
        Origin of the FinOptic service, e.g. ``"http://localhost:8000"``. A
        trailing slash is tolerated and normalized away.
    api_key:
        Optional API key. When set, it is sent as the ``X-API-Key`` header on
        every request (required only when the server has ``require_auth`` on).
    timeout:
        Per-request timeout in seconds, applied to all calls.

    Notes
    -----
    The instance owns an :class:`httpx.Client` (and its connection pool). Use it
    as a context manager, or call :meth:`close` when done, to release sockets.
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        api_key: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

        headers: dict[str, str] = {"Accept": "application/json"}
        if api_key:
            headers["X-API-Key"] = api_key

        self._client = httpx.Client(
            base_url=self.base_url,
            headers=headers,
            timeout=timeout,
        )

    # -- lifecycle -----------------------------------------------------------

    def close(self) -> None:
        """Close the underlying HTTP client and release pooled connections."""
        self._client.close()

    def __enter__(self) -> FinOpticClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        auth = "with api_key" if self.api_key else "no auth"
        return f"FinOpticClient(base_url={self.base_url!r}, {auth})"

    # -- internal helpers ----------------------------------------------------

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Issue a request, raising :class:`httpx.HTTPStatusError` on 4xx/5xx."""
        response = self._client.request(method, path, **kwargs)
        response.raise_for_status()
        return response

    def _get_json(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        return self._request("GET", path, params=params).json()

    def _post_json(
        self,
        path: str,
        *,
        json: Any | None = None,
        files: dict[str, Any] | None = None,
    ) -> Any:
        return self._request("POST", path, json=json, files=files).json()

    @staticmethod
    def _clean_params(**params: Any) -> dict[str, Any]:
        """Drop ``None`` values so optional query params are simply omitted."""
        return {key: value for key, value in params.items() if value is not None}

    # -- health --------------------------------------------------------------

    def health(self) -> dict[str, Any]:
        """Return service health: ``{"status", "version", "llm_enabled"}``."""
        return self._get_json("/healthz")

    # -- ingest --------------------------------------------------------------

    def ingest_file(self, path: str | os.PathLike[str]) -> dict[str, Any]:
        """Upload a cost/usage export file for ingestion + detection.

        Sends ``POST /api/v1/ingest`` as ``multipart/form-data`` (field name
        ``file``). Returns the ``IngestResponse`` payload as a ``dict``.
        """
        file_path = os.fspath(path)
        filename = os.path.basename(file_path) or "upload"
        with open(file_path, "rb") as handle:
            files = {"file": (filename, handle, "application/octet-stream")}
            return self._post_json(f"{_API_PREFIX}/ingest", files=files)

    def load_sample(self) -> dict[str, Any]:
        """Ingest the bundled AWS + Azure sample dataset (no upload required).

        Sends ``POST /api/v1/ingest/sample``. Returns the ``IngestResponse``.
        """
        return self._post_json(f"{_API_PREFIX}/ingest/sample")

    # -- reads ---------------------------------------------------------------

    def list_batches(self) -> list[dict[str, Any]]:
        """List ingested batches (newest-first per the API), as a list of dicts."""
        return self._get_json(f"{_API_PREFIX}/batches")

    def summary(self, batch_uid: str | None = None) -> dict[str, Any]:
        """Return aggregate waste/savings rollups (``SummaryOut``).

        With ``batch_uid`` omitted, the API summarizes across all batches.
        """
        params = self._clean_params(batch_uid=batch_uid)
        return self._get_json(f"{_API_PREFIX}/summary", params=params)

    def list_findings(
        self,
        *,
        batch_uid: str | None = None,
        severity: str | None = None,
        category: str | None = None,
        provider: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Return a paginated findings page: ``{items, total, limit, offset}``.

        All filters are optional; ``None`` filters are omitted from the query.
        """
        params = self._clean_params(
            batch_uid=batch_uid,
            severity=severity,
            category=category,
            provider=provider,
            limit=limit,
            offset=offset,
        )
        return self._get_json(f"{_API_PREFIX}/findings", params=params)

    def get_finding(self, finding_uid: str) -> dict[str, Any]:
        """Fetch a single finding by its ``finding_uid`` (``FindingOut``)."""
        return self._get_json(f"{_API_PREFIX}/findings/{finding_uid}")

    # -- analyze -------------------------------------------------------------

    def analyze(self, batch_uid: str | None = None) -> dict[str, Any]:
        """Generate an executive summary + prioritized runbook (``AnalyzeResponse``).

        Sends ``POST /api/v1/analyze``. Uses GenAI when configured server-side,
        otherwise a deterministic fallback (see ``mode`` in the response).
        """
        payload = self._clean_params(batch_uid=batch_uid)
        return self._post_json(f"{_API_PREFIX}/analyze", json=payload)

    # -- export --------------------------------------------------------------

    def remediation_script(self, batch_uid: str | None = None) -> str:
        """Return the remediation shell script as text (``text/x-shellscript``).

        Sends ``GET /api/v1/export/remediation.sh``. The result is a ready-to-run
        script; ``batch_uid`` scopes it to a single batch when provided.
        """
        params = self._clean_params(batch_uid=batch_uid)
        response = self._request("GET", f"{_API_PREFIX}/export/remediation.sh", params=params)
        return response.text
