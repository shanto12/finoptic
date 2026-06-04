"""Ingest package: billing-export parsers -> ``list[NormalizedRecord]``.

Re-exports the public parsing interface so callers can simply::

    from finoptic.ingest import parse, parse_aws_cur, parse_azure_billing
"""

from __future__ import annotations

from finoptic.ingest.normalize import parse, parse_aws_cur, parse_azure_billing

__all__ = ["parse", "parse_aws_cur", "parse_azure_billing"]
