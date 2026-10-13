"""Streamlit dashboard — Autonomous Research Platform (Phase 1).

Tabs:
  1. Home & Dashboard
  2. Literature Discovery (Agent 1)
  3. Evidence Explorer (Agent 2)
  4. Agent Tasks & Approvals (Supervisor & Agent 8)

COMPULSORY: every paper card, evidence entry and task log renders a
clickable external URL link to the source DOI / PubMed / publisher page.
"""
import os

import requests
import streamlit as st

BACKEND = os.getenv("BACKEND_URL", "http://localhost:8000")

st.set_page_config(page_title="Research Platform", page_icon="🔬", layout="wide")


# ---------------------------------------------------------------------------
# Backend helpers
# ---------------------------------------------------------------------------

def api_url(path: str) -> str:
    return f"{st.session_state.get('backend_url', BACKEND).rstrip('/')}/api{path}"


def api_get(path: str, params: dict | None = None):
    try:
        r = requests.get(api_url(path), params=params, timeout=60)
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        st.error(f"Backend GET {path} failed: {exc}")
        return None


def api_post(path: str, json: dict | None = None, files: dict | None = None,
             params: dict | None = None, data: dict | None = None):
    try:
        r = requests.post(api_url(path), json=json, files=files, params=params,
                          data=data, timeout=300)
        r.raise_for_status()
        return r.json()
    except requests.HTTPError as exc:
        detail = ""
        try:
            detail = exc.response.json().get("detail", "")
        except Exception:
            detail = exc.response.text[:300]
        st.error(f"Backend POST {path} failed ({exc.response.status_code}): {detail}")
        return None
    except Exception as exc:
        st.error(f"Backend POST {path} failed: {exc}")
        return None


def source_link_md(label: str, url: str | None) -> str:
    if url:
        return f"[{label}]({url})"
    return label


def status_badge(status: str) -> str:
    colors = {
        "metadata_only": "gray", "oa_url_found": "blue",
        "pdf_acquired": "green", "full_text_parsed": "green",
        "needs_pdf_upload": "orange", "extraction_failed": "red",
        "restricted": "red",
        "queued": "gray", "running": "blue", "needs_approval": "orange",
        "verified": "green", "failed": "red",
        "machine_extracted": "gray", "needs_review": "orange",
        "verified": "green", "rejected": "red",
        "researcher_approved": "green", "unverified": "gray",
        "needs_gap_approval": "orange", "needs_protocol_approval": "orange",
        "blocked": "red", "challenged_pass": "green",
        "challenged_disproved": "red", "pending": "gray",
        "approved": "green", "rejected": "red",
        "pass": "green", "fail": "orange",
        "pending_search": "gray",
    }
    return f":{colors.get(status, 'gray')}[{status}]"


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("⚙️ Connection")
    st.text_input("Backend URL", value=BACKEND, key="backend_url")
    health = api_get("/health")
    if health:
        st.success("Backend: online")
    else:
        st.warning("Backend unreachable — start it with:\n`uvicorn app.main:app`")

st.title("🔬 Autonomous Research Platform")
st.caption("Phase 1 — Literature-bank MVP · every claim links to its source")

tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8, tab9, tab10, tab11, tab12 = st.tabs([
    "🏠 Home",
    "🔎 Discovery",
    "🧾 Evidence",
    "✅ Tasks",
    "💡 Gaps",
    "📋 Protocols",
    "🛡️ Audit",
    "📊 Analysis",
    "✍️ Writing",
    "📚 Review",
    "📰 Journal",
    "📡 Intel",
])

# ---------------------------------------------------------------------------
# TAB 1 — Home & Dashboard
# ---------------------------------------------------------------------------
with tab1:
    st.header("Home & Dashboard")
    stats = api_get("/papers/stats") or {}
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Papers in bank", stats.get("papers", 0))
    c2.metric("Evidence items", stats.get("evidence_items", 0))
    c3.metric("Awaiting approval", stats.get("tasks_need_approval", 0))
    c4.metric("Papers need PDF upload", stats.get("papers_need_upload", 0))

    st.subheader("Action needed")
    tasks = api_get("/supervisor/tasks") or []
    gate_tab = {"needs_approval": "Tasks",
                "needs_pdf_upload": "Tasks",
                "needs_gap_approval": "Gaps",
                "needs_protocol_approval": "Protocols",
                "needs_analysis_approval": "Analysis",
                "needs_manuscript_approval": "Writing",
                "blocked": "Audit"}
    attention = [t for t in tasks if t["status"] in gate_tab]
    if attention:
        for t in attention:
            st.warning(f"⚠️ Task `{t['task_id'][:8]}` — **{t['status']}** · "
                       f"query: *{t['query'][:80]}* → see *{gate_tab[t['status']]}* tab.")
    else:
        st.info("Nothing waiting on you right now. 🎉")

    st.subheader("Active projects")
    projects = api_get("/memory/projects") or []
    with st.expander("➕ New project"):
        with st.form("new_project"):
            name = st.text_input("Project name")
            desc = st.text_area("Description")
            kw = st.text_input("Keywords (comma-separated)")
            if st.form_submit_button("Create project") and name:
                res = api_post("/memory/projects", json={
                    "name": name, "description": desc or None,
                    "keywords": [k.strip() for k in kw.split(",") if k.strip()] or None,
                })
                if res:
                    st.success(f"Project '{name}' created.")
                    st.rerun()
    if projects:
        for p in projects:
            with st.expander(f"📁 {p['name']}"):
                st.write(p.get("description") or "_No description_")
                state = api_get(f"/memory/projects/{p['id']}/state")
                if state:
                    s = state["summary"]
                    st.write(f"Papers: **{s['papers']}** · Evidence: "
                             f"**{s['evidence_items']}** · Decisions: "
                             f"**{s['decisions']}**")
                    st.markdown("**Recent activity**")
                    for a in state["activity_feed"][:5]:
                        link = f" · {source_link_md('evidence', a['evidence_link'])}" \
                            if a.get("evidence_link") else ""
                        st.caption(f"`{a['agent_name']}` — {a['action'][:120]}{link}")
    else:
        st.info("No projects yet — create one using '➕ New project' above.")

