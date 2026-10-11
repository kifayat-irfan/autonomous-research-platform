"""Agent 9 API: cross-domain bridges, feasibility, concept notes."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.agents import agent9_cross_domain as agent9
from app.core.database import get_db
from app.models.schemas import ConceptNoteOut, CrossDomainIdeaRequest
from app.services import evidence_service

router = APIRouter(prefix="/cross-domain", tags=["cross-domain"])


@router.post("/ideas", response_model=list[ConceptNoteOut])
def generate_ideas(payload: CrossDomainIdeaRequest,
                   db: Session = Depends(get_db)):
    """Identify cross-domain bridges from the project's evidence.

    Ideas are hypotheses pending Agent 3 novelty search.
    """
    ev_rows = evidence_service.list_evidence(db, project_id=payload.project_id,
                                             limit=200)
    source_evidence = [{
        "paper_id": e.paper_id, "claim_text": e.claim_text,
        "objective": e.objective, "method": e.method,
    } for e in ev_rows]
    if not source_evidence:
        raise HTTPException(
            status_code=409,
            detail="No evidence in this project yet — run discovery + extraction first.")
    notes = agent9.identify_cross_domain_bridges(
        source_evidence, payload.target_domain, db,
        project_id=payload.project_id, task_id=payload.task_id,
        source_domain=payload.source_domain, max_ideas=payload.max_ideas,
    )
    return [ConceptNoteOut.model_validate(n).model_dump() for n in notes]


@router.get("/notes", response_model=list[ConceptNoteOut])
def list_notes(project_id: str | None = None, limit: int = 100,
               db: Session = Depends(get_db)):
    rows = agent9.list_concept_notes(db, project_id=project_id, limit=limit)
    return [ConceptNoteOut.model_validate(agent9.generate_concept_note(r.id, db)).model_dump()
            for r in rows]


@router.get("/notes/{note_id}", response_model=ConceptNoteOut)
def get_note(note_id: str, db: Session = Depends(get_db)):
    try:
        note = agent9.generate_concept_note(note_id, db)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return ConceptNoteOut.model_validate(note).model_dump()


@router.post("/notes/{note_id}/challenge", response_model=dict)
def challenge_note(note_id: str, db: Session = Depends(get_db)):
    """Run the concept through Agent 3's novelty counter-search."""
    try:
        return agent9.challenge_concept_note(note_id, db)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
