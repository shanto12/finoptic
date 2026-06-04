"""AWS Cost & Usage Report (CUR) ingest entry point.

Thin provider-scoped facade over :mod:`finoptic.ingest.normalize`, which holds the
actual CSV parsing logic. Kept separate so callers can address the AWS path explicitly
(``from finoptic.ingest import aws_cur``) without importing the Azure code path.
"""

from __future__ import annotations

from finoptic.domain import NormalizedRecord
from finoptic.ingest.normalize import parse_aws_cur

__all__ = ["parse_aws_cur", "parse"]


def parse(content: bytes | str) -> list[NormalizedRecord]:
    """Parse AWS CUR CSV ``content`` (bytes or text) into normalized records."""
    text = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else content
    return parse_aws_cur(text)
