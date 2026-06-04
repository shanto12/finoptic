"""GenAI FinOps analyst with a deterministic fallback.

``analyze()`` turns a set of detected findings plus a precomputed summary into an
executive summary and a prioritized remediation runbook. When an LLM is configured it
builds a grounded prompt (summary + top findings + retrieved best-practice snippets) and
asks the model for prose; otherwise — or on ANY failure — it produces an equally usable
deterministic report from aggregates. The pipeline never crashes on bad data or a bad model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from finoptic.domain import SEVERITY_RANK, FindingResult
from finoptic.logging_config import get_logger

from .knowledge_base import retrieve
from .llm import LLMClient

logger = get_logger(__name__)

#: Hard cap on findings serialized into the prompt, to bound token usage.
_MAX_PROMPT_FINDINGS = 20

_SYSTEM_PROMPT = (
    "You are a senior FinOps cloud cost analyst. You receive a structured summary of "
    "detected cloud waste plus the highest-cost individual findings and a list of "
    "vetted best-practice snippets. Using ONLY the data provided (never invent "
    "resources, numbers, or services), write:\n"
    "1) An 'Executive Summary' paragraph: total monthly and annual savings, the "
    "biggest waste category, and the overall risk posture in plain business language.\n"
    "2) A 'Runbook' section: a numbered list of prioritized, concrete remediation "
    "actions, highest financial impact and highest severity first. Each step must name "
    "the resource type / category and the action, and reflect the best-practice "
    "guidance. Keep it tight and actionable.\n"
    "Output format exactly:\n"
    "Executive Summary:\n<paragraph>\n\nRunbook:\n1. <action>\n2. <action>\n..."
)


@dataclass
class AnalyzeResult:
    """Structured output of the analyst stage."""

    mode: str  # "llm" | "deterministic"
    provider: str
    executive_summary: str
    prioritized_runbook: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "provider": self.provider,
            "executive_summary": self.executive_summary,
            "prioritized_runbook": list(self.prioritized_runbook),
        }


# --- public entry point ------------------------------------------------------


def analyze(
    findings: list[FindingResult],
    summary: dict,
    client: LLMClient | None = None,
) -> AnalyzeResult:
    """Produce an executive summary + prioritized runbook for ``findings``.

    Uses the configured LLM when available and falls back to a deterministic report on
    a disabled client, a ``None`` completion, empty parse, or any unexpected error.
    """
    findings = [f for f in (findings or []) if isinstance(f, FindingResult)]
    summary = summary if isinstance(summary, dict) else {}

    if client is None:
        try:
            client = LLMClient.from_settings()
        except Exception as exc:  # noqa: BLE001 - settings/client must never crash us
            logger.warning("analyze.client_init_failed", extra={"error": type(exc).__name__})
            client = None

    if client is not None and client.enabled:
        try:
            result = _analyze_with_llm(findings, summary, client)
            if result is not None:
                return result
        except Exception as exc:  # noqa: BLE001 - fall back on anything
            logger.warning("analyze.llm_path_failed", extra={"error": type(exc).__name__})

    return _deterministic(findings, summary)


# --- LLM path ----------------------------------------------------------------


def _analyze_with_llm(
    findings: list[FindingResult], summary: dict, client: LLMClient
) -> AnalyzeResult | None:
    """Attempt the grounded LLM path. Returns ``None`` to signal fallback."""
    top = _top_findings(findings, _MAX_PROMPT_FINDINGS)
    categories = _categories_present(findings, summary)
    snippets = retrieve(categories)

    user_prompt = _build_user_prompt(summary, top, snippets)
    text = client.complete(_SYSTEM_PROMPT, user_prompt)
    if not text:
        return None

    exec_summary, runbook = _parse_completion(text)
    if not exec_summary and not runbook:
        return None
    if not exec_summary:
        # Model gave steps but no summary prose; synthesize a one-liner deterministically.
        exec_summary = _headline(summary, findings)

    return AnalyzeResult(
        mode="llm",
        provider=client.provider,
        executive_summary=exec_summary,
        prioritized_runbook=runbook,
    )


_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def _safe(value: object, limit: int = 160) -> str:
    """Neutralize untrusted, billing-derived text before it enters the LLM prompt.

    Collapses control chars/newlines, strips leading markdown/header/list markers (so a
    crafted resource id/tag can't spoof the trusted 'best practices' section), collapses
    whitespace, and clips length to bound prompt size regardless of input.
    """
    s = _str(value)
    s = _CONTROL_RE.sub(" ", s)
    s = re.sub(r"^[\s>#*`+\-]+", "", s)
    s = " ".join(s.split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _build_user_prompt(summary: dict, top: list[FindingResult], snippets: list[str]) -> str:
    """Assemble the grounded user message from summary, findings, and KB snippets."""
    lines: list[str] = []
    lines.append("# Portfolio summary")
    lines.append(_format_summary(summary, top))
    lines.append("")
    lines.append(f"# Top {len(top)} findings by monthly cost")
    if top:
        for i, f in enumerate(top, start=1):
            lines.append(
                f"{i}. [{_sev(f)}] {_money(_cost(f))}/mo "
                f"({_money(_annual(f))}/yr) | {_safe(f.category)} | "
                f"{_safe(f.resource_type)} {_safe(f.resource_id)} | "
                f"{_safe(f.provider)}/{_safe(f.region) or 'global'} | "
                f"rule={_safe(f.rule_id)} | {_safe(f.title)}"
            )
    else:
        lines.append("(none)")
    lines.append("")
    lines.append("# Vetted FinOps best practices (ground your runbook in these)")
    if snippets:
        for s in snippets:
            lines.append(f"- {s}")
    else:
        lines.append("- Prefer reversible remediations (snapshot before delete).")
    lines.append("")
    lines.append("Write the Executive Summary and Runbook now, following the required format.")
    return "\n".join(lines)


# Block headers tolerate markdown emphasis: "Runbook", "**Runbook:**", "## Runbook".
_RUNBOOK_HEADER = re.compile(r"^\s*(?:#+\s*)?(?:\*\*|__)?runbook:?(?:\*\*|__)?\s*$", re.IGNORECASE)
_EXEC_HEADER = re.compile(
    r"^\s*(?:#+\s*)?(?:\*\*|__)?executive\s+summary:?(?:\*\*|__)?\s*$", re.IGNORECASE
)
_NUMBERED = re.compile(r"^\s*(?:\d+[.)]|[-*])\s+(.*\S)\s*$")


def _parse_completion(text: str) -> tuple[str, list[str]]:
    """Split a completion into (executive_summary, runbook_steps).

    Tolerant of header variations and inline headers; falls back to treating any
    numbered/bulleted lines as the runbook and the preceding prose as the summary.
    """
    lines = text.replace("\r\n", "\n").split("\n")

    exec_lines: list[str] = []
    runbook: list[str] = []
    section = "pre"  # pre -> exec -> runbook

    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()

        # Inline "Executive Summary: <text>" on one line.
        m_exec_inline = re.match(
            r"^\s*#*\s*executive\s+summary\s*:\s*(.*\S)\s*$", line, re.IGNORECASE
        )
        m_run_inline = re.match(r"^\s*#*\s*runbook\s*:\s*(.*\S)\s*$", line, re.IGNORECASE)

        if _EXEC_HEADER.match(line) or m_exec_inline:
            section = "exec"
            if m_exec_inline:
                exec_lines.append(m_exec_inline.group(1).strip())
            continue
        if _RUNBOOK_HEADER.match(line) or m_run_inline:
            section = "runbook"
            if m_run_inline:
                item = m_run_inline.group(1).strip()
                if item:
                    runbook.append(item)
            continue

        if section == "runbook":
            item = _clean_step(stripped)
            if item:
                runbook.append(item)
        elif section == "exec":
            if stripped:
                exec_lines.append(stripped)
        else:  # pre-header content: numbered => runbook, prose => exec
            num = _NUMBERED.match(line)
            if num:
                section = "runbook"
                item = num.group(1).strip()
                if item:
                    runbook.append(item)
            elif stripped:
                exec_lines.append(stripped)

    exec_summary = " ".join(exec_lines).strip()
    runbook = [s for s in (s.strip() for s in runbook) if s]
    return exec_summary, runbook


def _clean_step(text: str) -> str:
    """Strip a leading list marker (``1.`` / ``2)`` / ``-`` / ``*``) from a step."""
    if not text:
        return ""
    m = _NUMBERED.match(text)
    if m:
        return m.group(1).strip()
    return text.strip()


# --- deterministic fallback --------------------------------------------------


def _deterministic(findings: list[FindingResult], summary: dict) -> AnalyzeResult:
    """Build a strong report purely from aggregates and per-finding data."""
    total_monthly, total_annual, count = _totals(findings, summary)
    cat_counts, cat_cost = _by_category(findings)
    sev_counts = _by_severity(findings)
    top_category = _top_key(cat_cost) or _top_key(cat_counts)
    top_severity = _top_severity(sev_counts)

    # Executive summary -----------------------------------------------------
    if count == 0:
        exec_summary = (
            "No cloud waste was detected in the analyzed billing and resource data. "
            "There are no remediation actions to prioritize at this time; continue "
            "tagging resources with owners and TTLs to keep it that way."
        )
        return AnalyzeResult(
            mode="deterministic",
            provider="none",
            executive_summary=exec_summary,
            prioritized_runbook=[],
        )

    parts: list[str] = []
    parts.append(
        f"Analysis identified {count} waste finding"
        f"{'s' if count != 1 else ''} totaling {_money(total_monthly)} per month "
        f"({_money(total_annual)} annualized) in recoverable cloud spend."
    )
    if top_category:
        parts.append(
            f"The largest opportunity is {_humanize(top_category)} at "
            f"{_money(cat_cost.get(top_category, 0.0))}/mo across "
            f"{cat_counts.get(top_category, 0)} resource"
            f"{'s' if cat_counts.get(top_category, 0) != 1 else ''}."
        )
    crit = sev_counts.get("critical", 0)
    high = sev_counts.get("high", 0)
    if crit or high:
        parts.append(
            f"Risk posture is led by {crit} critical and {high} high-severity "
            f"finding{'s' if (crit + high) != 1 else ''}; address these first."
        )
    elif top_severity:
        parts.append(
            f"All findings are {top_severity}-severity or below, so remediation can "
            f"proceed in a controlled, reversible manner."
        )
    exec_summary = " ".join(parts)

    # Prioritized runbook ---------------------------------------------------
    runbook = _build_deterministic_runbook(findings, cat_cost)

    return AnalyzeResult(
        mode="deterministic",
        provider="none",
        executive_summary=exec_summary,
        prioritized_runbook=runbook,
    )


def _build_deterministic_runbook(
    findings: list[FindingResult], cat_cost: dict[str, float]
) -> list[str]:
    """Highest-severity/highest-cost findings first, each citing a best practice."""
    ranked = sorted(
        findings,
        key=lambda f: (SEVERITY_RANK.get(_sev(f), 0), _cost(f)),
        reverse=True,
    )

    # Pre-resolve one best-practice snippet per category for citation.
    tip_by_category: dict[str, str] = {}
    for cat in {_str(f.category) for f in findings}:
        tips = retrieve([cat])
        if tips:
            tip_by_category[cat] = tips[0]

    steps: list[str] = []
    for f in ranked[:_MAX_PROMPT_FINDINGS]:
        cat = _str(f.category)
        where = f"{_str(f.provider)}/{_str(f.region) or 'global'}"
        action = _action_for(f)
        line = (
            f"[{_sev(f)} | {_money(_cost(f))}/mo] {action} "
            f"{_str(f.resource_type)} {_str(f.resource_id)} ({where})."
        )
        tip = tip_by_category.get(cat)
        if tip:
            line += f" Best practice: {tip}"
        steps.append(line)

    remaining = len(findings) - len(steps)
    if remaining > 0:
        tail_cost = sum(_cost(f) for f in ranked[_MAX_PROMPT_FINDINGS:])
        steps.append(
            f"Then sweep the remaining {remaining} lower-impact finding"
            f"{'s' if remaining != 1 else ''} ({_money(tail_cost)}/mo) by category, "
            f"applying the same reversible remediations."
        )
    return steps


def _action_for(f: FindingResult) -> str:
    """A short imperative verb phrase appropriate to the finding's category."""
    category = _str(f.category)
    return {
        "orphaned_storage": "Snapshot then delete orphaned",
        "idle_compute": "Rightsize or terminate idle",
        "unassociated_network": "Release unassociated",
        "idle_network": "Delete or consolidate idle",
        "idle_database": "Snapshot then stop idle",
        "stale_snapshot": "Verify retention then delete stale",
    }.get(category, "Review and remediate")


