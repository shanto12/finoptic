"""Shared pytest fixtures.

The database URL and LLM provider are pinned BEFORE importing the app so tests run against
an isolated temp SQLite file in fully-offline (deterministic) mode.
"""

from __future__ import annotations

import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="finoptic-test-")
os.environ["FINOPTIC_DATABASE_URL"] = f"sqlite:///{_TMP}/test.db"
os.environ["FINOPTIC_LLM_PROVIDER"] = "none"  # force deterministic analysis
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.pop("OPENAI_API_KEY", None)
os.environ.pop("AZURE_OPENAI_API_KEY", None)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from finoptic.api.app import app  # noqa: E402

# Known-good expectations for the bundled sample data (cross-checked by detection + builders).
EXPECTED = {
    "records": 32,
    "findings": 17,
    "monthly_waste": 1095.32,
    "annual_waste": 13143.84,
    "by_provider": {"aws": 753.79, "azure": 341.53},
    "by_severity": {"high": 762.56, "medium": 295.51, "low": 37.25},
    "rule_ids": {
        "AWS_EBS_UNATTACHED",
        "AWS_EC2_IDLE",
        "AWS_EIP_UNASSOCIATED",
        "AWS_RDS_IDLE",
        "AWS_ELB_IDLE",
        "AWS_SNAPSHOT_STALE",
        "AWS_NATGW_IDLE",
        "AZURE_DISK_UNATTACHED",
        "AZURE_PUBLIC_IP_UNASSOCIATED",
        "AZURE_VM_DEALLOCATED",
        "AZURE_SNAPSHOT_STALE",
    },
}


@pytest.fixture(scope="session")
def client() -> TestClient:
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def sample_batch(client: TestClient) -> str:
    """Ingest the bundled sample data once and return its batch_uid."""
    resp = client.post("/api/v1/ingest/sample")
    assert resp.status_code == 201, resp.text
    return resp.json()["batch_uid"]
