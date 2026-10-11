"""Supervisor / Orchestrator.

Receives research tasks, builds the execution pipeline, routes work
sequentially to Agent 1 (Discovery) -> Agent 2 (Extraction) -> Agent 8
(Project Memory), and enforces mandatory approval gates:

  * Gate 1 — missing PDF: task pauses as `needs_pdf_upload` until the
    researcher uploads the file, then resumes via `resume_after_upload`.
  * Gate 2 — critical evidence extracted: task pauses as `needs_approval`
    until the researcher approves/rejects via `approve_task`.

MANDATORY OUTPUT RULE: every task execution result keeps an immutable
provenance trail — task_id + timestamped execution_log lines that link to
source URLs / file paths, and every Evidence row carries page_number +
source_url. Nothing is ever emitted as an unanchored claim.

PHASE 2 PIPELINE (gated):
  Discovery (Agent 1) -> Extraction (Agent 2) -> [evidence approval]
  -> Gap Discovery & Challenge (Agent 3) -> [gap approval]
  -> Research Design (Agent 4) -> [protocol approval]
  -> Audit (Agent 7) -> verified | blocked
PHASE 3 PIPELINE (gated, continues after protocol approval):
  -> Analysis (Agent 5) -> [analysis approval]
  -> Writing (Agent 6) -> [manuscript approval]
  -> Final Audit (Agent 7) -> verified | blocked
Advance between phases via advance_to_gaps / advance_to_design /
advance_to_audit / advance_to_analysis / advance_to_writing /
advance_to_final_audit. HIGH-severity audit flags set the task to 'blocked'.
Agents 10/11/12 run standalone (routers + UI) outside the linear pipeline.
"""
import importlib.metadata as _md
import logging
from typing import List

from sqlalchemy.orm import Session

from app.agents import agent2_extraction as agent2
from app.agents import agent3_gap_discovery as agent3
from app.agents import agent4_design as agent4
from app.agents import agent5_analysis as agent5
from app.agents import agent6_writing as agent6
from app.agents import agent7_verification as agent7
from app.agents import agent8_memory as agent8
from app.agents.agent1_discovery import run_discovery
from app.core.config import PDF_DIR, settings
from app.models.schemas import Task
from app.services import evidence_service, paper_service, task_service

log = logging.getLogger(__name__)


def _tool_versions() -> dict:
    versions = {}
    for pkg in ("fastapi", "requests", "httpx", "pymupdf", "chromadb",
                "sqlalchemy", "streamlit"):
        try:
            versions[pkg] = _md.version(pkg)
        except Exception:
            versions[pkg] = "not-installed"
    return versions


