"""Agent 10 API: systematic review workspace (PRISMA 2020)."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.agents import agent10_systematic_review as agent10
from app.core.database import get_db
from app.models.schemas import AnalysisReviewRequest, ReviewCreateRequest, ReviewProjectOut

router = APIRouter(prefix="/reviews", tags=["reviews"])


@router.post("", response_model=ReviewProjectOut)
def create_review(payload: ReviewCreateRequest, db: Session = Depends(get_db)):
    r = agent10.create_review_project(
        db, payload.project_id, payload.title, pico=payload.pico,
        inclusion_criteria=payload.inclusion_criteria,
        exclusion_criteria=payload.exclusion_criteria,
        task_id=payload.task_id)
    return ReviewProjectOut.model_validate(r).model_dump()


@router.get("", response_model=list[ReviewProjectOut])
def list_reviews(project_id: str | None = None, limit: int = 100,
                 db: Session = Depends(get_db)):
    rows = agent10.list_reviews(db, project_id=project_id, limit=limit)
    return [ReviewProjectOut.model_validate(agent10.get_review_dict(r.id, db)).model_dump()
            for r in rows]


@router.get("/{review_id}", response_model=ReviewProjectOut)
def get_review(review_id: str, db: Session = Depends(get_db)):
    try:
        r = agent10.get_review_dict(review_id, db)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return ReviewProjectOut.model_validate(r).model_dump()


@router.post("/{review_id}/searches", response_model=ReviewProjectOut)
def log_search(review_id: str, source: str, query: str, hits: int,
               date: str | None = None, db: Session = Depends(get_db)):
    try:
        r = agent10.log_search(db, review_id, source, query, hits, date)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return ReviewProjectOut.model_validate(r).model_dump()


@router.post("/{review_id}/screening", response_model=ReviewProjectOut)
def record_screening(review_id: str, title: str, decision: str,
                     reason: str = "", stage: str = "title_abstract",
                     paper_id: str | None = None,
                     source_link: str | None = None,
                     db: Session = Depends(get_db)):
    try:
        r = agent10.record_screening(db, review_id, title, decision, reason,
                                     stage, paper_id, source_link)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return ReviewProjectOut.model_validate(r).model_dump()


@router.post("/{review_id}/prisma", response_model=dict)
def prisma(review_id: str, db: Session = Depends(get_db)):
    try:
        return agent10.prisma_flow(db, review_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/{review_id}/effects", response_model=ReviewProjectOut)
def extract_effect(review_id: str, paper_id: str, effect_type: str,
                   value: float, se: float | None = None,
                   ci_lower: float | None = None, ci_upper: float | None = None,
                   n: int | None = None, outcome: str = "",
                   standardize_to: str = "d",
                   source_link: str | None = None,
                   db: Session = Depends(get_db)):
    try:
        r = agent10.extract_effect(db, review_id, paper_id, effect_type, value,
                                   se, ci_lower, ci_upper, n, outcome,
                                   standardize_to, source_link)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return ReviewProjectOut.model_validate(r).model_dump()


class RobAssessRequest(BaseModel):
    paper_id: str
    domains: dict


@router.post("/{review_id}/rob", response_model=ReviewProjectOut)
def rob_assess(review_id: str, payload: RobAssessRequest,
               db: Session = Depends(get_db)):
    try:
        r = agent10.rob_assess(db, review_id, payload.paper_id, payload.domains)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return ReviewProjectOut.model_validate(r).model_dump()


@router.post("/{review_id}/meta", response_model=dict)
def meta_analysis(review_id: str, db: Session = Depends(get_db)):
    try:
        return agent10.run_meta_analysis(db, review_id)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/{review_id}/review", response_model=ReviewProjectOut)
def review_review(review_id: str, payload: AnalysisReviewRequest,
                  db: Session = Depends(get_db)):
    try:
        r = agent10.review_review(db, review_id, payload.approved, payload.note)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    out = agent10.get_review_dict(r.id, db)
    return ReviewProjectOut.model_validate(out).model_dump()
