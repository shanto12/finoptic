# Submission — Graduate Vibe Coding Challenge (Project 1)

**Candidate:** Shanto Mathew
**Project:** FinOptic — Cloud Cost Optimizer & Remediation Engine (FinOps)
**Tool used (end-to-end, no manual code edits):** Claude Code (Claude Opus)
**Live demo:** https://finoptic-shanto-demo.netlify.app (the dashboard, rendered from real sample output)

## Final submission checklist
- [x] **Public GitHub repository** — this repo (all source code).
- [x] **`prompts.md`** — the full architect-level audit log of the vibe-coding workflow.
- [x] **AI-generated presentation deck** — [`docs/FinOptic_Deck.pptx`](docs/FinOptic_Deck.pptx) (source: [`docs/deck.md`](docs/deck.md)).
- [ ] **Tagle.ai "Tag" output summary** — *to be attached by the candidate.* Phase 1 is a personal aptitude
  assessment that requires registering an account and answering authentically, so it must be completed by the candidate.
- [x] **Cloud-resource confirmation** — **N/A by design.** FinOptic provisions **no** cloud infrastructure; it
  analyzes *exported* billing data into a local SQLite file. There is nothing to decommission and no cloud spend was incurred.

## Run it in 60 seconds
```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

finoptic sample                          # offline CLI demo → 17 findings, $1,095.32/mo recoverable
uvicorn finoptic.api.app:app             # API + dashboard at http://localhost:8000  (click "Load sample data")
make test                                # 50 tests
```

## Reviewer's map
| To see… | Open |
| --- | --- |
| The dashboard, instantly | **Live demo →** https://finoptic-shanto-demo.netlify.app |
| The result | `finoptic sample` → **$1,095.32/mo ($13,143.84/yr)** across AWS + Azure |
| Project overview | [`README.md`](README.md) |
| System design | [`ARCHITECTURE.md`](ARCHITECTURE.md) |
| How it was built (prompts) | [`prompts.md`](prompts.md) |
| Engineering rigor | [`docs/REVIEW.md`](docs/REVIEW.md) — adversarial review + fixes · [`tests/`](tests) |
| The pitch | [`docs/FinOptic_Deck.pptx`](docs/FinOptic_Deck.pptx) |
