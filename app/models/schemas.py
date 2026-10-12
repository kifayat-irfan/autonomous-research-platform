"""Canonical data model for Phase 1.

SQLAlchemy ORM models (SQLite via SQLAlchemy, Postgres-ready) plus the
Pydantic request/response schemas used by the FastAPI routers.

Provenance rule (spec section 4.2): every factual output links back to a
source — papers carry `source_link`, evidence carries `source_url` +
`page_number`, tasks carry `task_id` + `execution_log` with source URLs.
"""
import json
import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

# ---------------------------------------------------------------------------
# Status vocabularies (single source of truth)
# ---------------------------------------------------------------------------

PAPER_STATUSES = {
    "metadata_only",      # discovery metadata, no full text yet
    "oa_url_found",       # legal open-access URL located, not yet downloaded
    "pdf_acquired",       # PDF file present locally (downloaded or uploaded)
    "full_text_parsed",   # PDF parsed + passages indexed in ChromaDB
    "needs_pdf_upload",   # no legal OA copy -> researcher must upload
    "extraction_failed",  # PDF present but parsing/extraction failed
    "restricted",         # paywalled / no legal access route
}

TASK_STATUSES = {
    "queued",             # task created, not yet executed
    "running",            # active search / extraction
    "needs_pdf_upload",   # paused: at least one paper has no accessible PDF
    "needs_approval",     # paused: critical evidence ready for researcher review
    "needs_gap_approval",     # Phase 2: gap briefs await researcher decision
    "needs_protocol_approval",  # Phase 2: study protocol awaits researcher decision
    "needs_analysis_approval",  # Phase 3: analysis results await researcher decision
    "needs_manuscript_approval",  # Phase 3: manuscript draft awaits researcher decision
    "blocked",            # high-severity audit issues block progression
    "verified",           # researcher-approved, outputs usable downstream
    "failed",             # unrecoverable error or researcher rejection
}

# --- Phase 2 vocabularies ---
GAP_TYPES = {"theoretical", "empirical", "population", "methodological"}
GAP_VERIFICATION = {"unverified", "challenged_pass", "challenged_disproved"}
RESEARCHER_DECISION = {"pending", "approved", "rejected"}
DESIGN_TYPES = {"survey", "experimental", "observational", "mixed_methods"}
AUDIT_SEVERITY = {"high", "medium", "low"}
AUDIT_OVERALL = {"pass", "fail", "blocked"}
NOVELTY_STATUS = {"pending_search", "challenged_pass", "challenged_disproved"}

# --- Phase 3 vocabularies ---
ANALYSIS_TYPES = {"descriptive", "ttest", "chi2", "correlation", "regression"}
SCREENING_DECISIONS = {"include", "exclude", "maybe"}
ROB_RATINGS = {"low", "some_concerns", "high"}
REVIEW_STAGES = {"title_abstract", "full_text"}

VERIFICATION_STATES = {
    "machine_extracted",  # produced by Agent 2, not yet reviewed
    "needs_review",       # flagged as uncertain
    "verified",           # independently checked against source passage
    "rejected",           # researcher judged the extraction wrong
    "researcher_approved",
    "unverified",
}


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.utcnow()


# ---------------------------------------------------------------------------
# SQLAlchemy models
# ---------------------------------------------------------------------------

