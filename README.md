# Autonomous Research Platform — Phases 1, 2 & 3 (12 agents + Supervisor)

A researcher-supervised multi-agent system covering the full research
lifecycle: literature discovery, evidence extraction, gap reasoning, study
design, **reproducible analysis, manuscript drafting, systematic review,
journal targeting, scientific audit, and literature intelligence** — built to
the project specification. Phase 1 delivered **a reliable literature bank
first** — daily multi-source paper discovery, deduplication, DOI/metadata
verification, access checks, PDF upload, passage-level retrieval, and alerts.
Phase 2 adds reasoning agents behind mandatory approval gates; Phase 3
completes the pipeline through analysis, writing, review, and publication
support — with every factual output linked to its source.

## Tech stack

| Layer | Technology |
|---|---|
| Backend / API | FastAPI (Python 3.10+) |
| Relational store | SQLite via SQLAlchemy ORM (Postgres-ready) |
| Vector search | ChromaDB (persistent, `data/chroma_db`) |
| PDF parsing | PyMuPDF |
| Discovery sources | Semantic Scholar API, PubMed E-utilities |
| Frontend | Streamlit |

## Project structure

```
research_platform/
├── app/
│   ├── main.py                  # FastAPI entry point
│   ├── api/
│   │   ├── discovery_routes.py  # Agent 1: /api/discovery/*
│   │   ├── extraction_routes.py # Agent 2: /api/extraction/*
│   │   ├── supervisor_routes.py # Supervisor: /api/supervisor/*
│   │   ├── memory_routes.py     # Agent 8: /api/memory/*
│   │   └── papers_routes.py     # Paper bank + dashboard stats
│   ├── core/
│   │   ├── config.py            # Settings (env/.env overridable)
│   │   ├── database.py          # Engine, sessions, init_db
│   │   └── chroma.py            # Persistent ChromaDB client
│   ├── models/
│   │   └── schemas.py           # SQLAlchemy models + Pydantic schemas
│   ├── services/                # Business logic (paper/evidence/task)
│   └── agents/
│       ├── supervisor.py        # Orchestrator + approval gates (all phases)
│       ├── agent1_discovery.py  # Semantic Scholar + PubMed + OA download
│       ├── agent2_extraction.py # PDF parse → ChromaDB → anchored evidence
│       ├── agent3_gap_discovery.py  # Phase 2: gaps + novelty challenge
│       ├── agent4_design.py         # Phase 2: protocols, scales, ethics
│       ├── agent5_analysis.py       # Phase 3: reproducible statistics
│       ├── agent6_writing.py        # Phase 3: drafting + Word export
│       ├── agent7_verification.py   # Phase 2/3: claim/numeric/DOI audits
│       ├── agent8_memory.py     # Decisions, workspace state, activity feed
│       ├── agent9_cross_domain.py   # Phase 2: cross-domain concept notes
│       ├── agent10_systematic_review.py  # Phase 3: PRISMA + meta-analysis
│       ├── agent11_journal.py         # Phase 3: journal match + responses
│       └── agent12_intelligence.py    # Phase 3: literature watches/alerts
├── ui/
│   └── app.py                   # Streamlit dashboard (12 tabs)
├── data/                        # PDFs + vector index + SQLite DB (local)
├── requirements.txt
└── README.md
```

## Quickstart

```bash
cd research_platform
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Terminal 1 — backend (http://localhost:8000, docs at /docs)
uvicorn app.main:app --reload

# Terminal 2 — dashboard (http://localhost:8501)
BACKEND_URL=http://localhost:8000 streamlit run ui/app.py
```

Optional `.env` in the project root:

```
DATABASE_URL=sqlite:///./data/research_platform.db
SEMANTIC_SCHOLAR_API_KEY=        # optional, raises S2S rate limits
NCBI_EMAIL=you@example.com       # polite NCBI identification
```

## The supervised pipeline

### Phase 1 — Literature bank
1. **Discovery (Agent 1)** — searches Semantic Scholar + PubMed, deduplicates
   by DOI/title, attempts legal open-access PDF download. Papers without a
   legal copy are marked `needs_pdf_upload` (upload queue — never treated as
   absent from the literature).
2. **Gate 1** — if no paper has a PDF, the task pauses as `needs_pdf_upload`.
   Upload PDFs (Discovery tab or API), then **Resume**.
3. **Extraction (Agent 2)** — parses PDFs page-by-page, indexes passages in
   ChromaDB, and builds structured extracts (objective, methodology, findings,
   direct quote). **Every claim carries `page_number` + `source_doi_or_url`** —
   unanchored claims are never emitted.
4. **Gate 2** — extracted evidence pauses as `needs_approval`. The researcher
   **Approves** (→ Agent 8 persists the decision to project memory, task →
   `verified`) or **Rejects** (→ `failed`).

### Phase 2 — Reasoning (explicitly advanced, step by step)
5. **Gap discovery (Agent 3)** — `advance-to-gaps`: analyzes the evidence
   matrix for theoretical / empirical / population / methodological gaps, then
   runs a **novelty-challenge counter-search** (Agent 1) trying to disprove each
   gap. Geography-only or method-swapping claims without a causal mechanism are
   rejected outright; disproved gaps are flagged `challenged_disproved` and can
   never be approved as novel.
6. **Gate 3** — task pauses as `needs_gap_approval`; the researcher
   accepts/rejects each gap brief (Tab 5).
7. **Research design (Agent 4)** — `advance-to-design`: builds a reviewable
   protocol (survey / experimental / observational / mixed-methods) for each
   approved gap, maps constructs to **validated scales with original citation
   links** (new items are always marked *proposed / unvalidated*), and produces
   a sampling + ethics plan with an explicit human-approval checkpoint.