# ---------------------------------------------------------------------------
# TAB 2 — Literature Discovery (Agent 1)
# ---------------------------------------------------------------------------
with tab2:
    st.header("Literature Discovery — Agent 1")
    projects = api_get("/memory/projects") or []
    proj_opts = {"(none)": None, **{p["name"]: p["id"] for p in projects}}

    with st.form("discovery_form"):
        query = st.text_input("Research query",
                              placeholder="e.g. human-AI trust in autonomous vehicles")
        col_a, col_b = st.columns(2)
        use_s2s = col_a.checkbox("Semantic Scholar", value=True)
        use_pubmed = col_b.checkbox("PubMed", value=True)
        col_c, col_d = st.columns(2)
        limit = col_c.slider("Max papers per source", 1, 30, 10)
        project_name = col_d.selectbox("Attach to project", list(proj_opts.keys()))
        download = st.checkbox("Attempt legal open-access PDF download", value=True)
        go = st.form_submit_button("🔎 Search")

    if go and query:
        sources = []
        if use_s2s:
            sources.append("semantic_scholar")
        if use_pubmed:
            sources.append("pubmed")
        with st.spinner("Agent 1 searching…"):
            res = api_post("/discovery/search", json={
                "query": query, "limit": limit, "sources": sources,
                "project_id": proj_opts[project_name], "download_pdfs": download,
            })
        if res:
            st.session_state["last_search"] = res
            st.success(f"Found **{res['count']}** unique papers "
                       f"({res['need_upload']} need PDF upload).")

    res = st.session_state.get("last_search")
    if res:
        for p in res["papers"]:
            with st.container(border=True):
                st.markdown(f"### {source_link_md(p['title'], p['source_link'])}")
                authors = ", ".join(p.get("authors") or [])
                st.caption(f"{authors} — {p.get('journal') or ''} "
                           f"({p.get('pub_year') or 'n/a'})")
                meta = (f"{status_badge(p['status'])} · "
                        f"source: `{p.get('source_api')}` · "
                        f"{'🟢 OA PDF found' if p.get('oa_url') else '⚪ no OA link'}")
                if p.get("doi"):
                    meta += f" · DOI: `{p['doi']}`"
                st.markdown(meta)
                st.link_button("🔗 Open source", p["source_link"])
                if p.get("abstract"):
                    with st.expander("Abstract"):
                        st.write(p["abstract"])

                c1, c2 = st.columns(2)
                if not p.get("pdf_path"):
                    if c1.button("⬇ Try OA download", key=f"dl_{p['id']}"):
                        upd = api_post(f"/discovery/papers/{p['id']}/download")
                        if upd:
                            st.success(f"Status → {upd['status']}")
                            st.rerun()
                else:
                    c1.success(f"PDF ready: `{p['pdf_path'].split('/')[-1]}`")
                up = c2.file_uploader("Manual PDF upload", type=["pdf"],
                                      key=f"up_{p['id']}")
                if up is not None:
                    with st.spinner("Uploading…"):
                        upd = api_post(
                            f"/discovery/papers/{p['id']}/upload-pdf",
                            files={"file": (up.name, up.getvalue(),
                                            "application/pdf")},
                        )
                    if upd:
                        st.success("PDF uploaded and linked.")
                        st.rerun()

    # --- Project paper bank (includes task-discovered papers) ---
    st.subheader("📚 Project papers")
    bank_proj = st.selectbox("Show papers for project", list(proj_opts.keys()),
                             key="bank_proj")
    bank_pid = proj_opts[bank_proj]
    with st.expander("➕ Add your own paper (e.g. client's publications)"):
        if not bank_pid:
            st.warning("Select a project first.")
        else:
            mp_title = st.text_input("Paper title", key="mp_title")
            mp_authors = st.text_input("Authors (comma-separated)", key="mp_authors")
            c1, c2 = st.columns(2)
            mp_journal = c1.text_input("Journal", key="mp_journal")
            mp_year = c2.text_input("Year", key="mp_year")
            mp_doi = st.text_input("DOI (optional)", key="mp_doi")
            mp_file = st.file_uploader("PDF file", type=["pdf"], key="mp_file")
            if st.button("Add paper") and mp_title.strip() and mp_file is not None:
                with st.spinner("Adding paper…"):
                    data = {"project_id": bank_pid, "title": mp_title.strip(),
                            "authors": mp_authors, "journal": mp_journal,
                            "doi": mp_doi}
                    if mp_year.strip().isdigit():
                        data["year"] = int(mp_year.strip())
                    res = api_post("/discovery/papers/manual", data=data,
                                   files={"file": (mp_file.name,
                                                   mp_file.getvalue(),
                                                   "application/pdf")})
                if res:
                    st.success(f"Added: {res['title'][:60]} — PDF ready.")
                    st.rerun()
    bank_papers = api_get("/discovery/papers",
                          params={"project_id": bank_pid} if bank_pid else {}) or []
    if not bank_papers:
        st.info("No papers in this project yet — run a search above or a "
                "supervised task from the Tasks tab.")
    for p in bank_papers:
        with st.container(border=True):
            st.markdown(f"### {source_link_md(p['title'], p['source_link'])}")
            authors = ", ".join(p.get("authors") or [])
            st.caption(f"{authors} — {p.get('journal') or ''} "
                       f"({p.get('pub_year') or 'n/a'})")
            st.markdown(f"{status_badge(p['status'])} · "
                        f"source: `{p.get('source_api')}`")
            c1, c2 = st.columns(2)
            if not p.get("pdf_path"):
                if c1.button("⬇ Try OA download", key=f"bdl_{p['id']}"):
                    upd = api_post(f"/discovery/papers/{p['id']}/download")
                    if upd:
                        st.success(f"Status → {upd['status']}")
                        st.rerun()
            else:
                c1.success(f"PDF ready: `{p['pdf_path'].split('/')[-1]}`")
            up = c2.file_uploader("Manual PDF upload", type=["pdf"],
                                  key=f"bup_{p['id']}")
            if up is not None:
                with st.spinner("Uploading…"):
                    upd = api_post(
                        f"/discovery/papers/{p['id']}/upload-pdf",
                        files={"file": (up.name, up.getvalue(),
                                        "application/pdf")},
                    )
                if upd:
                    st.success("PDF uploaded and linked.")
                    st.rerun()

# ---------------------------------------------------------------------------
# TAB 3 — Evidence Explorer (Agent 2)
# ---------------------------------------------------------------------------
with tab3:
    st.header("Evidence Explorer — Agent 2")
    papers = api_get("/papers", params={"limit": 200}) or []
    if not papers:
        st.info("No papers yet — run a discovery search first.")
    else:
        options = {f"{p['title'][:70]} ({p['status']})": p for p in papers}
        sel = st.selectbox("Paper", list(options.keys()))
        paper = options[sel]
        st.markdown(f"**Source:** {source_link_md(paper['source_link'], paper['source_link'])}")

        if paper["status"] == "pdf_acquired":
            if st.button("📖 Parse PDF & index passages"):
                with st.spinner("Parsing…"):
                    out = api_post(f"/extraction/papers/{paper['id']}/parse")
                if out:
                    st.success(f"Indexed {out['passages_indexed']} passages "
                               f"from {out['pages']} pages.")
                    st.rerun()
        elif paper["status"] == "needs_pdf_upload":
            st.warning("No PDF yet — upload one in the Discovery tab.")
        elif paper["status"] == "metadata_only":
            st.warning("Only metadata available for this paper.")

        if paper["status"] == "full_text_parsed":
            q = st.text_input("Evidence question",
                              placeholder="e.g. What sample sizes were used?")
            top_k = st.slider("Top findings", 1, 10, 5)
            if st.button("⚗️ Extract evidence") and q:
                with st.spinner("Agent 2 extracting…"):
                    ext = api_post(f"/extraction/papers/{paper['id']}/extract",
                                   json={"query": q,
                                         "project_id": paper.get("project_id"),
                                         "top_k": top_k})
                if ext:
                    st.session_state["last_extract"] = ext

            ext = st.session_state.get("last_extract")
            if ext and ext.get("key_findings"):
                st.subheader("Structured study extract")
                if ext.get("objective"):
                    o = ext["objective"]
                    st.markdown(f"**🎯 Objective** (p. {o['page_number']}) — "
                                f"{source_link_md('source', o['source_pointer'])}")
                    st.write(o["text"])
                if ext.get("methodology"):
                    m = ext["methodology"]
                    st.markdown(f"**🔬 Methodology** (p. {m['page_number']}) — "
                                f"{source_link_md('source', m['source_pointer'])}")
                    st.write(m["text"])
                if ext.get("sample_size"):
                    st.markdown(f"**👥 Sample size:** `{ext['sample_size']}`")
                st.markdown("**📌 Key findings**")
                for f in ext["key_findings"]:
                    with st.container(border=True):
                        st.write(f["claim_text"])
                        st.markdown(f"p. **{f['page_number']}** · "
                                    f"{source_link_md('🔗 evidence link', f['source_pointer'])}")
                if ext.get("direct_quote"):
                    dq = ext["direct_quote"]
                    st.markdown(f"**💬 Direct quote** (p. {dq['page_number']}) — "
                                f"{source_link_md('source', dq['source_pointer'])}")
                    st.blockquote(dq["paragraph_quote"][:800])
                st.caption(f"method: `{ext['extraction_method']}` · "
                           f"state: {status_badge(ext['verification_state'])} · "
                           f"abstract-only entries are never treated as full-text evidence")

        st.subheader("Saved evidence")
        ev_rows = api_get("/extraction/evidence",
                          params={"paper_id": paper["id"], "limit": 50}) or []
        for e in ev_rows:
            with st.container(border=True):
                st.write(e["claim_text"])
                meta = f"p. **{e.get('page_number', '?')}** · " \
                       f"{status_badge(e['verification_state'])}"
                if e.get("method"):
                    meta += f" · method: *{e['method'][:80]}*"
                st.markdown(meta)
                if e.get("paragraph_quote"):
                    st.blockquote(e["paragraph_quote"][:500])
                if e.get("source_url"):
                    st.link_button("🔗 Evidence link", e["source_url"])
                if st.button("✔ Mark verified", key=f"ver_{e['id']}"):
                    api_post(f"/extraction/evidence/{e['id']}/verify",
                             params={"state": "verified"})
                    st.rerun()

