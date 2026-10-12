"""Agent 8 — Project Management and Persistent Memory.

Persists researcher decisions, maintains the live workspace state and an
auditable activity feed.

COMPULSORY: every saved decision / memory item references the initiating
`task_id` and an `evidence_link` / `source_url` where one exists.
"""
import json
import logging
from typing import Dict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.schemas import ActivityLog, Project, ProjectDecision

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

def create_project(db: Session, name: str, description: str | None = None,
                   keywords: list[str] | None = None) -> Project:
    project = Project(
        name=name,
        description=description,
        keywords=json.dumps(keywords or []),
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


def get_or_create_project(db: Session, name: str,
                          description: str | None = None) -> Project:
    project = db.scalar(select(Project).where(Project.name == name))
    if project is None:
        project = create_project(db, name, description)
    return project


def get_project(db: Session, project_id: str) -> Project | None:
    return db.get(Project, project_id)


def list_projects(db: Session, limit: int = 100) -> list[Project]:
    return list(
        db.scalars(select(Project).order_by(Project.created_at.desc()).limit(limit)).all()
    )


# ---------------------------------------------------------------------------
# 1. Decisions
# ---------------------------------------------------------------------------

def save_project_decision(db: Session, project_id: str, decision_type: str,
                          details: Dict, user_approved: bool,
                          task_id: str | None = None,
                          evidence_link: str | None = None) -> ProjectDecision:
    """Persist a human choice / approved paper / rejected hypothesis.

    The decision is always tied to the initiating task_id and, when
    available, an evidence_link so it can be traced back to sources.
    """
    project = get_project(db, project_id)
    if project is None:
        raise ValueError(f"Unknown project_id: {project_id}")
    decision = ProjectDecision(
        project_id=project_id,
        task_id=task_id,
        decision_type=decision_type,
        details=json.dumps(details or {}),
        user_approved=bool(user_approved),
        evidence_link=evidence_link,
    )
    db.add(decision)
    db.commit()
    db.refresh(decision)
    log.info("Decision saved: project=%s type=%s approved=%s task=%s",
             project_id, decision_type, user_approved, task_id)
    return decision


def get_latest_decision(db: Session, project_id: str,
                        decision_type: str) -> Dict | None:
    """Return the most recent decision of a given type for a project.

    Used e.g. to retrieve a persisted style profile for manuscript drafting.
    """
    row = db.scalars(
        select(ProjectDecision)
        .where(ProjectDecision.project_id == project_id,
               ProjectDecision.decision_type == decision_type)
        .order_by(ProjectDecision.created_at.desc())
    ).first()
    if row is None:
        return None
    try:
        details = json.loads(row.details or "{}")
    except Exception:
        details = {}
    return {"id": row.id, "decision_type": row.decision_type,
            "details": details, "created_at": row.created_at}


# ---------------------------------------------------------------------------
# 2. Workspace state
# ---------------------------------------------------------------------------

def get_project_workspace_state(db: Session, project_id: str) -> Dict:
    """Live research workspace: summary, paper bank, tasks, evidence, feed."""
    from app.services import evidence_service, paper_service, task_service

    project = get_project(db, project_id)
    if project is None:
        raise ValueError(f"Unknown project_id: {project_id}")

    papers = paper_service.list_papers(db, project_id=project_id, limit=500)
    tasks = task_service.list_tasks(db, project_id=project_id, limit=200)
    evidence = evidence_service.list_evidence(db, project_id=project_id, limit=500)
    decisions = list(
        db.scalars(
            select(ProjectDecision)
            .where(ProjectDecision.project_id == project_id)
            .order_by(ProjectDecision.created_at.desc())
            .limit(200)
        ).all()
    )
    feed = list(
        db.scalars(
            select(ActivityLog)
            .where(ActivityLog.project_id == project_id)
            .order_by(ActivityLog.created_at.desc())
            .limit(200)
        ).all()
    )

    return {
        "project": {
            "id": project.id, "name": project.name,
            "description": project.description, "status": project.status,
        },
        "summary": {
            "papers": len(papers),
            "papers_with_pdf": sum(1 for p in papers if p.pdf_path),
            "papers_need_upload": sum(
                1 for p in papers if p.status == "needs_pdf_upload"),
            "active_tasks": sum(1 for t in tasks
                                if t.status in ("queued", "running")),
            "tasks_awaiting_input": sum(1 for t in tasks if t.status in (
                "needs_pdf_upload", "needs_approval")),
            "evidence_items": len(evidence),
            "decisions": len(decisions),
        },
        "paper_bank": [
            {"id": p.id, "title": p.title, "doi": p.doi,
             "status": p.status, "source_link": p.source_link,
             "source_api": p.source_api, "pub_year": p.pub_year}
            for p in papers
        ],
        "tasks": [
            {"task_id": t.task_id, "query": t.query, "status": t.status,
             "agent_name": t.agent_name,
             "duration_seconds": t.duration_seconds}
            for t in tasks
        ],
        "evidence_graph": [
            {"id": e.id, "paper_id": e.paper_id,
             "claim_text": (e.claim_text or "")[:160],
             "page_number": e.page_number, "source_url": e.source_url,
             "verification_state": e.verification_state}
            for e in evidence
        ],
        "decisions": [
            {"id": d.id, "decision_type": d.decision_type,
             "user_approved": d.user_approved, "task_id": d.task_id,
             "evidence_link": d.evidence_link, "details": d.details_dict}
            for d in decisions
        ],
        "activity_feed": [
            {"agent_name": a.agent_name, "action": a.action,
             "task_id": a.task_id, "evidence_link": a.evidence_link,
             "created_at": a.created_at.isoformat() if a.created_at else None}
            for a in feed
        ],
    }


# ---------------------------------------------------------------------------
# 3. Activity feed
# ---------------------------------------------------------------------------

def log_activity_feed(db: Session, project_id: str, agent_name: str,
                      action: str, evidence_link: str | None = None,
                      task_id: str | None = None) -> ActivityLog:
    """Append an auditable entry to the project's activity feed."""
    project = get_project(db, project_id)
    if project is None:
        raise ValueError(f"Unknown project_id: {project_id}")
    entry = ActivityLog(
        project_id=project_id,
        task_id=task_id,
        agent_name=agent_name,
        action=action,
        evidence_link=evidence_link,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def get_activity_feed(db: Session, project_id: str, limit: int = 200) -> list[ActivityLog]:
    return list(
        db.scalars(
            select(ActivityLog)
            .where(ActivityLog.project_id == project_id)
            .order_by(ActivityLog.created_at.desc())
            .limit(limit)
        ).all()
    )
