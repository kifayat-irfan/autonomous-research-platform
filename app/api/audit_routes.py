"""Agent 7 API: claim audits, numerical checks, audit reports."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.agents import agent7_verification as agent7
from app.core.database import get_db
from app.models.schemas import (
    AuditClaimsRequest,
    AuditNumericsRequest,
    AuditReportOut,
    AuditReportRequest,
)

router = APIRouter(prefix="/audit", tags=["audit"])


@router.post("/claims", response_model=dict)
def audit_claims(payload: AuditClaimsRequest, db: Session = Depends(get_db)):
    """Verify cited claims against indexed passages / paper records."""
    return agent7.audit_claim_citations(
        payload.manuscript_or_text, payload.cited_paper_ids, db)


@router.post("/numerics", response_model=dict)
def audit_numerics(payload: AuditNumericsRequest):
    """Cross-check reported numbers against extraction records."""
    return agent7.audit_numerical_consistency(
        [r.model_dump() for r in payload.reported_table],
        payload.source_evidence)


@router.post("/reports", response_model=AuditReportOut)
def create_report(payload: AuditReportRequest, db: Session = Depends(get_db)):
    """Full pass/fail audit. HIGH severity -> overall 'blocked'."""
    report = agent7.generate_audit_report(
        payload.project_id, db, task_id=payload.task_id,
        protocol_id=payload.protocol_id,
        manuscript_text=payload.manuscript_text,
        cited_paper_ids=payload.cited_paper_ids,
    )
    return AuditReportOut.model_validate(report).model_dump()


@router.get("/reports", response_model=list[AuditReportOut])
def list_reports(project_id: str | None = None, limit: int = 50,
                 db: Session = Depends(get_db)):
    rows = agent7.list_reports(db, project_id=project_id, limit=limit)
    return [AuditReportOut.model_validate(agent7.get_report_dict(r.id, db)).model_dump()
            for r in rows]


@router.get("/reports/{report_id}", response_model=AuditReportOut)
def get_report(report_id: str, db: Session = Depends(get_db)):
    try:
        report = agent7.get_report_dict(report_id, db)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return AuditReportOut.model_validate(report).model_dump()


@router.post("/flags/{flag_id}/resolve", response_model=dict)
def resolve_flag(flag_id: str, db: Session = Depends(get_db)):
    try:
        f = agent7.resolve_flag(db, flag_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"flag_id": f.id, "status": f.status}