# ---------------------------------------------------------------------------
# TAB 4 — Tasks & Approvals (Supervisor & Agent 8)
# ---------------------------------------------------------------------------
with tab4:
    st.header("Agent Tasks & Approvals")
    st.caption("Supervisor pipeline: Discovery → Extraction → "
               "mandatory researcher approval → Project Memory.")

    with st.form("new_task"):
        tq = st.text_input("New research task",
                           placeholder="e.g. trust calibration in robotaxis")
        _t_projects = api_get("/memory/projects") or []
        _t_proj_opts = {p["name"]: p["id"] for p in _t_projects}
        tproj = st.selectbox("Attach to project", list(_t_proj_opts.keys()),
                             key="new_task_proj") if _t_proj_opts else None
        if st.form_submit_button("▶ Run supervised pipeline") and tq:
            with st.spinner("Supervisor running (discovery → extraction)…"):
                _payload = {"query": tq, "limit": 10}
                if tproj:
                    _payload["project_id"] = _t_proj_opts[tproj]
                t = api_post("/supervisor/tasks", json=_payload)
            if t:
                st.success(f"Task finished as **{t['status']}** "
                           f"in {t.get('duration_seconds') or 0:.1f}s.")
                st.rerun()

    tasks = api_get("/supervisor/tasks") or []
    if not tasks:
        st.info("No tasks yet.")
    for t in tasks:
        with st.expander(
                f"{status_badge(t['status'])} · {t['query'][:70]} "
                f"(`{t['task_id'][:8]}`)"):
            c1, c2 = st.columns([3, 1])
            c1.write(f"**Agent:** `{t['agent_name']}` · "
                     f"**Duration:** {t.get('duration_seconds') or '—'}s")
            if t.get("error"):
                c1.error(t["error"])
            with c2:
                if t["status"] == "needs_approval":
                    note = st.text_input("Note", key=f"note_{t['task_id']}")
                    a, b = st.columns(2)
                    if a.button("✅ Approve", key=f"ap_{t['task_id']}"):
                        api_post(f"/supervisor/tasks/{t['task_id']}/approve",
                                 json={"approved": True, "note": note})
                        st.rerun()
                    if b.button("❌ Reject", key=f"rj_{t['task_id']}"):
                        api_post(f"/supervisor/tasks/{t['task_id']}/approve",
                                 json={"approved": False, "note": note})
                        st.rerun()
                elif t["status"] == "needs_pdf_upload":
                    st.info("Upload missing PDFs in the Discovery tab, then:")
                    if st.button("▶ Resume", key=f"rs_{t['task_id']}"):
                        api_post(f"/supervisor/tasks/{t['task_id']}/resume")
                        st.rerun()
                elif t["status"] == "failed":
                    if st.button("🔁 Retry", key=f"rt_{t['task_id']}"):
                        api_post(f"/supervisor/tasks/{t['task_id']}/retry")
                        st.rerun()
                elif t["status"] == "verified":
                    gaps = api_get("/gaps", params={"task_id": t["task_id"]}) or []
                    if not gaps:
                        st.info("Phase 1 complete. Continue to Phase 2:")
                        if st.button("💡 Gap discovery (Agent 3)",
                                     key=f"g3_{t['task_id']}"):
                            api_post(f"/supervisor/tasks/{t['task_id']}/advance-to-gaps")
                            st.rerun()
                    else:
                        st.success("Phase 2 pipeline finished for this task.")
                elif t["status"] == "needs_gap_approval":
                    st.info("Review gaps in the *Gaps & Novelty* tab, then:")
                    dtype = st.selectbox(
                        "Design type", ["mixed_methods", "survey", "experimental",
                                       "observational"], key=f"dt_{t['task_id']}")
                    if st.button("📋 Advance to design (Agent 4)",
                                 key=f"ad_{t['task_id']}"):
                        api_post(f"/supervisor/tasks/{t['task_id']}/advance-to-design",
                                 json={"design_type": dtype})
                        st.rerun()
                elif t["status"] == "needs_protocol_approval":
                    st.info("Review protocols in *Protocols*, then:")
                    if st.button("🛡️ Advance to audit (Agent 7)",
                                 key=f"au_{t['task_id']}"):
                        api_post(f"/supervisor/tasks/{t['task_id']}/advance-to-audit")
                        st.rerun()
                    st.caption("Or continue to Phase 3: upload the dataset in the "
                               "*Analysis* tab and advance from there.")
                elif t["status"] == "needs_analysis_approval":
                    st.info("Review results in the *Analysis* tab, then:")
                    if st.button("✍️ Advance to writing (Agent 6)",
                                 key=f"aw_{t['task_id']}"):
                        api_post(f"/supervisor/tasks/{t['task_id']}/advance-to-writing",
                                 json={})
                        st.rerun()
                elif t["status"] == "needs_manuscript_approval":
                    st.info("Review the draft in the *Writing* tab, then:")
                    if st.button("🛡️ Final audit (Agent 7)",
                                 key=f"fa_{t['task_id']}"):
                        api_post(f"/supervisor/tasks/{t['task_id']}/advance-to-final-audit")
                        st.rerun()
                elif t["status"] == "blocked":
                    st.error("⛔ Blocked by high-severity audit flags. "
                             "See *Audit & Quality* tab.")
            with st.expander("📜 Execution log (provenance trail)"):
                st.code(t.get("execution_log") or "(empty)", language="text")

