"""Billing-export parsers: raw bytes/text -> ``list[NormalizedRecord]``.

This module is the single source of truth for the ingest layer. It auto-detects the
provider of an upload (AWS Cost & Usage Report CSV vs. Azure billing-export JSON) and
normalizes each line item into the framework-free :class:`~finoptic.domain.NormalizedRecord`
contract consumed by the detect stage.

Robustness is the priority: a single malformed row or field must never abort the whole
import. Bad JSON in a per-row column degrades to ``{}``; an unparseable cost degrades to
``0.0``; a row with no resource id is skipped entirely.

Public interface
----------------
``parse(content, filename="")``     -> ``(provider, records)`` with provider auto-detection.
``parse_aws_cur(text)``             -> ``list[NormalizedRecord]`` for AWS CUR CSV.
``parse_azure_billing(data)``       -> ``list[NormalizedRecord]`` for Azure billing JSON.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any

from finoptic.domain import NormalizedRecord
from finoptic.logging_config import get_logger

logger = get_logger(__name__)

__all__ = ["parse", "parse_aws_cur", "parse_azure_billing"]


# --- Low-level coercion helpers ----------------------------------------------


def _to_text(content: bytes | str) -> str:
    """Decode bytes as UTF-8 (replacement on error); pass strings through."""
    if isinstance(content, bytes):
        return content.decode("utf-8", errors="replace")
    return content


def _coerce_float(value: Any) -> float:
    """Best-effort float coercion. Empty/None/garbage -> 0.0."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        try:
            return float(value)
        except (ValueError, OverflowError):
            return 0.0
    text = str(value).strip()
    if not text:
        return 0.0
    try:
        return float(text)
    except (ValueError, TypeError):
        return 0.0


def _coerce_mapping(value: Any) -> dict:
    """Coerce a JSON string / mapping into a ``dict``; anything else -> ``{}``.

    Handles the AWS case where ``resource_tags`` / ``state`` arrive as JSON *strings*
    and the Azure case where ``tags`` / ``properties`` arrive as native objects.
    """
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    text = str(value).strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _clean_str(value: Any) -> str:
    """Coerce to a stripped string; ``None`` -> ``""``."""
    if value is None:
        return ""
    return str(value).strip()


def _opt_str(value: Any) -> str | None:
    """Coerce to a stripped string or ``None`` when empty/missing."""
    text = _clean_str(value)
    return text or None


# --- AWS CUR best-effort type inference --------------------------------------

#: Substrings (matched case-insensitively against product_code + usage_type) mapped to
#: a normalized AWS resource type. Used only when the row's ``resource_type`` is blank.
_AWS_TYPE_HINTS: tuple[tuple[str, str], ...] = (
    ("ebs", "ebs_volume"),
    ("volume", "ebs_volume"),
    ("natgateway", "nat_gateway"),
    ("nat-gateway", "nat_gateway"),
    ("nat gateway", "nat_gateway"),
    ("elasticip", "elastic_ip"),
    ("elastic-ip", "elastic_ip"),
    ("elastic ip", "elastic_ip"),
    ("loadbalancer", "load_balancer"),
    ("load-balancer", "load_balancer"),
    ("elb", "load_balancer"),
    ("snapshot", "snapshot"),
    ("rds", "rds_instance"),
    ("ec2", "ec2_instance"),
)


def _infer_aws_resource_type(product_code: str, usage_type: str) -> str:
    """Best-effort normalized type from ``product_code``/``usage_type`` hints.

    Returns ``""`` when nothing matches so the record still flows through (detectors
    simply won't match an unknown type).
    """
    haystack = f"{product_code} {usage_type}".lower()
    for needle, resource_type in _AWS_TYPE_HINTS:
        if needle in haystack:
            return resource_type
    return ""


# --- AWS Cost & Usage Report (CSV) -------------------------------------------


