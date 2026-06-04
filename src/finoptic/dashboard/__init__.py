"""Static single-page dashboard assets for FinOptic.

This package contains only static files (``index.html``, ``app.js``, ``styles.css``)
served by the FastAPI app at ``/``. It intentionally has no importable Python API; it
exists so the dashboard directory is a proper package and its assets can be located via
``importlib.resources`` / ``Path(__file__).parent`` by the API layer.
"""

from __future__ import annotations

from pathlib import Path

#: Absolute path to the directory holding the static dashboard assets.
DASHBOARD_DIR: Path = Path(__file__).resolve().parent

#: Convenience handle to the SPA entry point.
INDEX_HTML: Path = DASHBOARD_DIR / "index.html"

__all__ = ["DASHBOARD_DIR", "INDEX_HTML"]
