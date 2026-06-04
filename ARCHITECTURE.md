# FinOptic — Architecture

## 1. Goals & non-goals

**Goals**
- Turn a raw cloud billing export into a **prioritized, costed, executable** remediation plan.
- **API-first**: every capability is an HTTP endpoint; the dashboard, SDK, and CLI are all clients of the same contract.
- **Safe by default**: nothing destructive is ever executed; remediations are surfaced for human approval.
- **Runs anywhere with zero config**: offline SQLite + deterministic analysis out of the box; opt into Postgres / LLMs.

**Non-goals (for the MVP)**
- Live cloud API polling (FinOptic consumes *exported* billing data — the realistic FinOps integration point).
- Auto-executing remediations (intentionally out of scope; the engine recommends, a human disposes).
- Multi-tenant auth/RBAC (a single API-key gate is provided; full IAM is a roadmap item).

## 2. Component view

```
                        ┌──────────────────────────────────────────────┐
                        │                  FastAPI app                  │
  AWS CUR / Azure  ──►  │  routes → service → pipeline → SQLAlchemy     │  ──► SQLite / Postgres
  billing export        │                         │                     │
                        │     dashboard (SPA)  ◄───┤                     │
                        │     Python SDK       ◄───┤   GenAI analyst ◄───┼──► LLM (opt) / deterministic
                        │     /metrics /healthz ◄──┘                     │
                        └──────────────────────────────────────────────┘
```

Layers (strict dependency direction, top → bottom):

| Layer | Modules | Responsibility |
| ----- | ------- | -------------- |
| Interface | `api/`, `cli.py`, `sdk/`, `dashboard/` | Transport + presentation |
| Orchestration | `service.py` | Wire pipeline ↔ persistence ↔ schemas |
| Pipeline (pure) | `ingest/`, `detect/`, `remediate/`, `analyze/` | Business logic on domain types |
| Contract | `domain.py`, `schemas.py`, `models.py` | The frozen interfaces |
| Platform | `config.py`, `db.py`, `logging_config.py`, `observability/` | Cross-cutting concerns |

The **pipeline modules never import each other** — they communicate only through the dataclasses in `domain.py`. This is
the key decision that allowed the modules to be built and tested in parallel and that keeps each independently replaceable.

## 3. Data model

**Domain (in-memory, framework-free)** — `NormalizedRecord` → `FindingResult` → `RemediationResult`.

**Persistence (SQLAlchemy 2.0)** — three tables:

- `ingest_batches` — one row per upload (provider, counts, total waste, timestamp).
- `resource_records` — normalized billing line items, with a JSON `state` column holding the detection signals an
  enrichment pipeline joins from AWS Config / Azure Resource Graph (attachment state, CPU, connections, age, …).
- `findings` — detected waste with the attached remediation (JSON), severity, confidence, and costs.

Synchronous SQLAlchemy is used deliberately: FastAPI runs sync path operations in a threadpool, which keeps the data layer
simple, avoids the `greenlet` dependency, and makes the SQLite→Postgres swap a pure config change (`FINOPTIC_DATABASE_URL`).

## 4. Pipeline

```
parse(content) ─► [NormalizedRecord]            # ingest: provider auto-detect, defensive coercion
      └─► detect([records]) ─► [FindingResult]   # apply 11 rules, sort by (severity, cost)
            └─► attach_remediations(findings)     # remediate: exact aws/az CLI per rule_id
                  └─► persist(batch, records, findings)   # service + SQLAlchemy
                        └─► summarize / analyze           # aggregates + GenAI runbook
```

Every stage is **total**: a malformed row, a missing signal, or an unknown resource type is skipped (and logged), never
fatal. This matters for a forward-deployed tool pointed at messy real-world exports.

### Detection engine
Each rule is a pure callable `(NormalizedRecord) -> FindingResult | None` registered in a list. The engine applies all
rules, collects matches, and sorts by `(SEVERITY_RANK, monthly_cost)`. Adding a rule = adding one function — no engine
changes. Rules are intentionally **explainable**: each finding's `evidence` records the exact signals that triggered it.

### Remediation & safety model
`build_remediation(finding)` maps each `rule_id` to the precise `aws`/`az` command(s), filling resource id + region.
Destructive actions snapshot-before-delete where possible and carry `risk` + `reversible` flags. `render_script()` emits a
bash script that is **safe by default**: a `DRY_RUN` guard echoes every command unless the operator sets `DRY_RUN=0`, and
all arguments are `shlex`-quoted so resource ids can't break or inject shell syntax.

## 5. GenAI design

```
analyze(findings, summary) ──► LLMClient.from_settings()
                                   │ enabled?
                          ┌────────┴─────────┐
                        yes                   no / error / empty
                          │                       │
            grounded prompt (summary +            └─► _deterministic(...)  ◄─ always a good answer
            top findings + KB snippets)
                          │
            anthropic / openai / azure  (httpx, timeout, never raises)
                          │
            parse → executive_summary + runbook   (mode="llm")
```

- **Provider-agnostic** over `httpx` (no vendor SDKs) → fewer deps, fewer version pins, trivial to extend.
- **RAG-style grounding**: a curated FinOps knowledge base is retrieved by the categories present and injected into the
  prompt so the runbook cites vetted best practices rather than hallucinating.
- **Evaluation-minded guardrails**: bounded finding count + `max_tokens`, strict timeout, tolerant completion parsing, and
  a hard rule that **any failure → deterministic fallback**. The pipeline can never be broken by the model.

## 6. Cross-cutting concerns

- **Observability**: structured JSON logs with a request-id propagated via `contextvars`; Prometheus counters/histograms
  at `/metrics`; `/healthz` + `/readyz`. Ready to scrape from OpenTelemetry / CloudWatch / Azure Monitor.
- **Security**: optional `X-API-Key` gate, per-client in-process rate limiting (429), CORS, and parse/pipeline errors
  mapped to `422` without leaking internals. Secrets come from the environment only.
- **Config**: a single `pydantic-settings` object; list-like values are CSV to avoid env JSON-parsing pitfalls; the LLM
  target auto-resolves from standard provider env vars.

## 7. Testing strategy

Unit tests per pipeline stage; an **API contract** suite via `TestClient`; a **live-server** SDK round-trip (real Uvicorn
in a thread); a CLI smoke test; and a **mocked-transport** test that exercises the LLM dispatch + parse without a network.
The sample dataset is the shared oracle — its expected totals (17 findings / $1,095.32) are asserted across layers so a
regression in any stage fails loudly. 50 tests, 86% coverage, ruff-clean, enforced in CI on 3.11 + 3.12.

## 8. Scaling & production roadmap

- **Persistence**: point `DATABASE_URL` at Postgres; add Alembic migrations; partition `findings` by batch/date.
- **Throughput**: stream-parse very large CURs; move ingest+detect to a worker queue; cache summaries.
- **Detection**: add utilization-trend rules (right-sizing), commitment coverage (RI/SP), and tag-policy compliance.
- **GenAI**: response evaluation harness + golden-set regression for the analyst; per-account cost guardrails on tokens.
- **Security/Multitenancy**: OAuth2/OIDC, per-tenant isolation, signed remediation approvals, audit trail.
- **Delivery**: publish the SDK to PyPI (semver), the image to a registry, and IaC (Bicep/Terraform) for the service itself.