# ---------------------------------------------------------------------------
# TAB 5 — Research Gaps & Novelty (Agent 3) + Cross-domain ideas (Agent 9)
# ---------------------------------------------------------------------------
with tab5:
    st.header("💡 Research Gaps & Novelty — Agent 3")
    st.caption("Every gap is challenged against prior art before you see a "
               "novelty claim. Disproved gaps cannot be approved.")
    projects = api_get("/memory/projects") or []
    proj_opts = {p["name"]: p["id"] for p in projects}
    psel = st.selectbox("Project", list(proj_opts.keys()), key="gap_proj") \
        if proj_opts else None

    if psel:
        pid = proj_opts[psel]
        if st.button("🔍 Run gap discovery"):
            with st.spinner("Agent 3 analyzing evidence + counter-searching…"):
                res = api_post("/gaps/analyze",
                               json={"project_id": pid, "max_gaps": 5})
            if res is not None:
                st.success(f"{len(res)} gap brief(s) generated.")
                st.rerun()

        gaps = api_get("/gaps", params={"project_id": pid}) or []
        if not gaps:
            st.info("No gap briefs yet — run gap discovery above.")
        for g in gaps:
            with st.container(border=True):
                st.markdown(f"**{g['gap_statement']}**")
                st.markdown(f"{status_badge(g['gap_type'])} · "
                            f"{status_badge(g['verification_status'])} · "
                            f"researcher: {status_badge(g['researcher_decision'])}")
                st.caption(f"Importance **{g.get('importance_score')}/10** · "
                           f"Feasibility **{g.get('feasibility_score')}/10**")
                with st.expander("Known vs unknown"):
                    st.markdown(f"**Known:** {g.get('known_summary') or '—'}")
                    st.markdown(f"**Unknown:** {g.get('unknown_summary') or '—'}")
                with st.expander(
                        f"Supporting evidence ({len(g['supporting_evidence'])})"):
                    for e in g["supporting_evidence"]:
                        st.markdown(
                            f"- {source_link_md((e.get('title') or 'paper')[:80], e.get('source_link'))} "
                            f"(p. {e.get('page_number', '?')})")
                with st.expander(
                        f"Novelty counter-search ({len(g['counter_search_results'])})"):
                    st.caption(f"query: `{g.get('counter_search_query')}`")
                    if not g["counter_search_results"]:
                        st.caption("No prior-art results returned.")
                    for r in g["counter_search_results"]:
                        st.markdown(
                            f"- {source_link_md((r.get('title') or 'result')[:80], r.get('source_link'))} "
                            f"· {r.get('relevance_note', '')} `{r.get('source_api', '')}`")
                c1, c2, c3 = st.columns([3, 2, 2])
                note = c1.text_input("Reviewer note", key=f"gnot_{g['id']}")
                dtype = c2.selectbox(
                    "Design", ["mixed_methods", "survey", "experimental",
                               "observational"], key=f"gdt_{g['id']}")
                with c3:
                    if st.button("✅ Approve", key=f"gap_{g['id']}"):
                        api_post(f"/gaps/{g['id']}/review",
                                 json={"approved": True, "note": note,
                                       "design_type": dtype})
                        st.rerun()
                    if st.button("❌ Reject", key=f"grj_{g['id']}"):
                        api_post(f"/gaps/{g['id']}/review",
                                 json={"approved": False, "note": note})
                        st.rerun()
                if st.button("🔁 Re-run counter-search", key=f"gch_{g['id']}"):
                    with st.spinner("Counter-searching…"):
                        api_post(f"/gaps/{g['id']}/challenge")
                    st.rerun()

        st.subheader("Advance pipeline")
        tasks_g = [t for t in (api_get("/supervisor/tasks") or [])
                   if t["status"] == "needs_gap_approval"]
        if tasks_g:
            topts = {f"{t['query'][:50]}… (`{t['task_id'][:8]}`)": t["task_id"]
                     for t in tasks_g}
            tsel = st.selectbox("Task awaiting gap decisions",
                                list(topts.keys()), key="adv_gap_task")
            adtype = st.selectbox(
                "Design type", ["mixed_methods", "survey", "experimental",
                                "observational"], key="adv_gap_dtype")
            if st.button("📋 Advance to design (Agent 4)"):
                api_post(f"/supervisor/tasks/{topts[tsel]}/advance-to-design",
                         json={"design_type": adtype})
                st.rerun()
        else:
            st.caption("No tasks waiting on gap decisions.")

    st.divider()
    st.subheader("🔀 Cross-domain ideas — Agent 9")
    st.caption("Ideas are hypotheses pending Agent 3 novelty search — never findings.")
    if psel:
        pid = proj_opts[psel]
        c1, c2 = st.columns(2)
        tdom = c1.text_input("Target domain",
                             placeholder="e.g. autonomous logistics",
                             key="xd_tdom")
        sdom = c2.text_input("Source domain label (optional)", key="xd_sdom")
        if st.button("💡 Generate cross-domain ideas") and tdom:
            with st.spinner("Agent 9 bridging domains…"):
                notes = api_post("/cross-domain/ideas",
                                 json={"project_id": pid,
                                       "target_domain": tdom,
                                       "source_domain": sdom or None,
                                       "max_ideas": 3})
            if notes is not None:
                st.success(f"{len(notes)} concept note(s) drafted.")
                st.rerun()
        notes = api_get("/cross-domain/notes", params={"project_id": pid}) or []
        for n in notes:
            with st.container(border=True):
                st.markdown(f"**{n['title']}**")
                st.markdown(f"{status_badge(n['novelty_status'])} · "
                            f"{n.get('source_domain', '')} → "
                            f"**{n.get('target_domain', '')}**")
                st.write(n.get("bridge_concept"))
                st.caption(f"Shared mechanism: {n.get('shared_mechanism')}")
                with st.expander("Citations — both domains"):
                    st.markdown("**Source domain:**")
                    for c in n["source_citations"]:
                        st.markdown(f"- {source_link_md((c.get('title') or '')[:80], c.get('source_link'))}")
                    st.markdown("**Target domain:**")
                    if not n["target_citations"]:
                        st.caption("No target-domain citations retrieved.")
                    for c in n["target_citations"]:
                        st.markdown(f"- {source_link_md((c.get('title') or '')[:80], c.get('source_link'))}")
                with st.expander("Feasibility"):
                    st.json(n.get("feasibility", {}))
                if st.button("🔍 Run novelty challenge (Agent 3)",
                             key=f"xdch_{n['id']}"):
                    with st.spinner("Counter-searching…"):
                        api_post(f"/cross-domain/notes/{n['id']}/challenge")
                    st.rerun()