class Project(Base):
    """A research topic / workspace (spec 4.1: Project / topic)."""
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    keywords: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow
    )

    papers: Mapped[list["Paper"]] = relationship(back_populates="project")
    tasks: Mapped[list["Task"]] = relationship(back_populates="project")
    decisions: Mapped[list["ProjectDecision"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    activity: Mapped[list["ActivityLog"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class Paper(Base):
    """Canonical publication record (spec 4.1: Paper / version, App. A1)."""
    __tablename__ = "papers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    doi: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    authors: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list
    journal: Mapped[str | None] = mapped_column(String(500), nullable=True)
    pub_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    abstract: Mapped[str | None] = mapped_column(Text, nullable=True)
    oa_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    pdf_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), default="metadata_only", index=True
    )
    source_api: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # COMPULSORY: every paper record carries a functional source link
    # (DOI URL, publisher/S2/PubMed page, or explicit search link).
    source_link: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    project_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("projects.id"), nullable=True, index=True
    )
    task_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("tasks.task_id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow
    )

    project: Mapped["Project | None"] = relationship(back_populates="papers")
    evidence: Mapped[list["Evidence"]] = relationship(
        back_populates="paper", cascade="all, delete-orphan"
    )

    @property
    def author_list(self) -> list[str]:
        if not self.authors:
            return []
        try:
            parsed = json.loads(self.authors)
            return parsed if isinstance(parsed, list) else [str(parsed)]
        except (json.JSONDecodeError, TypeError):
            return [self.authors]

    @property
    def pub_year(self) -> int | None:
        return self.pub_date.year if self.pub_date else None


class Evidence(Base):
    """Structured finding/claim anchored to a source passage (App. A2)."""
    __tablename__ = "evidence"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    paper_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("papers.id"), nullable=False, index=True
    )
    project_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("projects.id"), nullable=True, index=True
    )
    task_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("tasks.task_id"), nullable=True, index=True
    )
    claim_text: Mapped[str] = mapped_column(Text, nullable=False)
    objective: Mapped[str | None] = mapped_column(Text, nullable=True)
    method: Mapped[str | None] = mapped_column(Text, nullable=True)
    sample_size: Mapped[str | None] = mapped_column(String(255), nullable=True)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    paragraph_quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    # COMPULSORY: direct evidence link / source pointer (DOI URL + page).
    source_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    verification_state: Mapped[str] = mapped_column(
        String(32), default="machine_extracted", index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    paper: Mapped["Paper"] = relationship(back_populates="evidence")


class Task(Base):
    """Supervisor job / approval record (spec 4.1: Job / approval)."""
    __tablename__ = "tasks"

    task_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    agent_name: Mapped[str] = mapped_column(String(64), default="supervisor")
    query: Mapped[str] = mapped_column(Text, nullable=False)
    project_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("projects.id"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    limit: Mapped[int] = mapped_column(Integer, default=10)
    sources: Mapped[str] = mapped_column(
        Text, default='["semantic_scholar", "pubmed"]'
    )  # JSON list
    execution_log: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow
    )

    project: Mapped["Project | None"] = relationship(back_populates="tasks")

    @property
    def sources_list(self) -> list[str]:
        try:
            parsed = json.loads(self.sources)
            return parsed if isinstance(parsed, list) else []
        except (json.JSONDecodeError, TypeError):
            return []


class ProjectDecision(Base):
    """Human choices persisted by Agent 8 (approved papers, decisions...)."""
    __tablename__ = "project_decisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id"), nullable=False, index=True
    )
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    decision_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    details: Mapped[str] = mapped_column(Text, default="{}")  # JSON object
    user_approved: Mapped[bool] = mapped_column(Boolean, default=False)
    evidence_link: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    project: Mapped["Project"] = relationship(back_populates="decisions")

    @property
    def details_dict(self) -> dict:
        try:
            return json.loads(self.details)
        except (json.JSONDecodeError, TypeError):
            return {}


class ActivityLog(Base):
    """Auditable activity feed for Agent 8."""
    __tablename__ = "activity_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id"), nullable=False, index=True
    )
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    agent_name: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_link: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    project: Mapped["Project"] = relationship(back_populates="activity")


# ---------------------------------------------------------------------------
# Phase 2 models: gaps, protocols, audits, cross-domain ideas
# ---------------------------------------------------------------------------

class GapBrief(Base):
    """Agent 3 output: a candidate research gap + its novelty challenge."""
    __tablename__ = "gap_briefs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id"), nullable=False, index=True
    )
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    gap_statement: Mapped[str] = mapped_column(Text, nullable=False)
    gap_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    known_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    unknown_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # JSON: [{paper_id, title, source_link, page_number}]
    supporting_evidence: Mapped[str] = mapped_column(Text, default="[]")
    counter_search_query: Mapped[str | None] = mapped_column(Text, nullable=True)
    # JSON: [{title, doi, source_link, source_api, relevance_note}]
    counter_search_results: Mapped[str] = mapped_column(Text, default="[]")
    importance_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    feasibility_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    verification_status: Mapped[str] = mapped_column(
        String(32), default="unverified", index=True
    )
    researcher_decision: Mapped[str] = mapped_column(
        String(16), default="pending", index=True
    )
    researcher_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    design_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    @property
    def supporting_list(self) -> list:
        try:
            return json.loads(self.supporting_evidence or "[]")
        except (json.JSONDecodeError, TypeError):
            return []

    @property
    def counter_list(self) -> list:
        try:
            return json.loads(self.counter_search_results or "[]")
        except (json.JSONDecodeError, TypeError):
            return []