# --- aggregation helpers -----------------------------------------------------


def _top_findings(findings: list[FindingResult], n: int) -> list[FindingResult]:
    return sorted(findings, key=_cost, reverse=True)[: max(0, n)]


def _categories_present(findings: list[FindingResult], summary: dict) -> list[str]:
    cats: list[str] = []
    seen: set[str] = set()
    for f in findings:
        c = _str(f.category)
        if c and c not in seen:
            seen.add(c)
            cats.append(c)
    # Also honor any categories surfaced only in the summary breakdown.
    for c in _summary_categories(summary):
        if c and c not in seen:
            seen.add(c)
            cats.append(c)
    return cats


def _summary_categories(summary: dict) -> list[str]:
    """Best-effort extraction of category keys from a heterogeneous summary dict."""
    out: list[str] = []
    for key in ("by_category", "categories", "category_breakdown"):
        block = summary.get(key)
        if isinstance(block, dict):
            out.extend(str(k) for k in block)
        elif isinstance(block, list):
            for item in block:
                if isinstance(item, dict):
                    name = item.get("category") or item.get("name")
                    if name:
                        out.append(str(name))
    return out


def _totals(findings: list[FindingResult], summary: dict) -> tuple[float, float, int]:
    """Prefer explicit summary totals; fall back to summing findings."""
    # Only honor savings/waste-named keys — never treat gross spend as recoverable savings.
    monthly = _num(
        summary.get("total_monthly_waste")
        or summary.get("total_monthly_savings")
        or summary.get("monthly_savings")
    )
    annual = _num(summary.get("total_annual_savings") or summary.get("annual_savings"))
    count = summary.get("finding_count") or summary.get("total_findings") or summary.get("count")

    if monthly <= 0.0:
        monthly = round(sum(_cost(f) for f in findings), 2)
    if annual <= 0.0:
        annual = round(monthly * 12.0, 2)
    if not isinstance(count, int) or count < 0:
        count = len(findings)
    return monthly, annual, count