# ---------------------------------------------------------------------------
# TAB 6 — Research Protocols & Measurement (Agent 4)
# ---------------------------------------------------------------------------
with tab6:
    st.header("📋 Research Protocols & Measurement — Agent 4")
    st.caption("Protocols are drafts until YOU approve them — nothing moves to "
               "data collection without your explicit approval.")
    projects = api_get("/memory/projects") or []
    proj_opts = {p["name"]: p["id"] for p in projects}
    psel = st.selectbox("Project", list(proj_opts.keys()), key="proto_proj") \
        if proj_opts else None

    if psel:
        pid = proj_opts[psel]
        protocols = api_get("/design/protocols", params={"project_id": pid}) or []
        if not protocols:
            st.info("No protocols yet — approve gaps in Tab 5, then advance to design.")
        for p in protocols:
            with st.container(border=True):
                st.markdown(f"### {p['title']}")
                st.markdown(f"{status_badge(p['design_type'])} · approval: "
                            f"{status_badge(p['approval_status'])}")
                with st.expander("Research questions & hypotheses"):
                    for rq in p["research_questions"]:
                        st.markdown(f"- {rq}")
                    for h in p["hypotheses"]:
                        st.markdown(f"- *{h}*")
                with st.expander("Protocol sections"):
                    for k, v in p["sections"].items():
                        st.markdown(f"**{k.replace('_', ' ').title()}**")
                        if isinstance(v, list):
                            for item in v:
                                st.markdown(f"- {item}")
                        else:
                            st.write(v)
                with st.expander(
                        f"Measurement model ({len(p['measurement_model'])})"):
                    for m in p["measurement_model"]:
                        prov = m.get("provenance")
                        badge = ("🟢 validated"
                                 if prov == "validated"
                                 else "🟠 PROPOSED — NOT validated")
                        st.markdown(f"**{m['construct']}** → {m['scale_name']} · {badge}")
                        if m.get("citation"):
                            st.caption(m["citation"])
                        if m.get("source_link"):
                            st.link_button("🔗 Original scale source",
                                           m["source_link"])
                        if prov != "validated":
                            st.warning(m.get("usage_note",
                                             "Newly written items — pilot and validate before use."))
                        elif m.get("usage_note"):
                            st.caption(m["usage_note"])
                with st.expander(
                        f"Ethics checklist ({len(p['ethics_checklist'])})"):
                    for item in p["ethics_checklist"]:
                        if "HUMAN APPROVAL" in item.get("item", ""):
                            st.error(f"⛔ **{item['item']}** — {item['detail']}")
                        else:
                            st.markdown(f"⬜ **{item['item']}** — {item['detail']}")
                cp = p.get("approval_checkpoint", {})
                if cp.get("required"):
                    st.warning(f"🛑 {cp.get('message')}")
                c1, c2 = st.columns([3, 1])
                note = c1.text_input("Reviewer note", key=f"pnot_{p['id']}")
                with c2:
                    if st.button("✅ Approve", key=f"pok_{p['id']}"):
                        api_post(f"/design/protocols/{p['id']}/review",
                                 json={"approved": True, "note": note})
                        st.rerun()
                    if st.button("❌ Reject", key=f"prj_{p['id']}"):
                        api_post(f"/design/protocols/{p['id']}/review",
                                 json={"approved": False, "note": note})
                        st.rerun()

        st.subheader("Advance pipeline")
        tasks_p = [t for t in (api_get("/supervisor/tasks") or [])
                   if t["status"] == "needs_protocol_approval"]
        if tasks_p:
            topts = {f"{t['query'][:50]}… (`{t['task_id'][:8]}`)": t["task_id"]
                     for t in tasks_p}
            tsel = st.selectbox("Task awaiting protocol decisions",
                                list(topts.keys()), key="adv_proto_task")
            if st.button("🛡️ Advance to audit (Agent 7)"):
                api_post(f"/supervisor/tasks/{topts[tsel]}/advance-to-audit")
                st.rerun()
        else:
            st.caption("No tasks waiting on protocol decisions.")

# ---------------------------------------------------------------------------
# TAB 7 — Scientific Audit & Quality Check (Agent 7)
# ---------------------------------------------------------------------------
with tab7:
    st.header("🛡️ Scientific Audit & Quality Check — Agent 7")
    st.caption("Independent verification: HIGH-severity flags block progression. "
               "Nothing is auto-fixed.")
    projects = api_get("/memory/projects") or []
    proj_opts = {p["name"]: p["id"] for p in projects}
    psel = st.selectbox("Project", list(proj_opts.keys()), key="audit_proj") \
        if proj_opts else None

    if psel:
        pid = proj_opts[psel]
        if st.button("🔍 Run full audit"):
            with st.spinner("Agent 7 auditing…"):
                rep = api_post("/audit/reports", json={"project_id": pid})
            if rep:
                st.success(f"Audit complete: **{rep['overall_status']}**")
                st.rerun()

        reports = api_get("/audit/reports", params={"project_id": pid}) or []
        if not reports:
            st.info("No audit reports yet.")
        for r in reports:
            with st.container(border=True):
                st.markdown(f"**Report `{r['id'][:8]}`** · "
                            f"{status_badge(r['overall_status'])}")
                s = r.get("summary", {})
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("🔴 High", s.get("flags_high", 0))
                m2.metric("🟠 Medium", s.get("flags_medium", 0))
                m3.metric("Papers audited", s.get("papers_audited", 0))
                m4.metric("Unverified evidence", s.get("evidence_unverified", 0))
                if r["overall_status"] == "blocked":
                    st.error("⛔ BLOCKED — resolve high-severity flags before "
                             "the pipeline can proceed.")
                for fl in r["flags"]:
                    with st.container(border=True):
                        st.markdown(f"{status_badge(fl['severity'])} · "
                                    f"`{fl['category']}` · {fl['status']}")
                        st.write(fl.get("claim_text"))
                        if fl.get("expected"):
                            st.caption(f"Expected: {fl['expected']}")
                        if fl.get("found"):
                            st.caption(f"Found: {fl['found']}")
                        if fl.get("passage_text"):
                            st.blockquote(fl["passage_text"][:400])
                        meta = f"p. **{fl.get('page_number', '?')}**"
                        if fl.get("source_link"):
                            st.markdown(f"{meta} · "
                                        f"{source_link_md('🔗 source', fl['source_link'])}")
                        else:
                            st.markdown(meta)
                        if fl["status"] == "open" and st.button(
                                "✔ Mark resolved", key=f"flr_{fl['id']}"):
                            api_post(f"/audit/flags/{fl['id']}/resolve")
                            st.rerun()

        st.subheader("Numerical consistency check")
        papers = api_get("/papers", params={"project_id": pid, "limit": 200}) or []
        if papers:
            popts = {f"{p['title'][:60]}": p["id"] for p in papers}
            with st.form("numcheck"):
                ps = st.selectbox("Paper", list(popts.keys()))
                lab = st.text_input("Label", placeholder="e.g. Study 1 sample")
                met = st.text_input("Metric", placeholder="e.g. sample size n")
                val = st.text_input("Reported value", placeholder="e.g. 250")
                loc = st.text_input("Location", placeholder="e.g. Table 2")
                go = st.form_submit_button("Check against extraction")
            if go and val:
                evs = api_get("/extraction/evidence",
                              params={"paper_id": popts[ps], "limit": 50}) or []
                src = [{"paper_id": popts[ps],
                        "sample_size": e.get("sample_size"),
                        "paragraph_quote": e.get("paragraph_quote"),
                        "page_number": e.get("page_number"),
                        "source_url": e.get("source_url")} for e in evs]
                out = api_post("/audit/numerics",
                               json={"reported_table": [
                                   {"label": lab, "metric": met, "value": val,
                                    "paper_id": popts[ps], "location": loc}],
                                     "source_evidence": src})
                if out:
                    st.write(f"Checked **{out['checked']}**, matched "
                             f"**{out['matched']}**.")
                    for fl in out["flags"]:
                        st.warning(f"{status_badge(fl['severity'])} "
                                   f"`{fl['category']}` — {fl['claim_text']}")

