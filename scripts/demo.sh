#!/usr/bin/env bash
# FinOptic API demo — boots nothing; assumes the server is running at $BASE.
# Usage:  ./scripts/demo.sh            (defaults to http://localhost:8000)
#         BASE=http://host:port ./scripts/demo.sh
set -euo pipefail

BASE="${BASE:-http://localhost:8000}"
have_jq() { command -v jq >/dev/null 2>&1; }
pretty() { if have_jq; then jq "${1:-.}"; else cat; fi; }

echo "==> Health"
curl -fsS "$BASE/healthz" | pretty

echo "==> Ingesting bundled sample data"
curl -fsS -X POST "$BASE/api/v1/ingest/sample" | pretty

echo "==> Waste summary (totals + breakdowns)"
curl -fsS "$BASE/api/v1/summary" | pretty '{monthly: .total_monthly_waste, annual: .total_annual_savings, findings: .finding_count, by_severity, by_category}'

echo "==> Top 3 findings"
curl -fsS "$BASE/api/v1/findings?limit=3" | pretty '.items[] | {severity, resource_id, monthly_cost, fix: .remediation.cli_commands[0]}'

echo "==> GenAI FinOps analysis (deterministic fallback unless an LLM key is set)"
curl -fsS -X POST "$BASE/api/v1/analyze" -H 'content-type: application/json' -d '{}' | pretty '{mode, provider, executive_summary, steps: (.prioritized_runbook | length)}'

echo "==> Remediation script (first lines)"
curl -fsS "$BASE/api/v1/export/remediation.sh" | head -20

echo
echo "Done. Open $BASE/ for the dashboard."
