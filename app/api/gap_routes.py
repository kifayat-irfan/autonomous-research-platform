"""Agent 3 API: gap discovery, novelty challenge, researcher review."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.agents import agent3_gap_discovery as agent3
from app.core.database import get_db
from app.models.schemas import (
    GapAnalyzeRequest,
    GapBriefOut,
    GapReviewRequest,
)

router = APIRouter(prefix="/gaps", tags=["gaps"])


@router.post("/analyze", response_model=list[GapBriefOut])
def analyze_gaps(payload: GapAnalyzeRequest, db: Session = Depends(get_db)):
    """Run Agent 3: evidence-matrix analysis + novelty challenge per gap."""
    briefs = agent3.run_gap_discovery(
        payload.project_id, db, task_id=payload.task_id,
        max_gaps=payload.max_gaps,
    )
    return [GapBriefOut.model_validate(b).model_dump() for b in briefs]


@router.get("", response_model=list[GapBriefOut])
def list_gaps(project_id: str | None = None, task_id: str | None = None,
              limit: int = 100, db: Session = Depends(get_db)):
    rows = agent3.list_gaps(db, project_id=project_id, task_id=task_id,
                            limit=limit)
    out = []
    for r in rows:
        b = agent3.generate_gap_brief(r.id, db)
        out.append(GapBriefOut.model_validate(b).model_dump())
    return out


@router.get("/{gap_id}", response_model=GapBriefOut)
def get_gap_brief(gap_id: str, db: Session = Depends(get_db)):
    try:
        brief = agent3.generate_gap_brief(gap_id, db)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return GapBriefOut.model_validate(brief).model_dump()


@router.post("/{gap_id}/challenge", response_model=dict)
def rechallenge_gap(gap_id: str, db: Session = Depends(get_db)):
    """Re-run the novelty counter-search for a gap (new prior art may exist)."""
    gap = agent3.get_gap(db, gap_id)
    if gap is None:
        raise HTTPException(status_code=404, detail="Gap not found")
    import json as _json
    challenge = agent3.execute_novelty_challenge_search(gap.gap_statement, db)
    gap.counter_search_query = challenge["counter_query"]
    gap.counter_search_results = _json.dumps(challenge["results"])
    gap.verification_status = challenge["verdict"]
    # A newly disproved gap loses its prior approval automatically.
    if challenge["verdict"] == "challenged_disproved":
        gap.researcher_decision = "pending"
        gap.researcher_note = ((gap.researcher_note or "") +
                               " [Auto: re-challenge found prior art.]").strip()
    db.commit()
    return agent3.generate_gap_brief(gap_id, db)


@router.post("/{gap_id}/review", response_model=GapBriefOut)
def review_gap(gap_id: str, payload: GapReviewRequest,
               db: Session = Depends(get_db)):
    """Researcher approve/reject a gap (disproved gaps cannot be approved)."""
    try:
        gap = agent3.review_gap(db, gap_id, payload.approved, payload.note,
                                payload.design_type)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    brief = agent3.generate_gap_brief(gap.id, db)
    return GapBriefOut.model_validate(brief).model_dump()