# ---------------------------------------------------------------------------
# TAB 8 — Analysis (Agent 5)
# ---------------------------------------------------------------------------
with tab8:
    st.header("📊 Reproducible Analysis — Agent 5")
    st.caption("Every number is computed from your uploaded data. The exact "
               "reproduction script is saved with each run.")
    projects = api_get("/memory/projects") or []
    proj_opts = {p["name"]: p["id"] for p in projects}
    psel = st.selectbox("Project", list(proj_opts.keys()), key="an_proj") \
        if proj_opts else None

    def _analysis_config_form(key_prefix: str):
        atype = st.selectbox(
            "Analysis type",
            ["descriptive", "ttest", "chi2", "correlation", "regression"],
            key=f"{key_prefix}_type")
        cfg: dict = {}
        if atype == "ttest":
            cfg["group_col"] = st.text_input("Group column", key=f"{key_prefix}_g")
            cfg["value_col"] = st.text_input("Value column", key=f"{key_prefix}_v")
        elif atype == "chi2":
            cfg["col1"] = st.text_input("Column 1", key=f"{key_prefix}_c1")
            cfg["col2"] = st.text_input("Column 2", key=f"{key_prefix}_c2")
        elif atype == "correlation":
            cols = st.text_input("Columns (comma-separated)", key=f"{key_prefix}_cs")
            cfg["cols"] = [c.strip() for c in cols.split(",") if c.strip()]
        elif atype == "regression":
            cfg["outcome"] = st.text_input("Outcome column", key=f"{key_prefix}_o")
            preds = st.text_input("Predictors (comma-separated)", key=f"{key_prefix}_p")
            cfg["predictors"] = [c.strip() for c in preds.split(",") if c.strip()]
        return atype, cfg

    if psel:
        pid = proj_opts[psel]
        st.subheader("New analysis run")
        up = st.file_uploader("Dataset (CSV)", type=["csv"], key="an_file")
        atype, cfg = _analysis_config_form("an")
        if st.button("▶ Run analysis") and up is not None:
            import json as _json
            with st.spinner("Agent 5 cleaning + analyzing…"):
                res = api_post("/analysis/runs",
                               files={"file": (up.name, up.getvalue(), "text/csv")},
                               data={"project_id": pid, "analysis_type": atype,
                                     "config": _json.dumps(cfg)})
            if res:
                st.success(f"Run complete: {res['cleaning_report'].get('n_rows_clean')} clean rows.")
                st.rerun()

        st.subheader("Analysis runs")
        runs = api_get("/analysis/runs", params={"project_id": pid}) or []
        for r in runs:
            with st.container(border=True):
                st.markdown(f"**{r['dataset_name']}** · `{r['analysis_type']}` · "
                            f"{status_badge(r['approval_status'])}")
                st.caption(f"checksum `{str(r.get('dataset_checksum'))[:16]}…` · "
                           f"run `{r['id'][:8]}`")
                with st.expander("Cleaning report"):
                    st.json(r["cleaning_report"])
                with st.expander("Results"):
                    st.json(r["results"])
                c1, c2, c3 = st.columns(3)
                try:
                    sresp = requests.get(
                        api_url(f"/analysis/runs/{r['id']}/script"), timeout=60)
                    if sresp.ok:
                        c1.download_button("⬇ Repro script", sresp.text,
                                           file_name=f"run_{r['id'][:8]}.py",
                                           key=f"scr_{r['id']}")
                except Exception:
                    pass
                if c2.button("✅ Approve", key=f"anok_{r['id']}"):
                    api_post(f"/analysis/runs/{r['id']}/review",
                             json={"approved": True})
                    st.rerun()
                if c3.button("❌ Reject", key=f"anrj_{r['id']}"):
                    api_post(f"/analysis/runs/{r['id']}/review",
                             json={"approved": False})
                    st.rerun()

        st.subheader("Advance pipeline")
        tasks_a = [t for t in (api_get("/supervisor/tasks") or [])
                   if t["status"] in ("needs_protocol_approval", "verified")]
        if tasks_a:
            topts = {f"{t['query'][:50]}… (`{t['task_id'][:8]}`)": t["task_id"]
                     for t in tasks_a}
            tsel = st.selectbox("Task", list(topts.keys()), key="adv_an_task")
            up2 = st.file_uploader("Dataset (CSV) for pipeline", type=["csv"],
                                   key="adv_an_file")
            atype2, cfg2 = _analysis_config_form("advan")
            if st.button("📊 Advance to analysis (Agent 5)") and up2 is not None:
                import json as _json
                with st.spinner("Agent 5 analyzing…"):
                    api_post(
                        f"/supervisor/tasks/{topts[tsel]}/advance-to-analysis",
                        files={"file": (up2.name, up2.getvalue(), "text/csv")},
                        data={"analysis_type": atype2,
                              "config": _json.dumps(cfg2)})
                st.rerun()

# ---------------------------------------------------------------------------
# TAB 9 — Writing (Agent 6)
# ---------------------------------------------------------------------------
with tab9:
    st.header("✍️ Manuscript Writing — Agent 6")
    st.caption("Drafts use ONLY your evidence, approved analyses and protocols. "
               "Nothing is invented; every claim keeps its source link.")
    projects = api_get("/memory/projects") or []
    proj_opts = {p["name"]: p["id"] for p in projects}
    psel = st.selectbox("Project", list(proj_opts.keys()), key="wr_proj") \
        if proj_opts else None

    with st.expander("🎨 Style profile (from your own texts)"):
        up = st.file_uploader(
            "Upload your papers (PDF or TXT — multiple allowed)",
            type=["pdf", "txt"], accept_multiple_files=True,
            key="wr_style_files")
        corpus = st.text_area("Or paste 1+ of your own texts (blank line between texts)",
                              height=150, key="wr_corpus")
        if st.button("Build style profile"):
            texts = []
            if up:
                for f in up:
                    try:
                        if f.name.lower().endswith(".pdf"):
                            import fitz
                            doc = fitz.open(stream=f.read(), filetype="pdf")
                            t = "\n".join(p.get_text() for p in doc)
                        else:
                            t = f.read().decode("utf-8", errors="ignore")
                        if t.strip():
                            texts.append(t.strip())
                    except Exception as e:
                        st.warning(f"Could not read {f.name}: {e}")
            if corpus.strip():
                texts += [t.strip() for t in corpus.split("\n\n") if t.strip()]
            if not texts:
                st.warning("Upload at least one file or paste some text first.")
                st.stop()
            prof = api_post("/writing/style-profile", json={"texts": texts})
            if prof:
                st.session_state["style_profile"] = prof
                st.json(prof)
                if psel:
                    api_post(f"/memory/projects/{proj_opts[psel]}/decisions",
                             json={"decision_type": "style_profile",
                                   "details": prof, "user_approved": True})
                    st.success("Style profile saved to this project — it will "
                               "be used for every manuscript drafted here.")
    style_prof = st.session_state.get("style_profile")
    if not style_prof and psel:
        saved = api_get(f"/memory/projects/{proj_opts[psel]}/decisions/latest",
                        params={"decision_type": "style_profile"})
        if saved and saved.get("decision"):
            style_prof = saved["decision"]["details"]
            st.session_state["style_profile"] = style_prof
            st.info("Using the style profile saved for this project.")

    if psel:
        pid = proj_opts[psel]
        st.subheader("Draft manuscript")
        protos = api_get("/design/protocols", params={"project_id": pid}) or []
        popts = {"(none)": None, **{p["title"][:60]: p["id"] for p in protos}}
        psel2 = st.selectbox("Approved protocol (optional)", list(popts.keys()))
        title = st.text_input("Manuscript title", key="wr_title")
        secs = st.multiselect("Sections",
                              ["abstract", "introduction", "methods", "results",
                               "discussion", "conclusion"],
                              default=["abstract", "introduction", "methods",
                                       "results", "discussion", "conclusion"])
        if st.button("✍️ Draft manuscript") and secs:
            with st.spinner("Agent 6 drafting (evidence-grounded)…"):
                ms = api_post("/writing/manuscripts",
                              json={"project_id": pid,
                                    "protocol_id": popts[psel2],
                                    "title": title or None, "sections": secs,
                                    "style_profile": st.session_state.get("style_profile")})
            if ms:
                st.success(f"Drafted {len(ms['sections'])} sections.")
                st.rerun()

        st.subheader("Manuscripts")
        mss = api_get("/writing/manuscripts", params={"project_id": pid}) or []
        for m in mss:
            with st.container(border=True):
                st.markdown(f"**{m['title']}** · {status_badge(m['approval_status'])}")
                for sname, stext in m["sections"].items():
                    with st.expander(sname.capitalize()):
                        st.markdown(stext)
                with st.expander("Evidence map"):
                    for item in m["evidence_map"]:
                        links = ", ".join(
                            f"[source]({u})" for u in item.get("citations", []) if u)
                        st.markdown(f"**{item['section']}**: {links or '—'}")
                c1, c2, c3 = st.columns(3)
                if c1.button("📄 Export .docx", key=f"docx_{m['id']}"):
                    exp = api_post(f"/writing/manuscripts/{m['id']}/docx")
                    if exp:
                        st.success("Exported — download:")
                        st.link_button("⬇ Download .docx",
                                       api_url(f"/writing/manuscripts/{m['id']}/docx"))
                if c2.button("✅ Approve", key=f"wrok_{m['id']}"):
                    api_post(f"/writing/manuscripts/{m['id']}/review",
                             params={"approved": True})
                    st.rerun()
                if c3.button("❌ Reject", key=f"wrrj_{m['id']}"):
                    api_post(f"/writing/manuscripts/{m['id']}/review",
                             params={"approved": False})
                    st.rerun()

        st.subheader("Advance pipeline")
        tasks_w = [t for t in (api_get("/supervisor/tasks") or [])
                   if t["status"] in ("needs_analysis_approval",
                                      "needs_manuscript_approval")]
        if tasks_w:
            topts = {f"{t['query'][:50]}… `{t['task_id'][:8]}` [{t['status']}]": t
                     for t in tasks_w}
            tsel = st.selectbox("Task", list(topts.keys()), key="adv_wr_task")
            t = topts[tsel]
            if t["status"] == "needs_analysis_approval":
                if st.button("✍️ Advance to writing (Agent 6)"):
                    api_post(f"/supervisor/tasks/{t['task_id']}/advance-to-writing",
                             json={})
                    st.rerun()
            else:
                if st.button("🛡️ Final audit (Agent 7)"):
                    api_post(f"/supervisor/tasks/{t['task_id']}/advance-to-final-audit")
                    st.rerun()

