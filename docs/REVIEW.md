# Adversarial Review & Hardening

Before finalizing, FinOptic was put through an **adversarial multi-agent review**: reviewers swept five
dimensions in parallel, and **every finding was independently verified against the source** (default to
false-positive) before any change was made. The verifier confirmed **27 findings** and dismissed **2** as false
positives. 25 were fixed; 2 were consciously accepted with rationale. Every fix is covered by a regression test.

## Correctness
| # | Severity | Finding | Resolution |
|---|----------|---------|------------|
| 1 | **High** | Unknown `batch_uid` fell through to a **whole-database aggregate** in summary/analyze/export (wrong totals + cross-batch resource-ID leak) | Guard added to all read paths — an unknown batch returns empty, never the whole DB. `tests/test_hardening.py` |
| 2 | Medium | Same batch reported different totals across endpoints (sum-of-rounded vs rounded-sum) | Round once per finding; batch total sums the same rounded values → all endpoints agree |
| 3 | Low | `annual_savings` derived from un-rounded cost | Derived from the rounded monthly value (`annual == 12 × monthly`) |
| 4 | Low | Pagination had no tiebreaker after `monthly_cost DESC` | Added `id ASC` for deterministic paging |
| 5 | Low | Negative-cost lines (credits/refunds) became negative-waste findings | `_finding` skips non-positive cost in one place (guards all rules) |

## Security
| # | Severity | Finding | Resolution |
|---|----------|---------|------------|
| 6 | **High** | Newline in a billing-derived field could break out of a comment in the generated script | Every header field routed through `_inline()`; commands already `shlex`-quoted |
| 7 | Medium | Unbounded upload body on `/ingest` (memory DoS) | `max_upload_bytes` cap (25 MB) + early `413`; read is bounded |
| 8 | Medium | Raw exception text echoed to clients on ingest error | Generic message to client; full error logged server-side |
| 9 | High | Rate-limiter keyed on an attacker-controlled header with an unbounded map | Keys on client IP (or validated key); LRU-bounded to 10k keys |
| 10 | Low | Non-constant-time API-key comparison | `hmac.compare_digest` |
| 11 | Low | `/metrics` unauthenticated | **Accepted** — standard for Prometheus scraping; gate via network policy / separate token in production |

## API robustness
| # | Severity | Finding | Resolution |
|---|----------|---------|------------|
| 12 | **High** | Prometheus label used the raw URL path → unbounded metric cardinality | Labels on the matched **route template**, not the raw path |
| 13 | Medium | `init_db()` ran at import → a DB outage crashed the process | Moved to a FastAPI **lifespan** handler; failure degrades to not-ready |
| 14 | Low | `/readyz` reported ready without checking anything | Performs a `SELECT 1` round-trip; `503` on DB failure |

## GenAI safety
| # | Severity | Finding | Resolution |
|---|----------|---------|------------|
| 15 | **High** | Prompt injection: billing-derived fields injected verbatim could spoof the trusted best-practices section | `_safe()` sanitizer strips control chars + markdown markers and clips length |
| 16 | Medium | Completion parser missed bold-markdown headers (`**Runbook**`) | Header regexes broadened to tolerate markdown emphasis |
| 17 | Medium | Per-field prompt length was unbounded | Each untrusted field clipped (bounds total prompt size) |
| 18 | Low | OpenAI `max_tokens` vs `max_completion_tokens` | **Accepted** — correct for the default model; revisit per-model when adding reasoning models |
| 19 | Low | Deterministic fallback could treat gross cost as savings | Only savings/waste-named keys are honored |

## Cloud / FinOps domain accuracy
| # | Severity | Finding | Resolution |
|---|----------|---------|------------|
| 20 | **High** | Idle-LB rule could flag a **live** load balancer (0 healthy targets mid-deploy) | Trigger only on sustained zero requests; healthy-targets kept as evidence |
| 21 | **High** | Stale-snapshot rule could delete a valid backup on age alone | Retention-tagged snapshots are spared; remediation warns to confirm before deleting |
| 22 | Medium | NAT-gateway deletion leaves its Elastic IP billing | Remediation note instructs releasing the EIP afterward |
| 23 | Medium | EBS snapshot-then-delete raced (delete before snapshot completed) | Inserted `aws ec2 wait snapshot-completed` between the steps |
| 24 | Medium | RDS `stop-db-instance` is temporary and rejected on read replicas | Remediation note flags the replica/durability caveat |
| 25 | Medium | `az vm delete` leaves OS/data disks (the real cost) | Remediation note corrected + disk-cleanup guidance added |

**Dismissed (false positives):** 2 findings were refuted during verification against the actual code.
