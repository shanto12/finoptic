"""The detection engine: apply the rule set to records and aggregate findings.

``detect`` is the stage entry point (``list[NormalizedRecord] -> list[FindingResult]``);
``summarize`` rolls a finding set up into the cost breakdowns the dashboard/API expose.

Robustness is a hard requirement here: a single malformed record or a rule raising on
unexpected data must never abort the batch. Each rule application is isolated and logged.
"""

from __future__ import annotations

from collections.abc import Iterable

from finoptic.detect.rules import RULES
from finoptic.domain import SEVERITY_RANK, FindingResult, NormalizedRecord
from finoptic.logging_config import get_logger

logger = get_logger(__name__)


def detect(records: Iterable[NormalizedRecord]) -> list[FindingResult]:
    """Run every rule against every record and return prioritized findings.

    A record may match at most one rule by construction, but the engine does not rely
    on that — it collects all matches. Results are sorted by severity (most urgent
    first) and then by monthly cost (most expensive first). Any record or rule that
    raises is skipped and logged so one bad row cannot fail the whole batch.
    """
    findings: list[FindingResult] = []

    for record in records:
        if not isinstance(record, NormalizedRecord):
            logger.warning("detect.skip_non_record", extra={"value_type": type(record).__name__})
            continue
        for rule in RULES:
            try:
                result = rule(record)
            except Exception:  # one bad field must not abort the batch
                logger.exception(
                    "detect.rule_error",
                    extra={
                        "rule": getattr(rule, "__name__", repr(rule)),
                        "resource_id": getattr(record, "resource_id", None),
                    },
                )
                continue
            if result is not None:
                findings.append(result)

    findings.sort(
        key=lambda f: (SEVERITY_RANK.get(f.severity, 0), f.monthly_cost),
        reverse=True,
    )
    return findings


def summarize(findings: list[FindingResult], resource_count: int = 0) -> dict:
    """Aggregate findings into cost rollups keyed by severity/category/provider/type.

    Returns a plain dict with total monthly waste, total annual savings, counts, and
    per-dimension cost breakdowns (every encountered key is included, costs rounded to
    2 decimal places).
    """
    by_severity: dict[str, float] = {}
    by_category: dict[str, float] = {}
    by_provider: dict[str, float] = {}
    by_resource_type: dict[str, float] = {}
    total_monthly = 0.0

    for finding in findings:
        cost = float(finding.monthly_cost or 0.0)
        total_monthly += cost
        by_severity[finding.severity] = by_severity.get(finding.severity, 0.0) + cost
        by_category[finding.category] = by_category.get(finding.category, 0.0) + cost
        by_provider[finding.provider] = by_provider.get(finding.provider, 0.0) + cost
        by_resource_type[finding.resource_type] = (
            by_resource_type.get(finding.resource_type, 0.0) + cost
        )

    total_monthly = round(total_monthly, 2)
    return {
        "total_monthly_waste": total_monthly,
        "total_annual_savings": round(total_monthly * 12.0, 2),
        "finding_count": len(findings),
        "resource_count": resource_count,
        "by_severity": {k: round(v, 2) for k, v in by_severity.items()},
        "by_category": {k: round(v, 2) for k, v in by_category.items()},
        "by_provider": {k: round(v, 2) for k, v in by_provider.items()},
        "by_resource_type": {k: round(v, 2) for k, v in by_resource_type.items()},
    }
