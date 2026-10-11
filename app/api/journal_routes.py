"""Agent 11 API: journal matching, reviewer response matrix."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.agents import agent11_journal as agent11
from app.core.database import get_db
from app.models.schemas import (
    JournalMatchOut,
    JournalMatchRequest,
    ReviewerMatrixRequest,
    ReviewerResponseOut,
    ReviewerResponseUpdate,
)

router = APIRouter(prefix="/journals", tags=["journals"])


@router.post("/match", response_model=list[JournalMatchOut])
def match_journals(payload: JournalMatchRequest, db: Session = Depends(get_db)):
    matches = agent11.match_journals(
        db, payload.project_id, payload.keywords, task_id=payload.task_id,
        manuscript_id=payload.manuscript_id, top_n=payload.top_n)
    return [JournalMatchOut.model_validate(m).model_dump() for m in matches]


@router.get("/matches", response_model=list[JournalMatchOut])
def list_matches(project_id: str | None = None, limit: int = 50,
                 db: Session = Depends(get_db)):
    rows = agent11.list_matches(db, project_id=project_id, limit=limit)
    return [JournalMatchOut.model_validate(r).model_dump() for r in rows]


@router.post("/responses", response_model=list[ReviewerResponseOut])
def build_matrix(payload: ReviewerMatrixRequest, db: Session = Depends(get_db)):
    rows = agent11.build_reviewer_response_matrix(
        db, payload.project_id,
        [c.model_dump() for c in payload.comments],
        manuscript_id=payload.manuscript_id)
    return [ReviewerResponseOut.model_validate(r).model_dump() for r in rows]


@router.get("/responses", response_model=list[ReviewerResponseOut])
def list_responses(project_id: str | None = None,
                   manuscript_id: str | None = None, limit: int = 200,
                   db: Session = Depends(get_db)):
    rows = agent11.list_responses(db, project_id=project_id,
                                  manuscript_id=manuscript_id, limit=limit)
    return [ReviewerResponseOut.model_validate(r).model_dump() for r in rows]


@router.patch("/responses/{response_id}", response_model=ReviewerResponseOut)
def update_response(response_id: str, payload: ReviewerResponseUpdate,
                    db: Session = Depends(get_db)):
    try:
        r = agent11.update_response(
            db, response_id, response=payload.response,
            manuscript_change=payload.manuscript_change,
            status=payload.status)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return ReviewerResponseOut.model_validate(r).model_dump()
