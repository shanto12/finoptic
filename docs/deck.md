# FinOptic — Presentation Deck

> Cloud Cost Optimizer & Remediation Engine · GenAI Engineer (FDE) Challenge — Project 1
> Shanto Mathew · Built end-to-end via AI (Claude Code) under architectural direction
> (A polished `.pptx` of this deck is in `docs/FinOptic_Deck.pptx`.)

---

## Slide 1 — Title

# FinOptic
### Cloud Cost Optimizer & Remediation Engine
**Find wasted cloud spend → price it → generate the exact CLI to reclaim it → explain it with GenAI.**

`$13,143.84 / year` recoverable on the sample bill · API-first · Safe by default · Runs fully offline

---

## Slide 2 — The problem

**Cloud bills quietly bleed money.** Industry surveys put wasted cloud spend at **~30%**.

The waste is already in the billing export — teams just lack a fast, safe way to find, price, and act on it:

- 🗄️ **Orphaned storage** — unattached EBS/managed disks, stale snapshots
- 💤 **Idle compute** — running EC2 at ~1% CPU, deallocated VMs still billing for disks
- 🌐 **Dangling network** — unassociated Elastic IPs / public IPs, idle NAT gateways & load balancers
- 🗃️ **Idle databases** — RDS instances with zero connections

---

## Slide 3 — The solution: a closed loop

| 1 · Ingest | 2 · Detect | 3 · Remediate | 4 · Explain |
| ---------- | ---------- | ------------- | ----------- |
| AWS CUR CSV + Azure billing JSON → normalized model | 11-rule waste engine, priced & ranked | Exact `aws`/`az` CLI + DRY-RUN-guarded script | GenAI executive summary + prioritized runbook |

**API-first** — the dashboard, Python SDK, and CLI are all clients of one FastAPI contract.

---

## Slide 4 — Architecture

```
Ingest (AWS/Azure parsers) ─► Detect (11 rules) ─► Remediate (CLI gen) ─► SQLite/Postgres
                                                                              │
   Dashboard ◄─┐                                                             ▼
   Python SDK ◄─┼──────────────  FastAPI  /api/v1  ───────────────  GenAI Analyst
   CLI        ◄─┘                  + /metrics /healthz              (LLM + deterministic fallback)
```

- Pipeline stages are **decoupled by framework-free domain dataclasses** → built & tested in parallel.
- Synchronous SQLAlchemy in a threadpool → simple data layer, one-line **SQLite → Postgres** swap.

---

## Slide 5 — Detection engine (11 rules)

| AWS | Azure |
| --- | ----- |
| Unattached EBS volume · Idle EC2 · Unassociated Elastic IP | Unattached managed disk · Unassociated public IP |
| Idle RDS · Idle load balancer · Idle NAT gateway · Stale snapshot | Deallocated VM · Stale disk snapshot |

Each rule is a pure, **explainable** function — every finding records the exact signals that triggered it,
a severity, a confidence, and a cost. Adding a rule = adding one function.

---

## Slide 6 — Results (bundled sample)

# $1,095.32 / month  →  $13,143.84 / year

**32 resources scanned · 17 findings · 0 false positives on healthy controls**

| By severity | By provider | By category (top) |
| ----------- | ----------- | ----------------- |
| high $762.56 · medium $295.51 · low $37.25 | AWS $753.79 · Azure $341.53 | idle_compute $446 · idle_database $412 · orphaned_storage $141 |

---

## Slide 7 — GenAI analyst

**An LLM turns findings into an executive summary + a prioritized runbook — grounded, bounded, and safe.**

- **Provider-agnostic** over `httpx` — Anthropic / OpenAI / Azure OpenAI, no vendor SDKs.
- **RAG-style grounding** — a curated FinOps knowledge base is retrieved by category and injected into the prompt.
- **Guardrails** — bounded prompt size, strict timeout, tolerant parsing.
- **Graceful degradation** — *any* LLM error → a high-quality **deterministic** runbook. The pipeline can never break.

---

## Slide 8 — Safety & remediation

**The engine recommends; the human disposes.**

- Every finding ships the exact `aws`/`az` command, a **risk rating**, and a **reversible** flag.
- The exported bash script is **safe by default**: `DRY_RUN=1` prints commands; only `DRY_RUN=0` executes.
- Destructive actions **snapshot before delete**; all arguments are `shlex`-quoted (no shell injection).
- **No cloud infrastructure is provisioned** — nothing to decommission, no spend incurred.

---

## Slide 9 — Engineering quality

| API-first | Tested | Observable | Shippable |
| --------- | ------ | ---------- | --------- |
| FastAPI, OpenAPI, auth, rate-limit | **50 tests · 86% coverage** · ruff · CI (3.11/3.12) | JSON logs + request-id, Prometheus `/metrics` | Docker, Makefile, Python SDK, CLI |

Built with a **multi-agent workflow**: contract-first design → 7 parallel module builders → adversarial review.

---

## Slide 10 — Role fit & roadmap

**Maps directly to the GenAI Engineer (FDE) role:** Python · FastAPI · GenAI (LLM + RAG + guardrails) · SDK design ·
cloud/FinOps domain · CI/CD · observability · safe, ship-fast, ownership-driven execution.

**Next:** Postgres + Alembic · right-sizing & commitment-coverage rules · analyst eval harness · OAuth2/multi-tenant ·
publish SDK to PyPI + IaC for the service.

---

## Slide 11 — Thank you

**FinOptic** — turn the cloud bill into an executable savings plan.

GitHub: `github.com/shanto12/finoptic` · Shanto Mathew