# ---------------------------------------------------------------------------
# TAB 10 — Systematic Review (Agent 10)
# ---------------------------------------------------------------------------
with tab10:
    st.header("📚 Systematic Review — Agent 10")
    st.caption("PRISMA 2020-aligned: logged searches, staged screening, "
               "documented effect conversions, RoB, meta-analysis.")
    projects = api_get("/memory/projects") or []
    proj_opts = {p["name"]: p["id"] for p in projects}
    psel = st.selectbox("Project", list(proj_opts.keys()), key="rv_proj") \
        if proj_opts else None

    if psel:
        pid = proj_opts[psel]
        with st.expander("➕ New review project"):
            with st.form("new_review"):
                title = st.text_input("Review title")
                c1, c2 = st.columns(2)
                pop = c1.text_input("PICO: Population")
                intr = c2.text_input("PICO: Intervention")
                comp = c1.text_input("PICO: Comparison")
                outc = c2.text_input("PICO: Outcome")
                incl = st.text_area("Inclusion criteria (one per line)")
                excl = st.text_area("Exclusion criteria (one per line)")
                if st.form_submit_button("Create") and title:
                    r = api_post("/reviews",
                                 json={"project_id": pid, "title": title,
                                       "pico": {"population": pop,
                                                "intervention": intr,
                                                "comparison": comp,
                                                "outcome": outc},
                                       "inclusion_criteria": [x.strip() for x in incl.split("\n") if x.strip()],
                                       "exclusion_criteria": [x.strip() for x in excl.split("\n") if x.strip()]})
                    if r:
                        st.success("Review created.")
                        st.rerun()

        reviews = api_get("/reviews", params={"project_id": pid}) or []
        ropts = {r["title"][:60]: r["id"] for r in reviews}
        rsel = st.selectbox("Review", list(ropts.keys()), key="rv_sel") if ropts else None
        if rsel:
            rid = ropts[rsel]
            rev = api_get(f"/reviews/{rid}")
            if rev:
                with st.expander(f"🔎 Search log ({len(rev['search_log'])})"):
                    with st.form("add_search"):
                        s1, s2, s3 = st.columns(3)
                        src = s1.text_input("Source")
                        qry = s2.text_input("Exact query")
                        hits = s3.number_input("Hits", min_value=0, step=1)
                        if st.form_submit_button("Log search") and src and qry:
                            api_post(f"/reviews/{rid}/searches",
                                     params={"source": src, "query": qry, "hits": int(hits)})
                            st.rerun()
                    for s in rev["search_log"]:
                        st.caption(f"`{s['date']}` · **{s['source']}** · "
                                   f"`{s['query']}` → {s['hits']} hits")
                with st.expander(f"🧪 Screening ({len(rev['screening'])})"):
                    with st.form("add_screen"):
                        t1 = st.text_input("Record title")
                        c1, c2, c3 = st.columns(3)
                        stage = c1.selectbox("Stage", ["title_abstract", "full_text"])
                        dec = c2.selectbox("Decision", ["include", "exclude", "maybe"])
                        reason = c3.text_input("Reason (required if excluded)")
                        if st.form_submit_button("Record") and t1:
                            api_post(f"/reviews/{rid}/screening",
                                     params={"title": t1, "decision": dec,
                                             "reason": reason, "stage": stage})
                            st.rerun()
                    for s in rev["screening"][-10:]:
                        st.caption(f"{status_badge(s['decision'])} · {s['stage']} · "
                                   f"{s['title'][:70]}" +
                                   (f" — _{s['reason']}_" if s.get("reason") else ""))
                with st.expander("📊 PRISMA flow"):
                    if st.button("Compute PRISMA", key=f"prisma_{rid}"):
                        pr = api_post(f"/reviews/{rid}/prisma")
                        if pr:
                            st.rerun()
                    pr = rev.get("prisma") or {}
                    if pr:
                        st.json({k: v for k, v in pr.items()
                                 if k in ("identification", "screening",
                                          "eligibility", "included")})
                with st.expander(f"📈 Effects ({len(rev['extractions'])})"):
                    with st.form("add_effect"):
                        c1, c2, c3 = st.columns(3)
                        et = c1.selectbox("Type", ["d", "r", "or", "logor"])
                        val = c2.number_input("Value", format="%.4f")
                        se = c3.number_input("SE (optional)", format="%.4f")
                        outcome = st.text_input("Outcome label")
                        if st.form_submit_button("Extract") :
                            api_post(f"/reviews/{rid}/effects",
                                     params={"paper_id": "manual", "effect_type": et,
                                             "value": float(val),
                                             "se": float(se) if se else None,
                                             "outcome": outcome})
                            st.rerun()
                    for e in rev["extractions"]:
                        std = e["standardized"]
                        st.caption(f"{e.get('outcome', '')}: {e['original']['type']}="
                                   f"{e['original']['value']} → {std['type']}="
                                   f"{std['value']:.3f} (SE {std['se']}) · "
                                   f"{std['conversion_formula']}")
                with st.expander(f"⚖️ Risk of Bias ({len(rev['rob'])})"):
                    with st.form("add_rob"):
                        who = st.text_input("Paper label")
                        cols = st.columns(5)
                        doms = {}
                        for col, d in zip(cols, ["randomization", "deviations",
                                                "missing_data", "measurement",
                                                "reporting"]):
                            doms[d] = col.selectbox(d, ["low", "some_concerns",
                                                       "high"], key=f"rob_{rid}_{d}")
                        if st.form_submit_button("Assess") and who:
                            res = api_post(f"/reviews/{rid}/rob",
                                           json={"paper_id": who, "domains": doms})
                            if res:
                                st.rerun()
                    for rb in rev["rob"]:
                        st.caption(f"{rb['paper_id']}: overall **{rb['overall']}** · " +
                                   ", ".join(f"{k}={v}" for k, v in rb["domains"].items()))
                with st.expander("🧮 Meta-analysis"):
                    if st.button("Run meta-analysis", key=f"meta_{rid}"):
                        meta = api_post(f"/reviews/{rid}/meta")
                        if meta:
                            st.rerun()
                    meta = rev.get("meta_results")
                    if meta:
                        st.metric("Pooled (random)", meta["random_effects"]["pooled"])
                        st.caption(f"95% CI {meta['random_effects']['ci95']} · "
                                   f"I² = {meta['heterogeneity']['I2']}")
                        for s in meta["studies"]:
                            link = f" ([source]({s['source_link']}))" if s.get("source_link") else ""
                            st.caption(f"{s['paper_id']}: g={s['yi']} "
                                       f"(SE {s['sei']}) · {s['weight_pct']}%{link}")

