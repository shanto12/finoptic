"""SDK integration test against a live in-process Uvicorn server."""

from __future__ import annotations

import socket
import threading
import time

import httpx
import pytest
import uvicorn

from finoptic.api.app import app
from finoptic.sdk import FinOpticClient


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Server(uvicorn.Server):
    def install_signal_handlers(self) -> None:  # don't hijack pytest's signals
        pass


@pytest.fixture(scope="module")
def base_url() -> str:
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = _Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):  # wait up to ~10s for readiness
        try:
            httpx.get(f"{url}/healthz", timeout=0.5)
            break
        except Exception:
            time.sleep(0.1)
    else:
        pytest.fail("server did not start")
    yield url
    server.should_exit = True
    thread.join(timeout=5)


def test_sdk_end_to_end(base_url: str) -> None:
    client = FinOpticClient(base_url=base_url)
    try:
        assert client.health()["status"] == "ok"

        ingest = client.load_sample()
        assert ingest["finding_count"] == 17
        batch_uid = ingest["batch_uid"]

        summary = client.summary(batch_uid)
        assert summary["total_monthly_waste"] == 1095.32

        findings = client.list_findings(batch_uid=batch_uid, severity="high", limit=100)
        assert findings["items"] and all(i["severity"] == "high" for i in findings["items"])

        one = client.get_finding(findings["items"][0]["finding_uid"])
        assert one["finding_uid"] == findings["items"][0]["finding_uid"]

        analysis = client.analyze(batch_uid)
        assert analysis["mode"] == "deterministic"

        script = client.remediation_script(batch_uid)
        assert script.startswith("#!/usr/bin/env bash")
    finally:
        client.close()
