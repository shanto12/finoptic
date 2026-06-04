#!/usr/bin/env python3
"""Build the static Netlify demo (``web/``) from FinOptic's REAL pipeline output.

There is no backend on Netlify, so this script runs the API in-process (TestClient),
captures genuine responses for the bundled sample, and assembles a self-contained static
dashboard. The shipped ``app.js`` is copied verbatim except for a one-line, flag-guarded
demo branch injected into its ``api()`` helper, so the *exact same UI* runs with the baked
data instead of a live API. Regenerate with:  python scripts/build_static_demo.py
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

os.environ.setdefault("FINOPTIC_LLM_PROVIDER", "none")
_TMPDB = Path(tempfile.gettempdir()) / "finoptic_demo_build.db"
os.environ.setdefault("FINOPTIC_DATABASE_URL", f"sqlite:///{_TMPDB}")

from fastapi.testclient import TestClient  # noqa: E402

from finoptic.api.app import app  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / "src" / "finoptic" / "dashboard"
WEB = ROOT / "web"

API_NEEDLE = 'async function api(path, { method = "GET", body, headers, isForm = false } = {}) {'
API_INJECT = (
    API_NEEDLE
    + "\n  if (window.FINOPTIC_DEMO)"
    + " return window.__demoApi(path, { method, body, headers, isForm });"
)

DEMO_JS = """/* Static-demo shim: serve baked pipeline output instead of a live API.
 * Loaded before app.js; app.js's api() checks window.FINOPTIC_DEMO and delegates here. */
(function () {
  window.FINOPTIC_DEMO = true;
  var clone = function (x) { return JSON.parse(JSON.stringify(x === undefined ? null : x)); };
  window.__demoApi = async function (path) {
    var data = window.__DEMO_DATA__ || {};
    var parts = String(path).split("?");
    var base = parts[0];
    var q = new URLSearchParams(parts[1] || "");
    if (base === "/healthz") return clone(data.health || { status: "ok" });
    if (base === "/api/v1/batches") return clone(data.batches || []);
    if (base === "/api/v1/summary") return clone(data.summary || {});
    if (base === "/api/v1/analyze") return clone(data.analyze || {});
    if (base === "/api/v1/ingest" || base === "/api/v1/ingest/sample") return clone(data.ingest || {});
    if (base === "/api/v1/findings") {
      var all = ((data.findings && data.findings.items) || []).slice();
      var sev = q.get("severity"), cat = q.get("category"), prov = q.get("provider");
      if (sev) all = all.filter(function (f) { return f.severity === sev; });
      if (cat) all = all.filter(function (f) { return f.category === cat; });
      if (prov) all = all.filter(function (f) { return f.provider === prov; });
      var total = all.length;
      var limit = Number(q.get("limit") || 25);
      var offset = Number(q.get("offset") || 0);
      return { items: clone(all.slice(offset, offset + limit)), total: total, limit: limit, offset: offset };
    }
    return {};
  };
})();
"""

BANNER = (
    '<div id="demo-banner" style="position:sticky;top:0;z-index:9999;background:#0b1c2e;'
    "border-bottom:1px solid #274566;color:#cfe0f5;font:13px/1.5 system-ui,Segoe UI,Arial,"
    'sans-serif;padding:8px 16px;text-align:center">'
    '<strong style="color:#2dd4bf">Live demo</strong> — a read-only snapshot of FinOptic’s '
    "real sample output (no backend). "
    '<a href="https://github.com/shanto12/finoptic" '
    'style="color:#5b8def;text-decoration:none;font-weight:600">'
    "Source &amp; run-it-yourself on GitHub →</a></div>"
)

REDIRECTS = "# Serve the generated remediation script for the dashboard download link\n"
REDIRECTS += "/api/v1/export/remediation.sh   /remediation.sh   200\n"


def main() -> None:
    if WEB.exists():
        shutil.rmtree(WEB)
    WEB.mkdir(parents=True)

    with TestClient(app) as c:
        ingest = c.post("/api/v1/ingest/sample").json()
        data = {
            "health": c.get("/healthz").json(),
            "batches": c.get("/api/v1/batches").json(),
            "summary": c.get("/api/v1/summary").json(),
            "findings": c.get("/api/v1/findings", params={"limit": 500}).json(),
            "ingest": ingest,
            "analyze": c.post("/api/v1/analyze", json={}).json(),
        }
        remediation = c.get("/api/v1/export/remediation.sh").text

    (WEB / "demo-data.js").write_text(
        "window.__DEMO_DATA__ = " + json.dumps(data, indent=2) + ";\n", encoding="utf-8"
    )
    (WEB / "demo.js").write_text(DEMO_JS, encoding="utf-8")
    (WEB / "remediation.sh").write_text(remediation, encoding="utf-8")
    (WEB / "_redirects").write_text(REDIRECTS, encoding="utf-8")

    shutil.copy(DASH / "styles.css", WEB / "styles.css")

    app_js = (DASH / "app.js").read_text(encoding="utf-8")
    if API_NEEDLE not in app_js:
        raise SystemExit("api() signature changed — update API_NEEDLE in build_static_demo.py")
    (WEB / "app.js").write_text(app_js.replace(API_NEEDLE, API_INJECT, 1), encoding="utf-8")

    html = (DASH / "index.html").read_text(encoding="utf-8")
    html = html.replace(
        '<script defer src="/app.js"></script>',
        '<script src="/demo-data.js"></script>\n  <script src="/demo.js"></script>\n'
        '  <script defer src="/app.js"></script>',
        1,
    )
    html = html.replace("<body>", "<body>\n  " + BANNER, 1)
    (WEB / "index.html").write_text(html, encoding="utf-8")

    findings = data["findings"]
    print(f"Built static demo -> {WEB}")
    print(f"  findings: {findings.get('total')} | monthly: {data['summary'].get('total_monthly_waste')}")
    print(f"  files: {sorted(p.name for p in WEB.iterdir())}")


if __name__ == "__main__":
    main()