def parse_aws_cur(text: str) -> list[NormalizedRecord]:
    """Parse AWS CUR-style CSV text into normalized records.

    Expected header columns (extra/missing columns are tolerated)::

        identity_line_item_id, bill_payer_account_id, line_item_usage_account_id,
        product_code, line_item_resource_id, resource_type, product_region,
        line_item_usage_type, line_item_unblended_cost, billing_period_start_date,
        billing_period_end_date, resource_tags, state

    ``resource_tags`` and ``state`` are JSON strings (parsed safely). Rows without a
    resource id are skipped. Per-row failures are logged and skipped, never raised.
    """
    records: list[NormalizedRecord] = []
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        logger.warning("aws_cur_empty_or_headerless")
        return records

    skipped = 0
    for line_no, row in enumerate(reader, start=2):  # line 1 is the header
        try:
            resource_id = _clean_str(row.get("line_item_resource_id"))
            if not resource_id:
                skipped += 1
                continue

            product_code = _clean_str(row.get("product_code"))
            usage_type = _clean_str(row.get("line_item_usage_type"))

            resource_type = _clean_str(row.get("resource_type"))
            if not resource_type:
                resource_type = _infer_aws_resource_type(product_code, usage_type)

            records.append(
                NormalizedRecord(
                    provider="aws",
                    resource_id=resource_id,
                    resource_type=resource_type,
                    service=product_code,
                    account_id=_clean_str(row.get("line_item_usage_account_id")),
                    region=_opt_str(row.get("product_region")),
                    usage_type=usage_type or None,
                    monthly_cost=_coerce_float(row.get("line_item_unblended_cost")),
                    currency="USD",
                    tags=_coerce_mapping(row.get("resource_tags")),
                    state=_coerce_mapping(row.get("state")),
                    period_start=_opt_str(row.get("billing_period_start_date")),
                    period_end=_opt_str(row.get("billing_period_end_date")),
                )
            )
        except Exception:  # noqa: BLE001 - defensive: one bad row must not abort import
            skipped += 1
            logger.warning("aws_cur_row_skipped", extra={"line": line_no})
            continue

    logger.info(
        "aws_cur_parsed",
        extra={"records": len(records), "skipped": skipped},
    )
    return records


# --- Azure billing export (JSON) ---------------------------------------------


def _coerce_azure_rows(data: str | bytes | list) -> list[dict]:
    """Coerce raw Azure billing input into a list of row mappings.

    Accepts an already-parsed ``list``, a JSON array string/bytes, or a single object
    (wrapped into a one-element list). Returns ``[]`` on any decode failure.
    """
    if isinstance(data, list):
        rows = data
    else:
        text = _to_text(data) if isinstance(data, (bytes, str)) else ""
        if not text.strip():
            return []
        try:
            rows = json.loads(text)
        except (ValueError, TypeError):
            logger.warning("azure_billing_invalid_json")
            return []

    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list):
        return []
    return [r for r in rows if isinstance(r, dict)]


def parse_azure_billing(data: str | bytes | list) -> list[NormalizedRecord]:
    """Parse an Azure billing-export JSON array into normalized records.

    Each object is expected to carry::

        subscriptionId, resourceId, resourceType, meterCategory, resourceLocation,
        costInUsd, currency, tags (object), billingPeriodStartDate,
        billingPeriodEndDate, properties (object = state signals)

    Objects without a ``resourceId`` are skipped. Per-object failures are logged and
    skipped, never raised.
    """
    records: list[NormalizedRecord] = []
    rows = _coerce_azure_rows(data)

    skipped = 0
    for idx, row in enumerate(rows):
        try:
            resource_id = _clean_str(row.get("resourceId"))
            if not resource_id:
                skipped += 1
                continue

            currency = _clean_str(row.get("currency")) or "USD"

            records.append(
                NormalizedRecord(
                    provider="azure",
                    resource_id=resource_id,
                    resource_type=_clean_str(row.get("resourceType")),
                    service=_clean_str(row.get("meterCategory")),
                    account_id=_clean_str(row.get("subscriptionId")),
                    region=_opt_str(row.get("resourceLocation")),
                    usage_type=None,
                    monthly_cost=_coerce_float(row.get("costInUsd")),
                    currency=currency,
                    tags=_coerce_mapping(row.get("tags")),
                    state=_coerce_mapping(row.get("properties")),
                    period_start=_opt_str(row.get("billingPeriodStartDate")),
                    period_end=_opt_str(row.get("billingPeriodEndDate")),
                )
            )
        except Exception:  # noqa: BLE001 - defensive: one bad object must not abort import
            skipped += 1
            logger.warning("azure_billing_row_skipped", extra={"index": idx})
            continue

    logger.info(
        "azure_billing_parsed",
        extra={"records": len(records), "skipped": skipped},
    )
    return records


# --- Auto-detecting entry point ----------------------------------------------


def _looks_like_json(text: str, filename: str) -> bool:
    """Heuristic provider detection: JSON (=> Azure) vs. CSV (=> AWS).

    A ``.json`` filename forces JSON. Otherwise the first non-whitespace byte decides:
    ``[`` or ``{`` => JSON.
    """
    if filename.lower().endswith(".json"):
        return True
    stripped = text.lstrip()
    return bool(stripped) and stripped[0] in "[{"


def parse(content: bytes | str, filename: str = "") -> tuple[str, list[NormalizedRecord]]:
    """Auto-detect the provider and parse ``content`` into normalized records.

    Detection: JSON input (starts with ``[``/``{`` or ``filename`` ends with ``.json``)
    is treated as Azure billing; anything else is treated as an AWS CUR CSV. ``bytes`` are
    decoded as UTF-8.

    Returns a ``(provider, records)`` tuple where ``provider`` is ``"azure"`` or ``"aws"``.
    """
    text = _to_text(content)
    if _looks_like_json(text, filename):
        return "azure", parse_azure_billing(text)
    return "aws", parse_aws_cur(text)
