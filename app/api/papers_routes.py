"""Paper bank API: list, inspect, update status, dashboard stats."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.schemas import PaperOut, PaperStatusUpdate
from app.services import evidence_service, paper_service, task_service

router = APIRouter(prefix="/papers", tags=["papers"])


@router.get("", response_model=list[PaperOut])
def list_papers(status: str | None = None, project_id: str | None = None,
                q: str | None = None, limit: int = 100,
                db: Session = Depends(get_db)):
    rows = paper_service.list_papers(db, status=status, project_id=project_id,
                                     q=q, limit=limit)
    return [PaperOut.model_validate(p).model_dump() for p in rows]


@router.get("/stats", response_model=dict)
def dashboard_stats(project_id: str | None = None,
                    db: Session = Depends(get_db)):
    """Home-dashboard numbers: papers, evidence, tasks, action-needed items."""
    pstats = paper_service.stats(db)
    return {
        "papers": pstats["total_papers"],
        "papers_by_status": pstats["by_status"],
        "papers_need_upload": pstats["by_status"].get("needs_pdf_upload", 0),
        "evidence_items": evidence_service.count(db, project_id),
        "tasks_by_status": task_service.counts_by_status(db),
        "tasks_need_approval": task_service.counts_by_status(db).get(
            "needs_approval", 0),
        "tasks_need_upload": task_service.counts_by_status(db).get(
            "needs_pdf_upload", 0),
    }


@router.get("/{paper_id}", response_model=PaperOut)
def get_paper(paper_id: str, db: Session = Depends(get_db)):
    paper = paper_service.get_paper(db, paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="Paper not found")
    return PaperOut.model_validate(paper).model_dump()


@router.patch("/{paper_id}/status", response_model=PaperOut)
def update_paper_status(paper_id: str, payload: PaperStatusUpdate,
                        db: Session = Depends(get_db)):
    paper = paper_service.get_paper(db, paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="Paper not found")
    try:
        paper_service.set_status(db, paper, payload.status)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return PaperOut.model_validate(paper).model_dump()