class ResearchProtocol(Base):
    """Agent 4 output: a reviewable study protocol for an approved gap."""
    __tablename__ = "research_protocols"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id"), nullable=False, index=True
    )
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    gap_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("gap_briefs.id"), nullable=False, index=True
    )
    design_type: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    research_questions: Mapped[str] = mapped_column(Text, default="[]")  # JSON
    hypotheses: Mapped[str] = mapped_column(Text, default="[]")  # JSON
    sections: Mapped[str] = mapped_column(Text, default="{}")  # JSON dict
    measurement_model: Mapped[str] = mapped_column(Text, default="[]")  # JSON
    ethics_checklist: Mapped[str] = mapped_column(Text, default="[]")  # JSON
    approval_checkpoint: Mapped[str] = mapped_column(Text, default="{}")  # JSON
    approval_status: Mapped[str] = mapped_column(
        String(16), default="pending", index=True
    )
    researcher_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    def _j(self, attr: str, default):
        try:
            return json.loads(getattr(self, attr) or "x")
        except (json.JSONDecodeError, TypeError):
            return default

    @property
    def rq_list(self) -> list:
        return self._j("research_questions", [])

    @property
    def hypothesis_list(self) -> list:
        return self._j("hypotheses", [])

    @property
    def sections_dict(self) -> dict:
        return self._j("sections", {})

    @property
    def measurement_list(self) -> list:
        return self._j("measurement_model", [])

    @property
    def ethics_list(self) -> list:
        return self._j("ethics_checklist", [])

    @property
    def checkpoint_dict(self) -> dict:
        return self._j("approval_checkpoint", {})


class AuditReport(Base):
    """Agent 7 output: pass/fail audit of claims, numbers, DOIs, wording."""
    __tablename__ = "audit_reports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id"), nullable=False, index=True
    )
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    protocol_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    overall_status: Mapped[str] = mapped_column(
        String(16), default="pass", index=True
    )
    summary: Mapped[str] = mapped_column(Text, default="{}")  # JSON
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    flags: Mapped[list["AuditFlag"]] = relationship(
        back_populates="report", cascade="all, delete-orphan"
    )

    @property
    def summary_dict(self) -> dict:
        try:
            return json.loads(self.summary or "{}")
        except (json.JSONDecodeError, TypeError):
            return {}


class AuditFlag(Base):
    """One audit finding, always anchored to passage + source link."""
    __tablename__ = "audit_flags"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    report_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("audit_reports.id"), nullable=False, index=True
    )
    severity: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    claim_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected: Mapped[str | None] = mapped_column(Text, nullable=True)
    found: Mapped[str | None] = mapped_column(Text, nullable=True)
    passage_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_link: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    report: Mapped["AuditReport"] = relationship(back_populates="flags")


class ConceptNote(Base):
    """Agent 9 output: cross-domain research idea (hypothesis until challenged)."""
    __tablename__ = "concept_notes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id"), nullable=False, index=True
    )
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    source_domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    target_domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    bridge_concept: Mapped[str | None] = mapped_column(Text, nullable=True)
    shared_mechanism: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_constructs: Mapped[str] = mapped_column(Text, default="[]")  # JSON
    target_application: Mapped[str | None] = mapped_column(Text, nullable=True)
    required_adaptations: Mapped[str] = mapped_column(Text, default="[]")  # JSON
    # JSON: [{title, source_link, domain}]
    source_citations: Mapped[str] = mapped_column(Text, default="[]")
    target_citations: Mapped[str] = mapped_column(Text, default="[]")
    feasibility: Mapped[str] = mapped_column(Text, default="{}")  # JSON
    novelty_status: Mapped[str] = mapped_column(
        String(32), default="pending_search", index=True
    )
    challenge_results: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    researcher_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    def _j(self, attr: str, default):
        try:
            return json.loads(getattr(self, attr) or "x")
        except (json.JSONDecodeError, TypeError):
            return default

    @property
    def source_citation_list(self) -> list:
        return self._j("source_citations", [])

    @property
    def target_citation_list(self) -> list:
        return self._j("target_citations", [])


