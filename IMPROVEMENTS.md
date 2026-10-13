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

## 3. (more to be added as future runs continue…)