def _by_category(findings: list[FindingResult]) -> tuple[dict[str, int], dict[str, float]]:
    counts: dict[str, int] = {}
    cost: dict[str, float] = {}
    for f in findings:
        c = _str(f.category) or "uncategorized"
        counts[c] = counts.get(c, 0) + 1
        cost[c] = round(cost.get(c, 0.0) + _cost(f), 2)
    return counts, cost


def _by_severity(findings: list[FindingResult]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for f in findings:
        s = _sev(f)
        counts[s] = counts.get(s, 0) + 1
    return counts


def _top_key(d: dict[str, float | int]) -> str | None:
    if not d:
        return None
    return max(d.items(), key=lambda kv: kv[1])[0]


def _top_severity(sev_counts: dict[str, int]) -> str | None:
    present = [s for s in sev_counts if sev_counts.get(s, 0) > 0]
    if not present:
        return None
    return max(present, key=lambda s: SEVERITY_RANK.get(s, 0))


def _headline(summary: dict, findings: list[FindingResult]) -> str:
    monthly, annual, count = _totals(findings, summary)
    return (
        f"{count} waste finding{'s' if count != 1 else ''} totaling "
        f"{_money(monthly)}/mo ({_money(annual)}/yr) in recoverable cloud spend."
    )


def _format_summary(summary: dict, top: list[FindingResult]) -> str:
    monthly, annual, count = _totals(summary=summary, findings=top)
    counts, cost = _by_category(top)
    sev = _by_severity(top)
    bits = [
        f"total_findings={count}",
        f"total_monthly_savings={_money(monthly)}",
        f"total_annual_savings={_money(annual)}",
    ]
    if cost:
        breakdown = ", ".join(
            f"{k}={_money(v)}/mo(n={counts.get(k, 0)})"
            for k, v in sorted(cost.items(), key=lambda kv: kv[1], reverse=True)
        )
        bits.append(f"by_category[{breakdown}]")
    if sev:
        sev_str = ", ".join(
            f"{k}={sev[k]}" for k in ("critical", "high", "medium", "low") if sev.get(k)
        )
        if sev_str:
            bits.append(f"by_severity[{sev_str}]")
    return "; ".join(bits)


# --- tiny safe accessors / formatters ----------------------------------------


def _cost(f: FindingResult) -> float:
    return _num(getattr(f, "monthly_cost", 0.0))


def _annual(f: FindingResult) -> float:
    try:
        return _num(f.annual_savings)
    except Exception:  # noqa: BLE001
        return round(_cost(f) * 12.0, 2)


def _sev(f: FindingResult) -> str:
    s = getattr(f, "severity", "") or ""
    s = str(s).lower().strip()
    return s if s in SEVERITY_RANK else "low"


def _humanize(category: str) -> str:
    return str(category).replace("_", " ")


def _num(value: Any) -> float:
    try:
        if value is None:
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _money(value: float) -> str:
    return f"${_num(value):,.2f}"


def _str(value: Any) -> str:
    if value is None:
        return ""
    return str(value)