# ---------------------------------------------------------------------------
# TAB 11 — Journal (Agent 11)
# ---------------------------------------------------------------------------
with tab11:
    st.header("📰 Journal Selection — Agent 11")
    st.caption("Scope matching is date-stamped — always verify on the official "
               "journal page before submitting. This system never submits.")
    projects = api_get("/memory/projects") or []
    proj_opts = {p["name"]: p["id"] for p in projects}
    psel = st.selectbox("Project", list(proj_opts.keys()), key="jn_proj") \
        if proj_opts else None

    if psel:
        pid = proj_opts[psel]
        kw = st.text_input("Manuscript keywords (comma-separated)", key="jn_kw")
        if st.button("🎯 Match journals") and kw.strip():
            kws = [k.strip() for k in kw.split(",") if k.strip()]
            with st.spinner("Agent 11 matching…"):
                ms = api_post("/journals/match",
                              json={"project_id": pid, "keywords": kws, "top_n": 5})
            if ms:
                st.rerun()
        matches = api_get("/journals/matches", params={"project_id": pid}) or []
        for m in matches:
            with st.container(border=True):
                st.markdown(f"**{m['journal_name']}** — match {m['match_score']:.0%}")
                st.caption(m.get("scope_summary"))
                req = m.get("requirements", {})
                st.caption(f"Types: {req.get('article_types')} · "
                           f"Limit: {req.get('word_limit')} · "
                           f"Data: {req.get('data_policy')}")
                st.caption(f"APC: {m.get('apc_info')} (checked {m.get('checked_date')})")
                if m.get("source_link"):
                    st.link_button("🔗 Official journal page", m["source_link"])

        st.subheader("Reviewer response matrix")
        if "rev_comments" not in st.session_state:
            st.session_state["rev_comments"] = []
        with st.form("add_comment"):
            who = st.text_input("Reviewer (e.g. Reviewer 1)")
            cmt = st.text_area("Comment")
            if st.form_submit_button("Add comment") and cmt.strip():
                st.session_state["rev_comments"].append(
                    {"reviewer": who or None, "comment": cmt.strip()})
                st.rerun()
        if st.session_state["rev_comments"]:
            st.write(f"{len(st.session_state['rev_comments'])} comment(s) queued.")
            if st.button("Build response matrix"):
                rows = api_post("/journals/responses",
                                json={"project_id": pid,
                                      "comments": st.session_state["rev_comments"]})
                if rows:
                    st.session_state["rev_comments"] = []
                    st.rerun()
        rows = api_get("/journals/responses", params={"project_id": pid}) or []
        for r in rows:
            with st.container(border=True):
                st.markdown(f"**{r.get('reviewer') or 'Reviewer'}** · "
                            f"{status_badge(r['status'])}")
                st.blockquote(r["comment"])
                resp = st.text_area("Your response", value=r.get("response", ""),
                                    key=f"rr_{r['id']}")
                chg = st.text_area("Manuscript change", value=r.get("manuscript_change", ""),
                                   key=f"rc_{r['id']}")
                stt = st.selectbox("Status",
                                   ["pending", "addressed", "rebutted", "wont_fix"],
                                   index=["pending", "addressed", "rebutted",
                                          "wont_fix"].index(r["status"])
                                   if r["status"] in ["pending", "addressed",
                                                      "rebutted", "wont_fix"] else 0,
                                   key=f"rs_{r['id']}")
                if st.button("💾 Save", key=f"rsv_{r['id']}"):
                    api_post(f"/journals/responses/{r['id']}",
                             json={"response": resp, "manuscript_change": chg,
                                   "status": stt})
                    st.rerun()

# ---------------------------------------------------------------------------
# TAB 12 — Intelligence (Agent 12)
# ---------------------------------------------------------------------------
with tab12:
    st.header("📡 Research Intelligence — Agent 12")
    st.caption("Watches discover; alerts never modify approved plans automatically.")
    projects = api_get("/memory/projects") or []
    proj_opts = {p["name"]: p["id"] for p in projects}
    psel = st.selectbox("Project", list(proj_opts.keys()), key="in_proj") \
        if proj_opts else None

    if psel:
        pid = proj_opts[psel]
        with st.expander("➕ New watch"):
            with st.form("new_watch"):
                name = st.text_input("Watch name")
                query = st.text_input("Query")
                c1, c2 = st.columns(2)
                s2s = c1.checkbox("Semantic Scholar", value=True)
                pm = c2.checkbox("PubMed", value=True)
                if st.form_submit_button("Create") and name and query:
                    srcs = (["semantic_scholar"] if s2s else []) + \
                           (["pubmed"] if pm else [])
                    w = api_post("/intelligence/watches",
                                 json={"project_id": pid, "name": name,
                                       "query": query, "sources": srcs or ["semantic_scholar"]})
                    if w:
                        st.rerun()
        watches = api_get("/intelligence/watches", params={"project_id": pid}) or []
        for w in watches:
            with st.container(border=True):
                c1, c2 = st.columns([4, 1])
                c1.markdown(f"**{w['name']}** · `{w['query']}`")
                c1.caption(f"last run: {w.get('last_run') or 'never'} · "
                           f"sources: {', '.join(w.get('sources', []))}")
                if c2.button("▶ Run", key=f"wr_{w['id']}"):
                    with st.spinner("Agent 12 monitoring…"):
                        out = api_post(f"/intelligence/watches/{w['id']}/run")
                    if out:
                        st.success(f"{out['new_papers']} new papers, "
                                   f"{len(out['alerts'])} alerts.")
                        st.rerun()
        st.subheader("Alerts")
        show_ack = st.checkbox("Show acknowledged", value=False)
        alerts = api_get("/intelligence/alerts",
                         params={"project_id": pid,
                                 "acknowledged": show_ack if show_ack else False}) or []
        if not alerts:
            st.info("No alerts.")
        for a in alerts:
            with st.container(border=True):
                icon = "🚨" if a["alert_type"] == "novelty_challenge" else "📰"
                st.markdown(f"{icon} **{a['title']}** · "
                            f"{status_badge(a['alert_type'])}")
                st.caption(f"relevance {a.get('relevance_score')}")
                st.write(a.get("reason"))
                if a.get("source_link"):
                    st.link_button("🔗 Source", a["source_link"])
                if not a["acknowledged"] and st.button(
                        "✔ Acknowledge", key=f"ack_{a['id']}"):
                    api_post(f"/intelligence/alerts/{a['id']}/acknowledge")
                    st.rerun()