# ---------------------------------------------------------------------------
# Phase 3 models: analysis, writing, systematic review, journal, intelligence
# ---------------------------------------------------------------------------

class AnalysisRun(Base):
    """Agent 5 output: reproducible analysis of an uploaded dataset."""
    __tablename__ = "analysis_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id"), nullable=False, index=True
    )
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    protocol_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    dataset_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    dataset_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    dataset_checksum: Mapped[str | None] = mapped_column(String(128), nullable=True)
    analysis_type: Mapped[str] = mapped_column(String(32), nullable=False)
    analysis_config: Mapped[str] = mapped_column(Text, default="{}")  # JSON
    cleaning_report: Mapped[str] = mapped_column(Text, default="{}")  # JSON
    results: Mapped[str] = mapped_column(Text, default="{}")  # JSON
    script_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    script_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    log: Mapped[str] = mapped_column(Text, default="")
    approval_status: Mapped[str] = mapped_column(
        String(16), default="pending", index=True
    )
    researcher_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    def _j(self, attr: str, default):
        try:
            return json.loads(getattr(self, attr) or "x")
        except (json.JSONDecodeError, TypeError):
            return default

    @property
    def results_dict(self) -> dict:
        return self._j("results", {})

    @property
    def cleaning_dict(self) -> dict:
        return self._j("cleaning_report", {})

    @property
    def config_dict(self) -> dict:
        return self._j("analysis_config", {})


class Manuscript(Base):
    """Agent 6 output: drafted manuscript with style profile + evidence map."""
    __tablename__ = "manuscripts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id"), nullable=False, index=True
    )
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    protocol_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    sections: Mapped[str] = mapped_column(Text, default="{}")  # JSON {name: text}
    style_profile: Mapped[str] = mapped_column(Text, default="{}")  # JSON
    evidence_map: Mapped[str] = mapped_column(Text, default="[]")  # JSON
    docx_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    approval_status: Mapped[str] = mapped_column(
        String(16), default="pending", index=True
    )
    researcher_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    def _j(self, attr: str, default):
        try:
            return json.loads(getattr(self, attr) or "x")
        except (json.JSONDecodeError, TypeError):
            return default

    @property
    def sections_dict(self) -> dict:
        return self._j("sections", {})

    @property
    def style_dict(self) -> dict:
        return self._j("style_profile", {})

    @property
    def evidence_list(self) -> list:
        return self._j("evidence_map", [])


class ReviewProject(Base):
    """Agent 10: systematic review workspace (PRISMA 2020 aligned)."""
    __tablename__ = "review_projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id"), nullable=False, index=True
    )
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    pico: Mapped[str] = mapped_column(Text, default="{}")  # JSON
    inclusion_criteria: Mapped[str] = mapped_column(Text, default="[]")  # JSON
    exclusion_criteria: Mapped[str] = mapped_column(Text, default="[]")  # JSON
    search_log: Mapped[str] = mapped_column(Text, default="[]")  # JSON
    screening: Mapped[str] = mapped_column(Text, default="[]")  # JSON decisions
    prisma: Mapped[str] = mapped_column(Text, default="{}")  # JSON flow counts
    extractions: Mapped[str] = mapped_column(Text, default="[]")  # JSON effects
    rob: Mapped[str] = mapped_column(Text, default="[]")  # JSON RoB assessments
    meta_results: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    approval_status: Mapped[str] = mapped_column(
        String(16), default="pending", index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    def _j(self, attr: str, default):
        try:
            return json.loads(getattr(self, attr) or "x")
        except (json.JSONDecodeError, TypeError):
            return default

    @property
    def pico_dict(self) -> dict:
        return self._j("pico", {})

    @property
    def search_list(self) -> list:
        return self._j("search_log", [])

    @property
    def screening_list(self) -> list:
        return self._j("screening", [])

    @property
    def prisma_dict(self) -> dict:
        return self._j("prisma", {})

    @property
    def extraction_list(self) -> list:
        return self._j("extractions", [])

    @property
    def rob_list(self) -> list:
        return self._j("rob", [])

    @property
    def meta_dict(self) -> dict | None:
        return self._j("meta_results", None)


class JournalMatch(Base):
    """Agent 11: journal scope match (date-stamped; verify on official site)."""
    __tablename__ = "journal_matches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id"), nullable=False, index=True
    )
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    manuscript_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    journal_name: Mapped[str] = mapped_column(String(255), nullable=False)
    scope_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    match_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    requirements: Mapped[str] = mapped_column(Text, default="{}")  # JSON
    apc_info: Mapped[str | None] = mapped_column(String(500), nullable=True)
    checked_date: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_link: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class ReviewerResponse(Base):
    """Agent 11: reviewer comment -> response matrix row."""
    __tablename__ = "reviewer_responses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id"), nullable=False, index=True
    )
    manuscript_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    reviewer: Mapped[str | None] = mapped_column(String(64), nullable=True)
    comment: Mapped[str] = mapped_column(Text, nullable=False)
    response: Mapped[str] = mapped_column(Text, default="")
    manuscript_change: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class WatchTopic(Base):
    """Agent 12: saved literature-monitoring topic."""
    __tablename__ = "watch_topics"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    sources: Mapped[str] = mapped_column(Text, default='["semantic_scholar", "pubmed"]')
    schedule: Mapped[str] = mapped_column(String(32), default="daily")
    last_run: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    watermark: Mapped[str] = mapped_column(Text, default="{}")  # JSON
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    @property
    def sources_list(self) -> list:
        try:
            return json.loads(self.sources or "[]")
        except (json.JSONDecodeError, TypeError):
            return []


