# Platform Improvement Notes — Client Dry Run (2026-10-13)

Collected during the end-to-end client dry run ("Trust and acceptance of AVs
among vulnerable road users", 9 client papers).

## ✅ IMPLEMENTED 2026-10-13 (~17:00 PKT)

### 1. Agent 4 measurement model: evidence-grounded constructs (was HIGH)
**Was:** `_keywords(gap_text)` picked top-4 frequent words → "atheoretical",
"base", "objectives", "invoke".
**Now:** `extract_constructs()` mines the gap statement + unknown summary +
supporting-evidence titles against `CONSTRUCT_VOCABULARY` (13 canonical
constructs derived from the validated scale library + literature). Only
vocabulary constructs can surface; junk-only input returns [] → honest
"focal construct" placeholder. Client case now yields: perceived risk,
technology acceptance, behavioral intention, trust.
**Tests:** 4 in `tests/test_agent4_improvements.py`.

### 2. Regenerate protocol workflow (was MEDIUM)
**Was:** rejecting a protocol stalled the pipeline — no way forward.
**Now:** `agent4.regenerate_protocol()` + `POST /design/protocols/{id}/regenerate`
+ "🔄 Regenerate" expander in the Protocols tab (optional explicit constructs,
design-type override). Old row marked `superseded` (never deleted — revision
history preserved); new protocol starts `pending` through the normal approval
gate. Works for rejected AND approved-but-weak protocols, no pipeline restart.
**Tests:** 3 in `tests/test_agent4_improvements.py`.

## ✅ IMPLEMENTED 2026-10-13 — Round 2: all 5 deep improvements

### 1. Agent 5: real inferential stats + one-click suite
- New `anova` handler (one-way ANOVA with eta-squared) and `full_suite`
  (descriptives + correlation matrix + OLS regression with auto-detected
  outcome via name hints).
- UI: column **dropdowns** (read from the uploaded CSV header) instead of
  manual typing; multiselect for correlations/predictors.

### 2. Agent 11: concept-level journal scoring
- Replaced naive word-overlap with concept-level scoring: exact phrase =
  1.0, all-words = 0.7, partial = 0.3·fraction; phrase-IDF weighted;
  power-calibrated for confident display. Client query now: TR Part F 53%
  (#1, correct), JSR 51%, AAP 45% — ranking fixed AND scores confident.

### 3. UX: standalone vs task-linked confusion fixed
- `task_link_banner()` on tabs 5–9: active task → blue banner pointing to
  the task-linked flow.
- Sections relabeled: "🔗 Advance pipeline (task-linked)" vs
  "🔬 … (standalone exploration)".

### 4. Evidence: bulk verification
- `POST /extraction/evidence/bulk-verify` + `bulk_set_verification_state()`
  (defaults to only flipping `unverified` → never silently un-rejects).
- UI: per-paper "✔ Verify all N unverified" button + progress caption.

### 5. LLM layer (provider-agnostic, safe fallback)
- New `app/core/llm.py`: OpenAI-compatible client (`LLM_API_KEY`,
  `LLM_BASE_URL` default token.sensenova.ai/v1, `LLM_MODEL` default
  sensenova-6.8-flash-lite). `is_configured()` gates everything; key never
  logged/persisted/returned.
- Agent 4: LLM-generated RQs/hypotheses naming actual constructs
  (`_llm_rqs_and_hypotheses`), template fallback.
- Agent 6: LLM section prose from materials only
  (`_llm_section` + `_build_section`), per-section provenance
  (`llm`/`template`) stored in the manuscript.
- `GET /llm/status` + sidebar indicator (🤖 LLM: active/off).

**Tests:** 21 passing (`tests/test_agent4_improvements.py` + `tests/test_round2_improvements.py`).

## 3. (future runs…)
