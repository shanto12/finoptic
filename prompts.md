# `prompts.md` — Vibe Coding Audit Log

**Challenge:** Graduate Vibe Coding Challenge — Project 1: *Cloud Cost Optimizer & Remediation Engine*
**Architect:** Shanto Mathew
**AI engineer / tool (end-to-end):** Claude Code (Claude Opus) — *all* source was authored by the AI agent under
architectural direction; no code was hand-edited, per the challenge rules.
**Method:** Contract-first design + a multi-agent build/review workflow (the agent fanned out parallel sub-agents to
build and to adversarially review the modules against locked interface contracts).

> This is the audit log of the architect-level prompts/directives that drove the build, in order, with a short note on
> what the agent produced and a running elapsed-time marker. It is a faithful record of how the system was constructed.

---

### Prompt 0 — Required initial execution prompt
> **Lead Architect mode: ON.** We are building a Python-based, API-first **Cloud Cost Optimizer & Remediation Engine**
> using a free database and a dashboard.
> **Rules:**
> - **No Manual Edits:** You provide all logic and fixes. I will not edit any code.
> - **Audit Log:** You must maintain a file named `prompts.md`. After every turn, update that file with the prompt I just used.
> - **Time-Check:** Start a timer. Goal is an MVP in 4–6 hours (Max window: 16h). Report 'Elapsed Time' at the end of every response. Acknowledge and let's start.

**Agent:** Acknowledged. Proposed the FinOps angle (Project 1): ingest AWS/Azure billing exports → detect orphaned/idle
resources → generate exact decommission CLI → GenAI executive runbook. Confirmed stack: FastAPI + SQLAlchemy 2 + SQLite,
vanilla-JS dashboard, provider-agnostic LLM layer with deterministic fallback. *Elapsed: 0:05.*

---

### Prompt 1 — Architecture & contract-first foundation
> Before writing any feature code, lock the architecture so the build can parallelize cleanly. Define the data contracts
> first: framework-free domain dataclasses (`NormalizedRecord`, `FindingResult`, `RemediationResult`), the SQLAlchemy ORM
> (`IngestBatch`, `ResourceRecord`, `Finding`), the Pydantic API schemas, settings (env-driven, offline-safe defaults),
> structured JSON logging, and the DB engine. Keep dependencies lean for Python 3.14. These interfaces are frozen — every
> module builds against them.

**Agent:** Wrote `domain.py`, `models.py`, `schemas.py`, `config.py` (incl. LLM provider auto-resolution), `db.py`,
`logging_config.py`, `pyproject.toml`. Verified deps install on 3.14 and the spine imports + bootstraps the DB. *Elapsed: 0:45.*

---

### Prompt 2 — Parallel module build against the locked contracts
> Now build the leaf modules in parallel, each strictly against the frozen contracts and owning a disjoint set of files:
> (1) **ingest** — AWS CUR CSV + Azure billing JSON parsers; (2) **detect** — an 11-rule waste engine
> (unattached EBS/disks, idle EC2/RDS, unassociated EIP/public-IP, idle ELB/NAT, stale snapshots, deallocated VMs) + a
> `summarize()`; (3) **remediate** — exact `aws`/`az` CLI per rule + a DRY-RUN-guarded bash script renderer; (4) **analyze** —
> a provider-agnostic LLM client (Anthropic/OpenAI/Azure) + a FinOps knowledge base + an analyst with a *deterministic
> fallback* when no key is present; (5) **sample data** that exercises every rule plus healthy controls; (6) **dashboard**
> (vanilla JS + Chart.js, no build step); (7) **Python SDK**. Every module must be defensive — one bad row never aborts a batch.

**Agent:** Ran a 7-way parallel sub-agent build. Each sub-agent self-checked (py_compile + focused import) before returning a
manifest. All seven cohered against the contracts on first integration. *Elapsed: 2:10.*

---