class Supervisor:
    """Orchestrates the Phase-1 pipeline for one research task."""

    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------
    # Task lifecycle
    # ------------------------------------------------------------------

    def create_task(self, query: str, project_id: str | None = None,
                    sources: List[str] | None = None,
                    limit: int | None = None) -> Task:
        task = task_service.create_task(
            self.db, query,
            project_id=project_id,
            sources=sources or settings.discovery_sources,
            limit=limit or settings.discovery_default_limit,
        )
        task_service.append_log(
            self.db, task,
            f"Supervisor accepted task. Tool versions: {_tool_versions()}",
        )
        return task

    def run_task(self, task_id: str) -> Task:
        """Execute the full pipeline: discovery -> [gate] -> extraction -> [gate]."""
        task = self._get(task_id)
        if task.status not in ("queued", "failed"):
            raise ValueError(
                f"Task {task_id} cannot run from status={task.status!r}"
            )
        task_service.start_task(self.db, task)

        try:
            papers = self._phase_discovery(task)
            with_pdf = [p for p in papers if p.get("pdf_path")]

            # ---- Gate 1: PDF availability ----
            if not with_pdf:
                return self._pause_for_upload(task, papers)

            # ---- Phase 2: extraction (only papers with a real PDF) ----
            evidence_total = self._phase_extraction(task, with_pdf)

            # ---- Gate 2: researcher approval (mandatory) ----
            return self._pause_for_approval(task, papers, evidence_total)
        except Exception as exc:
            log.exception("Supervisor task %s failed", task_id)
            return task_service.finish_task(
                self.db, task, "failed", error=str(exc)[:2000]
            )

    def resume_after_upload(self, task_id: str) -> Task:
        """Resume a `needs_pdf_upload` task once PDFs were uploaded."""
        task = self._get(task_id)
        if task.status != "needs_pdf_upload":
            raise ValueError(
                f"Task {task_id} is not waiting for uploads "
                f"(status={task.status!r})"
            )
        project = self._ensure_project(task)
        ready = paper_service.list_papers(self.db, limit=1000)
        ready = [p for p in ready
                 if p.pdf_path and p.status == "pdf_acquired"
                 and (p.task_id == task_id
                      or (p.task_id is None and p.project_id == project.id))]
        if not ready:
            task_service.append_log(
                self.db, task,
                "Resume requested but no uploaded PDFs found for this task "
                "or its project. Still waiting.",
            )
            return task

        task_service.start_task(self.db, task)
        task_service.append_log(
            self.db, task,
            f"Resuming after upload: {len(ready)} PDF(s) now available.",
        )
        try:
            paper_dicts = [
                {"doi": p.doi, "title": p.title, "pdf_path": p.pdf_path,
                 "source_link": p.source_link, "source_api": p.source_api,
                 "_paper_id": p.id}
                for p in ready
            ]
            evidence_total = self._phase_extraction(task, paper_dicts)
            return self._pause_for_approval(task, paper_dicts, evidence_total)
        except Exception as exc:
            log.exception("Resume failed for task %s", task_id)
            return task_service.finish_task(
                self.db, task, "failed", error=str(exc)[:2000]
            )

    def approve_task(self, task_id: str, approved: bool, note: str = "") -> Task:
        """Researcher decision at Gate 2. Approved -> Agent 8 memory -> verified."""
        task = self._get(task_id)
        if task.status != "needs_approval":
            raise ValueError(
                f"Task {task_id} is not awaiting approval "
                f"(status={task.status!r})"
            )
        if not approved:
            task_service.append_log(
                self.db, task, f"Researcher REJECTED the evidence. Note: {note}"
            )
            return task_service.finish_task(
                self.db, task, "failed",
                error=f"Rejected by researcher: {note or 'no reason given'}",
            )

        # ---- Agent 8: persist the approved outcome to project memory ----
        project = self._ensure_project(task)
        evidence_rows = evidence_service.list_evidence(
            self.db, project_id=project.id, limit=1000
        )
        task_evidence = [e for e in evidence_rows if e.task_id == task.task_id]
        first_link = task_evidence[0].source_url if task_evidence else None

        agent8.save_project_decision(
            self.db,
            project_id=project.id,
            decision_type="task_evidence_approved",
            details={
                "query": task.query,
                "evidence_items_approved": len(task_evidence),
                "researcher_note": note,
            },
            user_approved=True,
            task_id=task.task_id,
            evidence_link=first_link,
        )
        agent8.log_activity_feed(
            self.db, project.id, "supervisor",
            f"Task {task.task_id} approved by researcher "
            f"({len(task_evidence)} evidence items) -> status=verified.",
            evidence_link=first_link, task_id=task.task_id,
        )
        task_service.append_log(
            self.db, task,
            f"Approved by researcher. Decision persisted to project "
            f"'{project.name}' (Agent 8).",
        )
        return task_service.finish_task(self.db, task, "verified")

    def retry_task(self, task_id: str) -> Task:
        """Reset a failed task back to queued and run it again."""
        task = self._get(task_id)
        if task.status != "failed":
            raise ValueError(
                f"Only failed tasks can be retried (status={task.status!r})"
            )
        task_service.set_status(self.db, task, "queued", "Task reset for retry.")
        return self.run_task(task_id)

    # ------------------------------------------------------------------
    # Phase 2: gated advancement (Agent 3 -> Agent 4 -> Agent 7)
    # ------------------------------------------------------------------

    def advance_to_gaps(self, task_id: str) -> Task:
        """Continue a verified (evidence-approved) task into Agent 3."""
        task = self._get(task_id)
        if task.status != "verified":
            raise ValueError(
                f"Task {task_id} must be 'verified' (evidence approved) before "
                f"gap discovery (status={task.status!r})."
            )
        task_service.start_task(self.db, task)
        task_service.append_log(
            self.db, task, "Phase 2: advancing to Agent 3 (gap discovery).")
        try:
            briefs = self._phase_gap_discovery(task)
            return self._pause_for_gap_approval(task, briefs)
        except Exception as exc:
            log.exception("Gap discovery failed for task %s", task_id)
            return task_service.finish_task(
                self.db, task, "failed", error=str(exc)[:2000])

    def advance_to_design(self, task_id: str,
                          design_type: str = "mixed_methods") -> Task:
        """Build Agent 4 protocols for researcher-approved gaps."""
        from app.models.schemas import DESIGN_TYPES
        task = self._get(task_id)
        if task.status != "needs_gap_approval":
            raise ValueError(
                f"Task {task_id} is not awaiting gap decisions "
                f"(status={task.status!r}).")
        if design_type not in DESIGN_TYPES:
            raise ValueError(f"Unknown design_type: {design_type!r}")
        approved = [g for g in agent3.list_gaps(self.db, task_id=task.task_id)
                    if g.researcher_decision == "approved"]
        if not approved:
            raise ValueError(
                "No gaps approved yet — approve at least one gap brief first.")
        task_service.start_task(self.db, task)
        task_service.append_log(
            self.db, task,
            f"Phase 2: advancing to Agent 4 (design) for {len(approved)} "
            f"approved gap(s), design_type={design_type}.")
        try:
            protocols = self._phase_design(task, approved, design_type)
            return self._pause_for_protocol_approval(task, protocols)
        except Exception as exc:
            log.exception("Protocol design failed for task %s", task_id)
            return task_service.finish_task(
                self.db, task, "failed", error=str(exc)[:2000])

    def advance_to_audit(self, task_id: str) -> Task:
        """Run Agent 7 audit on approved protocols. HIGH severity -> blocked."""
        task = self._get(task_id)
        if task.status != "needs_protocol_approval":
            raise ValueError(
                f"Task {task_id} is not awaiting protocol decisions "
                f"(status={task.status!r}).")
        approved = [p for p in agent4.list_protocols(self.db, task_id=task.task_id)
                    if p.approval_status == "approved"]
        if not approved:
            raise ValueError(
                "No protocols approved yet — approve at least one protocol first.")
        task_service.start_task(self.db, task)
        task_service.append_log(
            self.db, task,
            f"Phase 2: advancing to Agent 7 (audit) for {len(approved)} "
            f"approved protocol(s).")
        try:
            return self._phase_audit(task, approved)
        except Exception as exc:
            log.exception("Audit failed for task %s", task_id)
            return task_service.finish_task(
                self.db, task, "failed", error=str(exc)[:2000])

    # ------------------------------------------------------------------
    # Phase 3: gated advancement (Agent 5 -> Agent 6 -> final Agent 7)
    # ------------------------------------------------------------------

    def advance_to_analysis(self, task_id: str, dataset_path: str,
                            dataset_name: str, analysis_type: str,
                            config: dict) -> Task:
        """Run Agent 5 on an uploaded dataset for approved protocol(s)."""
        task = self._get(task_id)
        if task.status != "needs_protocol_approval":
            raise ValueError(
                f"Task {task_id} is not awaiting protocol decisions "
                f"(status={task.status!r}).")
        approved = [p for p in agent4.list_protocols(self.db, task_id=task.task_id)
                    if p.approval_status == "approved"]
        if not approved:
            raise ValueError(
                "No protocols approved yet — approve at least one protocol first.")
        project = self._ensure_project(task)
        task_service.start_task(self.db, task)
        task_service.append_log(
            self.db, task,
            f"Phase 3: advancing to Agent 5 (analysis): {analysis_type} on "
            f"{dataset_name} for protocol {approved[0].id[:8]}.")
        try:
            run = agent5.create_analysis_run(
                self.db, project.id, dataset_path, dataset_name,
                analysis_type, config, task_id=task.task_id,
                protocol_id=approved[0].id)
            task_service.append_log(
                self.db, task,
                f"Agent 5 finished run {run['run_id'][:8]}: "
                f"{run['cleaning_report'].get('n_rows_clean')} clean rows; "
                f"reproduction script at {run['script_path']}. "
                f"GATE: pausing as needs_analysis_approval — researcher must "
                f"approve results before writing.")
            agent8.log_activity_feed(
                self.db, project.id, "agent5",
                f"Analysis run {run['run_id'][:8]} ({analysis_type}) awaiting "
                f"researcher approval.", task_id=task.task_id)
            return task_service.set_status(
                self.db, task, "needs_analysis_approval")
        except Exception as exc:
            log.exception("Analysis failed for task %s", task_id)
            return task_service.finish_task(
                self.db, task, "failed", error=str(exc)[:2000])

    def advance_to_writing(self, task_id: str,
                           sections: List[str] | None = None) -> Task:
        """Draft a manuscript (Agent 6) from approved analysis + materials."""
        task = self._get(task_id)
        if task.status != "needs_analysis_approval":
            raise ValueError(
                f"Task {task_id} is not awaiting analysis decisions "
                f"(status={task.status!r}).")
        approved_runs = [r for r in agent5.list_runs(self.db, task_id=task.task_id)
                         if r.approval_status == "approved"]
        if not approved_runs:
            raise ValueError(
                "No analysis runs approved yet — approve at least one run first.")
        project = self._ensure_project(task)
        protocols = [p for p in agent4.list_protocols(self.db, task_id=task.task_id)
                     if p.approval_status == "approved"]
        protocol_id = protocols[0].id if protocols else None
        task_service.start_task(self.db, task)
        task_service.append_log(
            self.db, task,
            f"Phase 3: advancing to Agent 6 (writing) from "
            f"{len(approved_runs)} approved analysis run(s).")
        try:
            ms = agent6.draft_manuscript(
                self.db, project.id, task_id=task.task_id,
                protocol_id=protocol_id,
                title=f"Manuscript: {task.query[:80]}",
                sections=sections)
            task_service.append_log(
                self.db, task,
                f"Agent 6 drafted manuscript {ms['manuscript_id'][:8]} "
                f"({len(ms['sections'])} sections, evidence-grounded only). "
                f"GATE: pausing as needs_manuscript_approval.")
            agent8.log_activity_feed(
                self.db, project.id, "agent6",
                f"Manuscript {ms['manuscript_id'][:8]} drafted; awaiting "
                f"researcher approval.", task_id=task.task_id)
            return task_service.set_status(
                self.db, task, "needs_manuscript_approval")
        except Exception as exc:
            log.exception("Writing failed for task %s", task_id)
            return task_service.finish_task(
                self.db, task, "failed", error=str(exc)[:2000])

    def advance_to_final_audit(self, task_id: str) -> Task:
        """Final Agent 7 audit over approved manuscript(s)."""
        task = self._get(task_id)
        if task.status != "needs_manuscript_approval":
            raise ValueError(
                f"Task {task_id} is not awaiting manuscript decisions "
                f"(status={task.status!r}).")
        approved = [m for m in agent6.list_manuscripts(self.db, task_id=task.task_id)
                    if m.approval_status == "approved"]
        if not approved:
            raise ValueError(
                "No manuscripts approved yet — approve at least one draft first.")
        project = self._ensure_project(task)
        task_service.start_task(self.db, task)
        try:
            blocked = False
            for ms in approved:
                d = agent6.get_manuscript_dict(ms.id, self.db)
                manuscript_text = "\n\n".join(
                    f"## {k}\n{v}" for k, v in d["sections"].items())
                cited = sorted({c for item in d["evidence_map"]
                                for c in item.get("citations", [])})
                # evidence_map stores source links; map back to paper ids
                paper_ids = self._paper_ids_for_links(
                    [c for c in cited if c], project.id)
                report = agent7.generate_audit_report(
                    project.id, self.db, task_id=task.task_id,
                    protocol_id=ms.protocol_id,
                    manuscript_text=manuscript_text,
                    cited_paper_ids=paper_ids or None)
                s = report["summary"]
                task_service.append_log(
                    self.db, task,
                    f"Agent 7 final audit for manuscript {ms.id[:8]}: "
                    f"{report['overall_status']} ({s['flags_high']} high, "
                    f"{s['flags_medium']} medium).")
                if report["overall_status"] == "blocked":
                    blocked = True
            if blocked:
                task_service.append_log(
                    self.db, task,
                    "GATE: final audit BLOCKED on high-severity issues. "
                    "Task set to 'blocked'.")
                return task_service.set_status(self.db, task, "blocked")
            agent8.save_project_decision(
                self.db, project.id, "manuscript_audited",
                {"manuscripts": [m.id for m in approved]},
                user_approved=True, task_id=task.task_id, evidence_link=None)
            return task_service.finish_task(self.db, task, "verified")
        except Exception as exc:
            log.exception("Final audit failed for task %s", task_id)
            return task_service.finish_task(
                self.db, task, "failed", error=str(exc)[:2000])

    def _paper_ids_for_links(self, links: List[str], project_id: str) -> List[str]:
        """Map source links back to paper ids for the citation audit."""
        from app.models.schemas import Paper
        from sqlalchemy import select as _select
        papers = self.db.scalars(
            _select(Paper).where(Paper.project_id == project_id)).all()
        out = []
        for p in papers:
            if p.source_link and any(
                    (p.source_link in link) or (link in p.source_link)
                    for link in links):
                out.append(p.id)
        return out

    # ------------------------------------------------------------------
    # Pipeline phases (private)
    # ------------------------------------------------------------------

    def _get(self, task_id: str) -> Task:
        task = task_service.get_task(self.db, task_id)
        if task is None:
            raise ValueError(f"Unknown task_id: {task_id}")
        return task

    def _ensure_project(self, task: Task):
        if task.project_id:
            project = agent8.get_project(self.db, task.project_id)
            if project:
                return project
        project = agent8.get_or_create_project(
            self.db, "Default workspace",
            description="Auto-created workspace for ad-hoc research tasks.",
        )
        task.project_id = project.id
        self.db.commit()
        return project

    def _phase_discovery(self, task: Task) -> List[dict]:
        """Agent 1: search -> dedupe -> OA download -> persist papers."""
        task_service.append_log(
            self.db, task,
            f"Agent 1 (discovery) started: sources={task.sources_list}, "
            f"limit={task.limit}.",
        )
        papers = run_discovery(
            task.query, limit=task.limit, sources=task.sources_list,
            download_dir=str(PDF_DIR), download_pdfs=True,
        )
        for p in papers:
            paper = paper_service.upsert_paper(
                self.db, p, project_id=task.project_id, task_id=task.task_id
            )
            p["_paper_id"] = paper.id  # link dict -> DB row for extraction phase
            task_service.append_log(
                self.db, task,
                f"DISCOVERY [{p['source_api']}] '{p['title'][:80]}' "
                f"status={paper.status} source={p['source_link']}",
            )
        task_service.append_log(
            self.db, task,
            f"Agent 1 finished: {len(papers)} unique papers "
            f"({sum(1 for p in papers if p.get('pdf_path'))} with PDF).",
        )
        return papers

    def _phase_extraction(self, task: Task, paper_dicts: List[dict]) -> int:
        """Agent 2: parse -> index -> extract, per paper with a PDF."""
        self._ensure_project(task)  # evidence rows need a real project_id
        total = 0
        for p in paper_dicts:
            paper_id = p.get("_paper_id")
            paper = (paper_service.get_paper(self.db, paper_id)
                     if paper_id else None)
            if paper is None:
                task_service.append_log(
                    self.db, task,
                    f"Skipping paper dict with no DB row: {p.get('title', '?')[:60]}")
                continue
            label = f"'{paper.title[:60]}'"
            try:
                task_service.append_log(
                    self.db, task, f"Agent 2 parsing PDF for {label} "
                                   f"(file={paper.pdf_path}).")
                parsed = agent2.parse_pdf_passages(paper.pdf_path, paper.id)
                n = agent2.index_passages_to_chromadb(
                    paper.id, parsed["passages"],
                    source_doi_or_url=paper.source_link,
                )
                paper_service.set_status(self.db, paper, "full_text_parsed")
                task_service.append_log(
                    self.db, task,
                    f"Indexed {n} passages from {parsed['pages']} pages "
                    f"for {label}. source={paper.source_link}")
                extract = agent2.extract_structured_evidence(
                    paper.id, task.query,
                    source_doi_or_url=paper.source_link,
                    top_k=settings.extraction_top_k,
                    db=self.db, task_id=task.task_id,
                    project_id=task.project_id,
                )
                n_ev = len(extract.get("evidence_ids", []))
                total += n_ev
                task_service.append_log(
                    self.db, task,
                    f"Agent 2 extracted {n_ev} anchored evidence items "
                    f"for {label} (method={extract['extraction_method']}).")
            except Exception as exc:
                paper_service.set_status(self.db, paper, "extraction_failed")
                task_service.append_log(
                    self.db, task,
                    f"Agent 2 FAILED for {label}: {exc}. source={paper.source_link}")
                log.warning("Extraction failed for paper %s: %s", paper.id, exc)
        return total

    def _pause_for_upload(self, task: Task, papers: List[dict]) -> Task:
        task_service.append_log(
            self.db, task,
            f"GATE: {len(papers)} paper(s) found but none has an accessible "
            f"PDF. Pausing as needs_pdf_upload — upload PDFs, then resume.",
        )
        project = self._ensure_project(task)
        agent8.log_activity_feed(
            self.db, project.id, "supervisor",
            f"Task {task.task_id} paused: {len(papers)} paper(s) need PDF upload.",
            task_id=task.task_id,
        )
        return task_service.set_status(self.db, task, "needs_pdf_upload")

    def _pause_for_approval(self, task: Task, papers: List[dict],
                            evidence_total: int) -> Task:
        if evidence_total > 0:
            msg = (f"GATE: {evidence_total} critical evidence item(s) extracted "
                   f"from {len(papers)} paper(s). Pausing as needs_approval — "
                   f"researcher review required before verification.")
        else:
            msg = ("GATE: extraction produced no evidence items. Pausing as "
                   "needs_approval for researcher review.")
        task_service.append_log(self.db, task, msg)
        project = self._ensure_project(task)
        agent8.log_activity_feed(
            self.db, project.id, "supervisor",
            f"Task {task.task_id}: {evidence_total} evidence item(s) awaiting "
            f"researcher approval.", task_id=task.task_id,
        )
        return task_service.set_status(self.db, task, "needs_approval")

    # ---- Phase 2 phase implementations ----

    def _phase_gap_discovery(self, task: Task) -> List[dict]:
        """Agent 3: evidence-matrix analysis + novelty challenge per gap."""
        project = self._ensure_project(task)
        task_service.append_log(
            self.db, task, "Agent 3 (gap discovery) started.")
        briefs = agent3.run_gap_discovery(
            project.id, self.db, task_id=task.task_id, max_gaps=5)
        for b in briefs:
            n_counter = len(b["counter_search"]["findings"])
            task_service.append_log(
                self.db, task,
                f"GAP [{b['gap_type']}] {b['verification_status']}: "
                f"{b['gap_statement'][:100]}… "
                f"({n_counter} counter-search results, "
                f"importance={b['scores']['importance']}, "
                f"feasibility={b['scores']['feasibility']})")
            for r in b["counter_search"]["findings"][:3]:
                task_service.append_log(
                    self.db, task,
                    f"  counter-search hit: {r.get('title', '')[:70]} — "
                    f"{r.get('source_link')}")
        task_service.append_log(
            self.db, task,
            f"Agent 3 finished: {len(briefs)} gap brief(s).")
        agent8.log_activity_feed(
            self.db, project.id, "agent3",
            f"Task {task.task_id}: {len(briefs)} gap brief(s) proposed, "
            f"{sum(1 for b in briefs if b['verification_status'] == 'challenged_disproved')} "
            f"disproved by counter-search.", task_id=task.task_id)
        return briefs

    def _pause_for_gap_approval(self, task: Task, briefs: List[dict]) -> Task:
        n_disproved = sum(1 for b in briefs
                          if b["verification_status"] == "challenged_disproved")
        task_service.append_log(
            self.db, task,
            f"GATE: {len(briefs)} gap brief(s) ready ({n_disproved} disproved by "
            f"novelty challenge). Pausing as needs_gap_approval — researcher "
            f"must accept/reject each gap.")
        return task_service.set_status(self.db, task, "needs_gap_approval")

    def _phase_design(self, task: Task, approved_gaps: List,
                      design_type: str) -> List[dict]:
        """Agent 4: protocol + measurement model + ethics plan per gap."""
        project = self._ensure_project(task)
        protocols = []
        for gap in approved_gaps:
            dtype = gap.design_type or design_type
            proto = agent4.propose_research_protocol(
                gap.id, dtype, self.db, task_id=task.task_id)
            protocols.append(proto)
            task_service.append_log(
                self.db, task,
                f"Agent 4 drafted protocol '{proto['title'][:80]}' "
                f"({dtype}) for gap {gap.id[:8]}: "
                f"{len(proto['measurement_model'])} construct(s) mapped, "
                f"{len(proto['ethics_checklist'])} ethics items.")
            agent8.log_activity_feed(
                self.db, project.id, "agent4",
                f"Protocol drafted for gap {gap.id[:8]} ({dtype}).",
                task_id=task.task_id)
        return protocols

    def _pause_for_protocol_approval(self, task: Task,
                                     protocols: List[dict]) -> Task:
        task_service.append_log(
            self.db, task,
            f"GATE: {len(protocols)} protocol(s) ready. Pausing as "
            f"needs_protocol_approval — researcher approval required before "
            f"any data collection or analysis.")
        return task_service.set_status(self.db, task, "needs_protocol_approval")

    def _phase_audit(self, task: Task, approved_protocols: List) -> Task:
        """Agent 7: independent audit. HIGH severity -> task 'blocked'."""
        project = self._ensure_project(task)
        # Audit the project's own evidence base as the "manuscript": every
        # extracted claim is checked against its cited paper's passages.
        ev_rows = evidence_service.list_evidence(
            self.db, project_id=project.id, limit=500)
        manuscript = "\n".join(e.claim_text or "" for e in ev_rows[:50])
        cited = sorted({e.paper_id for e in ev_rows if e.paper_id})
        blocked_reports = []
        for proto in approved_protocols:
            report = agent7.generate_audit_report(
                project.id, self.db, task_id=task.task_id,
                protocol_id=proto.id,
                manuscript_text=manuscript or None,
                cited_paper_ids=cited or None)
            s = report["summary"]
            task_service.append_log(
                self.db, task,
                f"Agent 7 audit for protocol {proto.id[:8]}: {report['overall_status']} "
                f"({s['flags_high']} high, {s['flags_medium']} medium, "
                f"{s['papers_audited']} papers, {s['evidence_items']} evidence items).")
            for fl in report["flags"]:
                if fl["severity"] == "high":
                    task_service.append_log(
                        self.db, task,
                        f"  HIGH [{fl['category']}]: {fl['claim_text'][:100]} — "
                        f"{fl['source_link']}")
            if report["overall_status"] == "blocked":
                blocked_reports.append(report)
            agent8.log_activity_feed(
                self.db, project.id, "agent7",
                f"Audit {report['report_id'][:8]} for protocol {proto.id[:8]}: "
                f"{report['overall_status']}.", task_id=task.task_id)

        if blocked_reports:
            task_service.append_log(
                self.db, task,
                f"GATE: {len(blocked_reports)} audit report(s) BLOCKED on "
                f"high-severity issues. Task set to 'blocked' — resolve the "
                f"flags before proceeding. The system will not auto-fix.")
            agent8.save_project_decision(
                self.db, project.id, "audit_blocked",
                {"blocked_reports": [r["report_id"] for r in blocked_reports]},
                user_approved=False, task_id=task.task_id,
                evidence_link=None)
            return task_service.set_status(self.db, task, "blocked")

        agent8.save_project_decision(
            self.db, project.id, "protocol_audited",
            {"protocols": [p.id for p in approved_protocols],
             "audit": "pass/fail with no high-severity flags"},
            user_approved=True, task_id=task.task_id, evidence_link=None)
        task_service.append_log(
            self.db, task,
            "All audits clear of high-severity issues. Decision persisted "
            "to project memory (Agent 8).")
        return task_service.finish_task(self.db, task, "verified")
