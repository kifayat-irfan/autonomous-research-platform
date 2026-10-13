# Client Live-Session Click-Path Cheat Sheet
**Agentic AI Research Platform — exact buttons per stage, learned the hard way
in the 2026-10-13 dry run. For the live guided session with the China client.**

Session setup: project **Client Mobility Research**, topic
"Trust and acceptance of autonomous vehicles among vulnerable road users".
Style profile already saved into the project (impersonal academic voice;
Moreover/However/Therefore transitions) — writing uses it automatically.

---

## The golden rule (2 traps from today's dry run)

Every phase has TWO paths: a **standalone** button at the top of the tab and a
**task-linked** "Advance pipeline" section at the bottom. In the session,
**always use the task-linked one** — standalone runs are NOT connected to the
task and the pipeline will not advance (we lost ~10 minutes on this twice).

---

## Stage-by-stage

| Stage | Tab | Do this (task-linked path) | NOT this |
|---|---|---|---|
| 1. Papers | Papers | Upload PDFs → **Tasks tab → Resume** | — |
| 2. Evidence | Evidence | Review claims (page # + source link each) → **Approve** → Tasks tab → advance to gaps (Agent 3) | — |
| 3. Gaps | Gaps | Review gap briefs + novelty verdicts → approve ≥1 → **Advance pipeline → Advance to design (Agent 4)** | — |
| 4. Protocol | 📋 Protocols | Review design, measurement model, ethics → ✅ **Approve** ≥1 → **Advance pipeline → 🛡️ Advance to audit (Agent 7)** | — |
| 5. Analysis | 📊 Analysis | **Scroll to "Advance pipeline"** → select task → upload CSV → type → **📊 Advance to analysis (Agent 5)** → approve the task-linked run in "Analysis runs" | ❌ Standalone "Run analysis" at the top |
| 6. Writing | ✍️ Writing | **"Advance pipeline" → ✍️ Advance to writing (Agent 6)** → review sections + evidence map → ✅ **Approve** → Advance to final audit | ❌ "Draft manuscript" button |
| 7. Final audit | Audit | Automatic (Agent 7) → PASS → task = **verified (complete)** | — |

## Client questions you'll get (answered today — reuse these)

- **"Why do you need a CSV?"** → "This is your collected data — survey
  responses, experiment results. The protocol designed a mixed-methods study;
  Agent 5 needs data to compute on. Real project = your real CSV."
- **"What should the CSV look like?"** → One row per respondent, one column
  per measured variable (trust, perceived risk, acceptance… + demographics),
  headers in the first row.
- **"What is the repro script?"** → Every analysis ships the exact script that
  produced the numbers — anyone can re-run it on the same data and get the
  identical numbers. No black box; every number traceable.
- **Synthetic-data honesty line:** "For today's demo we used clearly-labeled
  synthetic data so you can see the analysis machinery. In your project you
  upload your own CSV." (Keep "synthetic" in the filename.)

## Things to say once, early

1. **Provenance promise:** every claim carries page number + source URL —
   any sentence in the final manuscript traces back to the exact PDF page.
2. **Audit rule:** HIGH-severity flags BLOCK the pipeline; nothing is
   auto-fixed — a human fixes and re-runs. (This is a feature, not a bug.)
3. **Journal matching (Agent 11):** scores only, never submits — and the DB
   has no mobility/AV journals, so expect generic matches (nursing run got
   PLOS ONE at 11%). Set that expectation before clicking it.
4. **If a protocol looks weak:** reject it — but know there is currently no
   regenerate button (fix planned), so rejection stalls until you re-draft.
