"""Agent 2 API: PDF parsing, passage indexing, evidence extraction."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.agents import agent2_extraction as agent2
from app.core.database import get_db
from app.models.schemas import EvidenceOut, ExtractRequest
from app.services import evidence_service, paper_service

router = APIRouter(prefix="/extraction", tags=["extraction"])


@router.post("/papers/{paper_id}/parse", response_model=dict)
def parse_and_index(paper_id: str, db: Session = Depends(get_db)):
    """Parse a paper's PDF into passages and index them in ChromaDB."""
    paper = paper_service.get_paper(db, paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="Paper not found")
    if not paper.pdf_path:
        raise HTTPException(
            status_code=409,
            detail="Paper has no PDF yet (status=%s). Upload one first." % paper.status,
        )
    try:
        parsed = agent2.parse_pdf_passages(paper.pdf_path, paper_id)
        n = agent2.index_passages_to_chromadb(
            paper_id, parsed["passages"], source_doi_or_url=paper.source_link
        )
    except FileNotFoundError:
        raise HTTPException(status_code=410, detail="PDF file missing on disk")
    except RuntimeError as exc:
        raise HTTPException(status_code=501, detail=str(exc))
    paper_service.set_status(db, paper, "full_text_parsed")
    return {
        "paper_id": paper_id,
        "pages": parsed["pages"],
        "passages_indexed": n,
        "skipped_pages": parsed["skipped_pages"],
        "status": paper.status,
    }


@router.post("/papers/{paper_id}/extract", response_model=dict)
def extract_evidence(paper_id: str, payload: ExtractRequest,
                     db: Session = Depends(get_db)):
    """Run Agent 2 extraction and persist anchored Evidence rows."""
    paper = paper_service.get_paper(db, paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="Paper not found")
    if paper.status != "full_text_parsed":
        raise HTTPException(
            status_code=409,
            detail=f"Parse the PDF first (current status={paper.status}).",
        )
    try:
        extract = agent2.extract_structured_evidence(
            paper_id, payload.query,
            source_doi_or_url=paper.source_link,
            top_k=payload.top_k,
            db=db,
            task_id=payload.task_id,
            project_id=payload.project_id or paper.project_id,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=501, detail=str(exc))
    return extract


@router.get("/evidence", response_model=list[EvidenceOut])
def get_evidence(paper_id: str | None = None, project_id: str | None = None,
                 verification_state: str | None = None, limit: int = 100,
                 db: Session = Depends(get_db)):
    """List evidence with optional filters. Every row carries source_url+page."""
    rows = evidence_service.list_evidence(
        db, paper_id=paper_id, project_id=project_id,
        verification_state=verification_state, limit=limit,
    )
    return [EvidenceOut.model_validate(r).model_dump() for r in rows]


@router.post("/evidence/{evidence_id}/verify", response_model=EvidenceOut)
def verify_evidence(evidence_id: str, state: str = "verified",
                    db: Session = Depends(get_db)):
    """Researcher marks an extraction verified / needs_review / rejected."""
    from sqlalchemy import select
    from app.models.schemas import Evidence
    ev = db.scalar(select(Evidence).where(Evidence.id == evidence_id))
    if ev is None:
        raise HTTPException(status_code=404, detail="Evidence not found")
    evidence_service.set_verification_state(db, ev, state)
    return EvidenceOut.model_validate(ev).model_dump()


@router.post("/evidence/bulk-verify", response_model=dict)
def bulk_verify_evidence(state: str = "verified",
                         paper_id: str | None = None,
                         project_id: str | None = None,
                         only_state: str | None = None,
                         db: Session = Depends(get_db)):
    """Mark many evidence rows verified (or another state) at once.

    By default only rows currently 'unverified' are touched, so already
    rejected items are never silently flipped.
    """
    try:
        n = evidence_service.bulk_set_verification_state(
            db, state, paper_id=paper_id, project_id=project_id,
            only_state=only_state or "unverified")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"updated": n, "state": state}