8. **Gate 4** — task pauses as `needs_protocol_approval` (Tab 6). Nothing moves
   to data collection without researcher approval.
9. **Audit (Agent 7)** — `advance-to-audit`: independently checks cited claims
   against indexed passages, cross-checks numbers against extraction records,
   validates DOIs, and scans for causal-overreach wording. **HIGH-severity
   flags set the task to `blocked`** — the system never auto-fixes or proceeds.
   Clean audits → Agent 8 persists the decision → `verified` (Tab 7).
10. **Cross-domain ideas (Agent 9)** — standalone: bridges source-domain
    constructs to a target domain with **dual-domain citations**; every idea is
    labeled *hypothesis pending novelty search* until it passes Agent 3's
    counter-search.

Every run keeps an immutable provenance trail: `task_id`, timestamps,
duration, tool versions, per-paper source links, and error history in the
task's `execution_log`.

## API overview

| Endpoint | Purpose |
|---|---|
| `POST /api/discovery/search` | Agent 1 search + persist |
| `POST /api/discovery/papers/{id}/upload-pdf` | Manual PDF upload |
| `POST /api/extraction/papers/{id}/parse` | Parse + index passages |
| `POST /api/extraction/papers/{id}/extract` | Anchored evidence extraction |
| `POST /api/supervisor/tasks` | Create + run supervised pipeline |
| `POST /api/supervisor/tasks/{id}/resume` | Resume after PDF upload |
| `POST /api/supervisor/tasks/{id}/approve` | Approve / reject evidence |
| `POST /api/memory/projects` | Create project workspace |
| `GET /api/memory/projects/{id}/state` | Live workspace state |
| `GET /api/papers/stats` | Dashboard numbers |
| `POST /api/gaps/analyze` | Agent 3: gap discovery + challenge |
| `POST /api/gaps/{id}/review` | Researcher gap accept/reject |
| `POST /api/design/protocols` | Agent 4: protocol draft |
| `POST /api/design/protocols/{id}/review` | Researcher protocol accept/reject |
| `POST /api/audit/reports` | Agent 7: full pass/fail audit |
| `POST /api/audit/numerics` | Numerical consistency check |
| `POST /api/cross-domain/ideas` | Agent 9: cross-domain concept notes |
| `POST /api/supervisor/tasks/{id}/advance-to-gaps` | Phase 2: start Agent 3 |
| `POST /api/supervisor/tasks/{id}/advance-to-design` | Phase 2: start Agent 4 |
| `POST /api/supervisor/tasks/{id}/advance-to-audit` | Phase 2: start Agent 7 |
| `POST /api/supervisor/tasks/{id}/advance-to-analysis` | Phase 3: start Agent 5 |
| `POST /api/supervisor/tasks/{id}/advance-to-writing` | Phase 3: start Agent 6 |
| `POST /api/supervisor/tasks/{id}/advance-to-final-audit` | Phase 3: final Agent 7 |
| `POST /api/analysis/runs` | Agent 5: upload CSV + run analysis |
| `POST /api/writing/manuscripts` | Agent 6: draft manuscript |
| `POST /api/reviews` | Agent 10: systematic review project |
| `POST /api/journals/match` | Agent 11: journal scope matching |
| `POST /api/intelligence/watches` | Agent 12: literature watch |

## Scientific safeguards (from the spec)

- No fabricated publications, DOIs, numbers, or claims — extraction is
  heuristic and honestly labeled (`heuristic_rule_based_v1`).
- Abstract-only records are never treated as full-text evidence.
- Only **legal** open-access PDFs are downloaded; paywalls are never circumvented.
- Approval gates cannot be skipped: the system cannot reach `verified`
  without a recorded researcher action.

## The full pipeline (all 12 agents + Supervisor)

**Phase 3** continues after protocol approval:
11. **Analysis (Agent 5)** — `advance-to-analysis`: upload a CSV dataset;
    cleaning report, assumption-safe statistics (descriptive, t-test, χ²,
    correlation, OLS regression), and a generated stand-alone reproduction
    script. Results pause as `needs_analysis_approval`.
12. **Writing (Agent 6)** — `advance-to-writing`: drafts manuscript sections
    strictly from approved materials with a style profile built from your own
    texts; exports Word `.docx`. Pauses as `needs_manuscript_approval`.
13. **Final audit (Agent 7)** — `advance-to-final-audit`: independent check of
    the manuscript's claims, numbers, DOIs and causal wording → `verified` or
    `blocked`.

Standalone agents (routers + UI tabs, outside the linear pipeline):
- **Agent 10 — Systematic Review**: PICO protocol, logged searches, staged
  screening, PRISMA 2020 flow, effect extraction with documented conversions
  (r↔d, OR↔logOR), Risk-of-Bias, DerSimonian-Laird meta-analysis.
- **Agent 11 — Journal**: scope matching from a date-stamped journal knowledge
  base (verify officially before submitting — the system never submits) plus a
  reviewer response matrix.
- **Agent 12 — Intelligence**: saved literature watches; dedupe, relevance
  scoring, new-paper and novelty-challenge alerts.

## Roadmap

- Phase 1 (this repo): Agents 1, 2, 8 + Supervisor, Streamlit UI.
- Phase 2 (this repo): Agents 3, 4, 7, 9 + gated supervisor advancement.
- Phase 3 (this repo): Agents 5, 6, 10, 11, 12 + 5 new UI tabs — **platform complete**.
