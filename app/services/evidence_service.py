"""Business logic for Evidence records."""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.schemas import VERIFICATION_STATES, Evidence


def save_evidence(db: Session, evidence_dict: dict) -> Evidence:
    ev = Evidence(
        paper_id=evidence_dict["paper_id"],
        project_id=evidence_dict.get("project_id"),
        task_id=evidence_dict.get("task_id"),
        claim_text=evidence_dict.get("claim_text", ""),
        objective=evidence_dict.get("objective"),
        method=evidence_dict.get("method"),
        sample_size=evidence_dict.get("sample_size"),
        page_number=evidence_dict.get("page_number"),
        paragraph_quote=evidence_dict.get("paragraph_quote"),
        source_url=evidence_dict.get("source_url"),
        verification_state=evidence_dict.get("verification_state", "machine_extracted"),
    )
    if ev.verification_state not in VERIFICATION_STATES:
        ev.verification_state = "machine_extracted"
    db.add(ev)
    db.commit()
    db.refresh(ev)
    return ev


def list_evidence(db: Session, paper_id: str | None = None,
                  project_id: str | None = None,
                  verification_state: str | None = None,
                  limit: int = 100) -> list[Evidence]:
    stmt = select(Evidence).order_by(Evidence.created_at.desc())
    if paper_id:
        stmt = stmt.where(Evidence.paper_id == paper_id)
    if project_id:
        stmt = stmt.where(Evidence.project_id == project_id)
    if verification_state:
        stmt = stmt.where(Evidence.verification_state == verification_state)
    return list(db.scalars(stmt.limit(limit)).all())


def set_verification_state(db: Session, evidence: Evidence, state: str) -> Evidence:
    if state not in VERIFICATION_STATES:
        raise ValueError(f"Unknown verification state: {state!r}")
    evidence.verification_state = state
    db.commit()
    db.refresh(evidence)
    return evidence


def bulk_set_verification_state(db: Session, state: str,
                                paper_id: str | None = None,
                                project_id: str | None = None,
                                only_state: str | None = None) -> int:
    """Mark many evidence rows at once. Returns the number updated.

    Args:
        state: target state (must be in VERIFICATION_STATES).
        paper_id / project_id: scope the bulk update.
        only_state: only rows currently in this state are updated
            (e.g. only 'unverified' → 'verified', leaving already
            'rejected' rows untouched).
    """
    if state not in VERIFICATION_STATES:
        raise ValueError(f"Unknown verification state: {state!r}")
    stmt = select(Evidence)
    if paper_id:
        stmt = stmt.where(Evidence.paper_id == paper_id)
    if project_id:
        stmt = stmt.where(Evidence.project_id == project_id)
    if only_state:
        stmt = stmt.where(Evidence.verification_state == only_state)
    rows = list(db.scalars(stmt).all())
    for ev in rows:
        ev.verification_state = state
    db.commit()
    return len(rows)


def count(db: Session, project_id: str | None = None) -> int:
    stmt = select(func.count(Evidence.id))
    if project_id:
        stmt = stmt.where(Evidence.project_id == project_id)
    return db.scalar(stmt) or 0
