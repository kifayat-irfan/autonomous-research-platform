"""Agent 6 API: style profiles, manuscript drafting, Word export."""
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.agents import agent6_writing as agent6
from app.core.database import get_db
from app.models.schemas import (
    ManuscriptCreateRequest,
    ManuscriptOut,
    StyleProfileRequest,
)

router = APIRouter(prefix="/writing", tags=["writing"])


@router.post("/style-profile", response_model=dict)
def style_profile(payload: StyleProfileRequest):
    """Build a style profile from the researcher's own texts."""
    try:
        return agent6.build_style_profile(payload.texts)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/manuscripts", response_model=ManuscriptOut)
def draft_manuscript(payload: ManuscriptCreateRequest,
                     db: Session = Depends(get_db)):
    """Draft a manuscript strictly from project materials (Agent 6)."""
    try:
        ms = agent6.draft_manuscript(
            db, payload.project_id, task_id=payload.task_id,
            protocol_id=payload.protocol_id, title=payload.title,
            sections=payload.sections,
            style_profile=payload.style_profile)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return ManuscriptOut.model_validate(ms).model_dump()


@router.get("/manuscripts", response_model=list[ManuscriptOut])
def list_manuscripts(project_id: str | None = None, task_id: str | None = None,
                     limit: int = 100, db: Session = Depends(get_db)):
    rows = agent6.list_manuscripts(db, project_id=project_id, task_id=task_id,
                                   limit=limit)
    return [ManuscriptOut.model_validate(agent6.get_manuscript_dict(r.id, db)).model_dump()
            for r in rows]


@router.get("/manuscripts/{manuscript_id}", response_model=ManuscriptOut)
def get_manuscript(manuscript_id: str, db: Session = Depends(get_db)):
    try:
        ms = agent6.get_manuscript_dict(manuscript_id, db)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return ManuscriptOut.model_validate(ms).model_dump()


@router.post("/manuscripts/{manuscript_id}/docx", response_model=dict)
def export_docx(manuscript_id: str, db: Session = Depends(get_db)):
    """Export the manuscript to Word .docx."""
    try:
        path = agent6.export_docx(manuscript_id, db)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"manuscript_id": manuscript_id, "docx_path": path}


@router.get("/manuscripts/{manuscript_id}/docx")
def download_docx(manuscript_id: str, db: Session = Depends(get_db)):
    try:
        ms = agent6.get_manuscript_dict(manuscript_id, db)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    if not ms.get("docx_path") or not Path(ms["docx_path"]).exists():
        raise HTTPException(status_code=404,
                            detail="No .docx yet — export it first.")
    return FileResponse(ms["docx_path"],
                        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        filename=f"manuscript_{manuscript_id[:8]}.docx")


@router.post("/manuscripts/{manuscript_id}/review", response_model=ManuscriptOut)
def review_manuscript(manuscript_id: str, approved: bool = True, note: str = "",
                      db: Session = Depends(get_db)):
    """Researcher approve/reject a manuscript draft (gates release)."""
    try:
        ms = agent6.review_manuscript(db, manuscript_id, approved, note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    out = agent6.get_manuscript_dict(ms.id, db)
    return ManuscriptOut.model_validate(out).model_dump()