### Prompt 3 — Integration layer (service + API + CLI + observability)
> Wire the pipeline together coherently: a `service.py` orchestrating ingest → detect → remediate → persist → summarize/analyze;
> a FastAPI app (`/api/v1` ingest, sample, batches, summary, findings, finding-by-id, analyze, remediation.sh export) with
> API-key auth, rate limiting, CORS, request-id + structured access logging, and Prometheus `/metrics`; serve the dashboard
> at `/`; and a `finoptic` CLI (`scan`, `sample`, `serve`) for an offline demo. API-first, contract-faithful.

**Agent:** Authored `service.py`, `api/{app,routes,deps,middleware}.py`, `observability/metrics.py`, `cli.py`, `__main__.py`.
End-to-end smoke test via TestClient: 32 resources → **17 findings → $1,095.32/mo ($13,143.84/yr)**; all endpoints green;
GenAI analyst correctly fell back to deterministic mode with a real exec summary + runbook. *Elapsed: 3:25.*

---

### Prompt 4 — Test suite + verification gates
> Lock the behavior with a real pytest suite: ingest/detect/remediate/analyze unit tests, an API contract suite via TestClient,
> a **live-server** SDK round-trip, and a CLI smoke test. Assert the exact sample totals so a regression can't slip. Pin the
> LLM path to a mocked transport so the GenAI dispatch+parse is covered without a network. Get ruff clean and report coverage.

**Agent:** 50 tests passing, ruff clean, **86% coverage** (incl. the LLM dispatch path via a mocked httpx transport and the
deterministic-fallback path). *Elapsed: 4:15.*

---

### Prompt 5 — Adversarial review & hardening
> Before calling it done, run an adversarial review. Fan out reviewers across correctness, security, API robustness, GenAI
> safety, and cloud/FinOps domain accuracy; then *verify each finding against the actual source* (default to false-positive)
> and apply only the confirmed fixes. Re-run the gates after.

**Agent:** Multi-agent review → per-finding verification → applied the confirmed fixes (see `CHANGELOG`/git history), tests
still green. *Elapsed: 5:05.*

---

### Prompt 6 — Documentation, deck, and submission package
> Produce the submission artifacts: a polished `README` (quickstart, architecture, API reference), `ARCHITECTURE.md`, this
> `prompts.md`, and an AI-generated presentation deck. Confirm there are no live cloud resources to decommission (the tool
> operates entirely on exported billing data + local SQLite).

**Agent:** Wrote the docs and generated the deck. **Decommission confirmation:** FinOptic provisions *no* cloud
infrastructure — it analyzes uploaded/sample billing exports against a local SQLite file — so there is nothing to tear down
and no cloud spend was incurred. *Elapsed: 5:40.*

---

### Prompt 7 — Ship a live demo + a spreadsheet export
> Make it better and get it in front of a reviewer: add a CSV export of findings (FinOps teams live in spreadsheets),
> and host a clickable live demo. Netlify can't run the FastAPI backend, so publish a static snapshot of the dashboard
> that renders the *real* pipeline output.

**Agent:** Added `GET /api/v1/export/findings.csv` (+ SDK method + test). Wrote `scripts/build_static_demo.py` — it runs
the real pipeline, bakes the genuine output, and serves the **same** dashboard UI client-side via a flag-guarded shim —
then deployed to Netlify and tested the live URL: **https://finoptic-shanto-demo.netlify.app**. 51 tests green. *Elapsed: 6:20.*

---

## Reflection — architect's notes
- **Why contract-first:** freezing the domain/schema interfaces up front let the build fan out into parallel agents with
  zero interface drift — the seven modules integrated on the first run. That is the leverage of "architect, not typist."
- **Why a deterministic fallback for the GenAI layer:** a forward-deployed tool must degrade gracefully at a customer site
  with no model key and must never let an LLM error break the pipeline — so the analyst always returns a useful runbook.
- **Why DRY-RUN by default:** the remediation script is generated, never executed; it prints commands unless the operator
  explicitly sets `DRY_RUN=0`. Safe-by-default is non-negotiable for anything touching production cloud accounts.
- **Human-in-the-loop:** every destructive action is surfaced as a reviewable command with a risk rating and reversibility
  flag — the engine recommends, the human decides.