class Alert(Base):
    """Agent 12: significance / new-paper alert."""
    __tablename__ = "alerts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id"), nullable=False, index=True
    )
    watch_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("watch_topics.id"), nullable=True, index=True
    )
    paper_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("papers.id"), nullable=True
    )
    alert_type: Mapped[str] = mapped_column(String(32), default="new_paper", index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    relevance_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_link: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


# ---------------------------------------------------------------------------
# Pydantic schemas (API contracts)
# ---------------------------------------------------------------------------

class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class PaperOut(_Out):
    id: str
    doi: str | None = None
    title: str
    authors: list[str] | None = None
    journal: str | None = None
    pub_date: date | None = None
    pub_year: int | None = None
    abstract: str | None = None
    oa_url: str | None = None
    pdf_path: str | None = None
    status: str
    source_api: str | None = None
    source_link: str | None = None
    project_id: str | None = None
    task_id: str | None = None
    created_at: datetime | None = None

    @field_validator("authors", mode="before")
    @classmethod
    def _parse_authors(cls, v):
        if v is None or isinstance(v, list):
            return v
        try:
            parsed = json.loads(v)
            return parsed if isinstance(parsed, list) else [str(parsed)]
        except (json.JSONDecodeError, TypeError):
            return [str(v)]


class EvidenceOut(_Out):
    id: str
    paper_id: str
    project_id: str | None = None
    task_id: str | None = None
    claim_text: str
    objective: str | None = None
    method: str | None = None
    sample_size: str | None = None
    page_number: int | None = None
    paragraph_quote: str | None = None
    source_url: str | None = None
    verification_state: str
    created_at: datetime | None = None


class TaskOut(_Out):
    task_id: str
    agent_name: str
    query: str
    project_id: str | None = None
    status: str
    limit: int
    sources: list[str] | None = None
    execution_log: str = ""
    error: str | None = None
    duration_seconds: float | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime | None = None

    @field_validator("sources", mode="before")
    @classmethod
    def _parse_sources(cls, v):
        if v is None or isinstance(v, list):
            return v
        try:
            parsed = json.loads(v)
            return parsed if isinstance(parsed, list) else []
        except (json.JSONDecodeError, TypeError):
            return []


class ProjectOut(_Out):
    id: str
    name: str
    description: str | None = None
    keywords: list[str] | None = None
    status: str
    created_at: datetime | None = None

    @field_validator("keywords", mode="before")
    @classmethod
    def _parse_keywords(cls, v):
        if v is None or isinstance(v, list):
            return v
        try:
            parsed = json.loads(v)
            return parsed if isinstance(parsed, list) else [str(parsed)]
        except (json.JSONDecodeError, TypeError):
            return [str(v)]


class DecisionOut(_Out):
    id: str
    project_id: str
    task_id: str | None = None
    decision_type: str
    details: dict = {}
    user_approved: bool
    evidence_link: str | None = None
    created_at: datetime | None = None

    @field_validator("details", mode="before")
    @classmethod
    def _parse_details(cls, v):
        if isinstance(v, dict):
            return v
        try:
            return json.loads(v) if v else {}
        except (json.JSONDecodeError, TypeError):
            return {}


class ActivityOut(_Out):
    id: str
    project_id: str
    task_id: str | None = None
    agent_name: str
    action: str
    evidence_link: str | None = None
    created_at: datetime | None = None


# --- Request bodies ---

class DiscoveryRequest(BaseModel):
    query: str = Field(min_length=2)
    limit: int = Field(default=10, ge=1, le=50)
    sources: list[str] = Field(default=["semantic_scholar", "pubmed"])
    project_id: str | None = None
    download_pdfs: bool = True


class SupervisorTaskRequest(BaseModel):
    query: str = Field(min_length=2)
    project_id: str | None = None
    sources: list[str] | None = None
    limit: int = Field(default=10, ge=1, le=50)


class ApproveRequest(BaseModel):
    approved: bool
    note: str = ""


class ExtractRequest(BaseModel):
    query: str = Field(min_length=2)
    project_id: str | None = None
    task_id: str | None = None
    top_k: int = Field(default=5, ge=1, le=20)


class ProjectCreate(BaseModel):
    name: str = Field(min_length=2)
    description: str | None = None
    keywords: list[str] | None = None


class DecisionCreate(BaseModel):
    decision_type: str = Field(min_length=2)
    details: dict = {}
    user_approved: bool = False
    task_id: str | None = None
    evidence_link: str | None = None


class ActivityCreate(BaseModel):
    agent_name: str = Field(min_length=2)
    action: str = Field(min_length=2)
    evidence_link: str | None = None
    task_id: str | None = None


class PaperStatusUpdate(BaseModel):
    status: str


# --- Phase 2 response/request schemas ---

class GapBriefOut(_Out):
    id: str
    project_id: str
    task_id: str | None = None
    gap_statement: str
    gap_type: str
    known_summary: str | None = None
    unknown_summary: str | None = None
    supporting_evidence: list = []
    counter_search_query: str | None = None
    counter_search_results: list = []
    importance_score: int | None = None
    feasibility_score: int | None = None
    verification_status: str
    researcher_decision: str
    researcher_note: str | None = None
    design_type: str | None = None
    created_at: datetime | None = None

    @field_validator("supporting_evidence", "counter_search_results", mode="before")
    @classmethod
    def _parse_json_list(cls, v):
        if isinstance(v, list):
            return v
        try:
            return json.loads(v) if v else []
        except (json.JSONDecodeError, TypeError):
            return []


class GapAnalyzeRequest(BaseModel):
    project_id: str
    task_id: str | None = None
    max_gaps: int = Field(default=5, ge=1, le=15)


class GapReviewRequest(BaseModel):
    approved: bool
    note: str = ""
    design_type: str | None = None  # survey|experimental|observational|mixed_methods


class ProtocolCreateRequest(BaseModel):
    gap_id: str
    design_type: str = "mixed_methods"
    target_population: str | None = None
    sample_size_needed: int | None = None
    constructs: list[str] | None = None


class ProtocolOut(_Out):
    id: str
    project_id: str
    task_id: str | None = None
    gap_id: str
    design_type: str
    title: str
    research_questions: list = []
    hypotheses: list = []
    sections: dict = {}
    measurement_model: list = []
    ethics_checklist: list = []
    approval_checkpoint: dict = {}
    approval_status: str
    researcher_note: str | None = None
    created_at: datetime | None = None

    @field_validator("research_questions", "hypotheses", "measurement_model",
                     "ethics_checklist", mode="before")
    @classmethod
    def _parse_list(cls, v):
        if isinstance(v, list):
            return v
        try:
            return json.loads(v) if v else []
        except (json.JSONDecodeError, TypeError):
            return []

    @field_validator("sections", "approval_checkpoint", mode="before")
    @classmethod
    def _parse_dict(cls, v):
        if isinstance(v, dict):
            return v
        try:
            return json.loads(v) if v else {}
        except (json.JSONDecodeError, TypeError):
            return {}


class ProtocolReviewRequest(BaseModel):
    approved: bool
    note: str = ""


class MeasurementModelRequest(BaseModel):
    constructs: list[str] = Field(min_length=1)


class AuditClaimsRequest(BaseModel):
    manuscript_or_text: str = Field(min_length=10)
    cited_paper_ids: list[str] = Field(min_length=1)


class ReportedValue(BaseModel):
    label: str
    metric: str = ""
    value: float | str
    paper_id: str
    location: str = ""  # e.g. "Table 2" / "p. 4"


class AuditNumericsRequest(BaseModel):
    reported_table: list[ReportedValue]
    # evidence items: [{paper_id, sample_size, paragraph_quote, page_number, source_url}]
    source_evidence: list[dict]


class AuditReportRequest(BaseModel):
    project_id: str
    task_id: str | None = None
    protocol_id: str | None = None
    manuscript_text: str | None = None
    cited_paper_ids: list[str] | None = None


class AuditFlagOut(_Out):
    id: str
    severity: str
    category: str
    claim_text: str | None = None
    expected: str | None = None
    found: str | None = None
    passage_text: str | None = None
    page_number: int | None = None
    source_link: str | None = None
    status: str


class AuditReportOut(_Out):
    id: str
    project_id: str
    task_id: str | None = None
    protocol_id: str | None = None
    overall_status: str
    summary: dict = {}
    flags: list[AuditFlagOut] = []
    created_at: datetime | None = None

    @field_validator("summary", mode="before")
    @classmethod
    def _parse_summary(cls, v):
        if isinstance(v, dict):
            return v
        try:
            return json.loads(v) if v else {}
        except (json.JSONDecodeError, TypeError):
            return {}


class CrossDomainIdeaRequest(BaseModel):
    project_id: str
    task_id: str | None = None
    target_domain: str = Field(min_length=2)
    source_domain: str | None = None
    max_ideas: int = Field(default=3, ge=1, le=8)


class ConceptNoteOut(_Out):
    id: str
    project_id: str
    task_id: str | None = None
    title: str
    source_domain: str | None = None
    target_domain: str | None = None
    bridge_concept: str | None = None
    shared_mechanism: str | None = None
    source_constructs: list = []
    target_application: str | None = None
    required_adaptations: list = []
    source_citations: list = []
    target_citations: list = []
    feasibility: dict = {}
    novelty_status: str
    researcher_note: str | None = None
    created_at: datetime | None = None

    @field_validator("source_constructs", "required_adaptations",
                     "source_citations", "target_citations", mode="before")
    @classmethod
    def _parse_list(cls, v):
        if isinstance(v, list):
            return v
        try:
            return json.loads(v) if v else []
        except (json.JSONDecodeError, TypeError):
            return []

    @field_validator("feasibility", mode="before")
    @classmethod
    def _parse_dict(cls, v):
        if isinstance(v, dict):
            return v
        try:
            return json.loads(v) if v else {}
        except (json.JSONDecodeError, TypeError):
            return {}


class SupervisorAdvanceGapsRequest(BaseModel):
    design_type: str = "mixed_methods"


# --- Phase 3 response/request schemas ---

class AnalysisRunOut(_Out):
    id: str
    project_id: str
    task_id: str | None = None
    protocol_id: str | None = None
    dataset_name: str | None = None
    dataset_checksum: str | None = None
    analysis_type: str
    analysis_config: dict = {}
    cleaning_report: dict = {}
    results: dict = {}
    script_path: str | None = None
    approval_status: str
    researcher_note: str | None = None
    created_at: datetime | None = None

    @field_validator("analysis_config", "cleaning_report", "results", mode="before")
    @classmethod
    def _parse_dict(cls, v):
        if isinstance(v, dict):
            return v
        try:
            return json.loads(v) if v else {}
        except (json.JSONDecodeError, TypeError):
            return {}


class AnalysisReviewRequest(BaseModel):
    approved: bool
    note: str = ""


class ManuscriptOut(_Out):
    id: str
    project_id: str
    task_id: str | None = None
    protocol_id: str | None = None
    title: str
    sections: dict = {}
    style_profile: dict = {}
    evidence_map: list = []
    docx_path: str | None = None
    approval_status: str
    researcher_note: str | None = None
    created_at: datetime | None = None

    @field_validator("sections", "style_profile", mode="before")
    @classmethod
    def _parse_dict(cls, v):
        if isinstance(v, dict):
            return v
        try:
            return json.loads(v) if v else {}
        except (json.JSONDecodeError, TypeError):
            return {}

    @field_validator("evidence_map", mode="before")
    @classmethod
    def _parse_list(cls, v):
        if isinstance(v, list):
            return v
        try:
            return json.loads(v) if v else []
        except (json.JSONDecodeError, TypeError):
            return []


class ManuscriptCreateRequest(BaseModel):
    project_id: str
    task_id: str | None = None
    protocol_id: str | None = None
    title: str | None = None
    sections: list[str] = ["abstract", "introduction", "methods", "results",
                           "discussion", "conclusion"]
    style_profile: dict | None = None


class StyleProfileRequest(BaseModel):
    texts: list[str] = Field(min_length=1)


class ReviewProjectOut(_Out):
    id: str
    project_id: str
    task_id: str | None = None
    title: str
    pico: dict = {}
    inclusion_criteria: list = []
    exclusion_criteria: list = []
    search_log: list = []
    screening: list = []
    prisma: dict = {}
    extractions: list = []
    rob: list = []
    meta_results: dict | None = None
    approval_status: str
    created_at: datetime | None = None

    @field_validator("pico", "prisma", mode="before")
    @classmethod
    def _parse_dict(cls, v):
        if isinstance(v, dict):
            return v
        try:
            return json.loads(v) if v else {}
        except (json.JSONDecodeError, TypeError):
            return {}

    @field_validator("inclusion_criteria", "exclusion_criteria", "search_log",
                     "screening", "extractions", "rob", mode="before")
    @classmethod
    def _parse_list(cls, v):
        if isinstance(v, list):
            return v
        try:
            return json.loads(v) if v else []
        except (json.JSONDecodeError, TypeError):
            return []

    @field_validator("meta_results", mode="before")
    @classmethod
    def _parse_meta(cls, v):
        if v is None or isinstance(v, dict):
            return v
        try:
            return json.loads(v)
        except (json.JSONDecodeError, TypeError):
            return None


class ReviewCreateRequest(BaseModel):
    project_id: str
    task_id: str | None = None
    title: str = Field(min_length=3)
    pico: dict = {}
    inclusion_criteria: list[str] = []
    exclusion_criteria: list[str] = []


class JournalMatchOut(_Out):
    id: str
    journal_name: str
    scope_summary: str | None = None
    match_score: float | None = None
    requirements: dict = {}
    apc_info: str | None = None
    checked_date: str | None = None
    source_link: str | None = None

    @field_validator("requirements", mode="before")
    @classmethod
    def _parse_dict(cls, v):
        if isinstance(v, dict):
            return v
        try:
            return json.loads(v) if v else {}
        except (json.JSONDecodeError, TypeError):
            return {}


class JournalMatchRequest(BaseModel):
    project_id: str
    task_id: str | None = None
    manuscript_id: str | None = None
    keywords: list[str] = Field(min_length=1)
    top_n: int = Field(default=5, ge=1, le=10)


class ReviewerCommentIn(BaseModel):
    reviewer: str | None = None
    comment: str = Field(min_length=3)


class ReviewerMatrixRequest(BaseModel):
    project_id: str
    manuscript_id: str | None = None
    comments: list[ReviewerCommentIn] = Field(min_length=1)


class ReviewerResponseOut(_Out):
    id: str
    reviewer: str | None = None
    comment: str
    response: str = ""
    manuscript_change: str = ""
    status: str


class ReviewerResponseUpdate(BaseModel):
    response: str | None = None
    manuscript_change: str | None = None
    status: str | None = None


class WatchCreateRequest(BaseModel):
    project_id: str
    name: str = Field(min_length=2)
    query: str = Field(min_length=2)
    sources: list[str] = ["semantic_scholar", "pubmed"]


class WatchTopicOut(_Out):
    id: str
    project_id: str
    name: str
    query: str
    sources: list = []
    schedule: str
    last_run: datetime | None = None
    created_at: datetime | None = None

    @field_validator("sources", mode="before")
    @classmethod
    def _parse_list(cls, v):
        if isinstance(v, list):
            return v
        try:
            return json.loads(v) if v else []
        except (json.JSONDecodeError, TypeError):
            return []


class AlertOut(_Out):
    id: str
    project_id: str
    watch_id: str | None = None
    paper_id: str | None = None
    alert_type: str
    title: str
    reason: str | None = None
    relevance_score: float | None = None
    source_link: str | None = None
    acknowledged: bool
    created_at: datetime | None = None


class SupervisorAdvanceWritingRequest(BaseModel):
    sections: list[str] | None = None
