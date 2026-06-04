"""``python -m finoptic`` -> launch the API server (shortcut for ``finoptic serve``)."""

from __future__ import annotations

from finoptic.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["serve"]))
