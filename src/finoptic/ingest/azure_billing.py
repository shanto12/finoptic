"""Azure billing-export ingest entry point.

Thin provider-scoped facade over :mod:`finoptic.ingest.normalize`, which holds the
actual JSON parsing logic. Kept separate so callers can address the Azure path
explicitly (``from finoptic.ingest import azure_billing``).
"""

from __future__ import annotations

from finoptic.domain import NormalizedRecord
from finoptic.ingest.normalize import parse_azure_billing

__all__ = ["parse_azure_billing", "parse"]


def parse(content: str | bytes | list) -> list[NormalizedRecord]:
    """Parse Azure billing-export JSON ``content`` into normalized records."""
    return parse_azure_billing(content)
