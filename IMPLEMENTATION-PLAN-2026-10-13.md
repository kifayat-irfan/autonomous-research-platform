# Post-Dry-Run Implementation Plan
**Prepared 2026-10-13 · DO NOT implement until the client dry run completes (final audit done) — owner's order.**

Covers the two items in `IMPROVEMENTS.md` (logged during the 2026-10-13 dry run,
"Trust and acceptance of AVs among vulnerable road users", 9 client papers).
Researched against the actual code today; nothing changed yet.

---

## Fix 1 — Evidence-grounded constructs, Agent 4 (HIGH)

**Problem (observed in dry run):** Protocol's measurement model had constructs
"atheoretical", "objectives", "invoke" — top-4 frequent words from the gap
statement via `_keywords(gap_text)` (`app/agents/agent4_design.py:388`),
not real research constructs.

**Design:**
- New `_evidence_constructs(db, task_id, top_n=4) -> List[dict]` in
  `app/agents/agent4_design.py`:
  - Query `Evidence` rows where `task_id == task.task_id` — that IS the approved
    set (Gate 2 approves the whole evidence set and marks the task `verified`;
    verification states live in `app/services/evidence_service.py`).
  - Corpus = `claim_text + objective + method` per row.
  - Phrase mining: lowercase; regex 2–3 word phrases of words ≥3 letters;
    frequency-ranked. Stoplist = existing stopwords in `_keywords()` plus
    academic filler ("results show", "study found", "based on", "using",
    "among", "between", "associated", "significant", "increase", "effect",
    "data", "analysis", "sample"). Dedupe by substring containment (keep the
    longer phrase).
  - Boost phrases overlapping `SCALE_LIBRARY` keywords (34 scales at
    `agent4_design.py:34`) — they map to validated scales, so the measurement
    model can mark provenance `validated` instead of `proposed_unvalidated`.
  - Return `[{construct, source: "evidence", mentions: n, evidence_ids: [...]}]`.
- Wire-in at call site (`agent4_design.py:342`, inside
  `propose_research_protocol`, which already receives `task_id`):
  ```python
  ev = [c["construct"] for c in _evidence_constructs(db, task_id, 4)]
  construct_list = constructs or ev or _keywords(gap_text, top_n=4) or ["focal construct"]
  ```
  `_keywords` stays as the fallback for tasks with no evidence.
- Add `"construct_source": "evidence" | "gap_keyword" | "researcher"` to each
  measurement-model row so the UI can badge it (see UI note below).
- Unit test: fake gap with junky gap text + evidence rows containing known
  phrases ("trust in automation", "perceived risk") → assert constructs come
  from evidence, not from gap-text keywords.
- Effort: small (~60 lines + test).

## Fix 2 — "Regenerate protocol" path (MEDIUM)

**Problem (observed in dry run):** Rejecting a protocol marks it rejected and
stalls the pipeline — no regenerate button, no way forward except approving a
weak protocol.

**Design:**
- Backend: `agent4.regenerate_protocol(db, protocol_id, note="") -> dict`
  in `app/agents/agent4_design.py`:
  - `old = get_protocol(db, protocol_id)`; missing → ValueError.
  - Refuse if `old.approval_status == "approved"` (approved protocols gate
    downstream work — never regenerate those).
  - `old.approval_status = "superseded"` (fits `String(16)` on
    `ResearchProtocol.approval_status`, `schemas.py:346`); append
    `"[superseded by regenerate]"` to `researcher_note`.
  - `new = propose_research_protocol(gap_id=old.gap_id,
    design_type=old.design_type, db, task_id=old.task_id)` → starts `pending`.
  - Log the supersession.
- Route: `POST /design/protocols/{protocol_id}/regenerate` in
  `app/api/design_routes.py` → returns the new `ProtocolOut`
  (follow the existing `review_protocol` pattern at `design_routes.py:51`).
- UI: `ui/app.py` Tab 6 ("📋 Research Protocols", ~line 831) — add a
  "🔄 Regenerate" button next to ✅ Approve / ❌ Reject; on click
  `api_post(f"/design/protocols/{p['id']}/regenerate")` then `st.rerun()`.
  Show superseded rows with a grey badge. Task stays `needs_protocol_approval`;
  the new protocol needs a fresh approval.
- Unit test: propose → reject → regenerate → old is `superseded`, new is
  `pending`, same `gap_id`/`design_type`.
- Effort: small (~40 lines + test + UI button).

## Possible Fix 3 — Standalone vs task-linked trap (observed today, may already be logged)

Twice today the dry run went down the WRONG path and had to be corrected:
1. Ran a **standalone analysis** (Analysis tab top) instead of the task-linked
   "📊 Advance to analysis (Agent 5)" in the "Advance pipeline" section —
   had to re-run linked.
2. Pressed standalone **"Draft manuscript"** instead of the task-linked
   "✍️ Advance to writing (Agent 6)" in "Advance pipeline" — twice, incl. once
   after being told; then a task-linked analysis approval was still pending.

Fix options: (a) hide/disable the standalone buttons when a task-linked path is
available (task selected + status matches); (b) rename labels to make the
distinction explicit, e.g. "🧪 Standalone analysis (not linked to a task)" vs
"📊 Advance to analysis (Agent 5) — task-linked"; (c) when a standalone run
completes while a task awaits that phase, show a warning banner pointing at
the linked path. The client live session is the deadline for this fix.

## Rollout (after the run completes)
1. Implement Fix 1 + 2 (+3 if chosen) on the repo copy; run unit tests +
   compile check (`python -m compileall`).
2. Backend restart required for agent changes; Streamlit picks up UI on rerun.
3. Push to GitHub (`kifayat-irfan/autonomous-research-platform`); secret scan.
4. Verify in Streamlit: reject a protocol → regenerate → new pending protocol;
   design a protocol on evidence-heavy task → constructs read like real
   variables (trust, perceived risk), not gap-text words.
