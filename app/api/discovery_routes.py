"""Agent 1 API: literature discovery, OA download, manual PDF upload."""
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.agents.agent1_discovery import (
    check_open_access_and_download,
    run_discovery,
)
from app.core.config import PDF_DIR
from app.core.database import get_db
from app.models.schemas import DiscoveryRequest, PaperOut
from app.services import paper_service

router = APIRouter(prefix="/discovery", tags=["discovery"])


@router.post("/search", response_model=dict)
def search_papers(payload: DiscoveryRequest, db: Session = Depends(get_db)):
    """Run Agent 1 discovery and persist deduplicated paper records."""
    papers = run_discovery(
        query=payload.query,
        limit=payload.limit,
        sources=payload.sources,
        download_pdfs=payload.download_pdfs,
    )
    saved = [
        paper_service.upsert_paper(db, p, project_id=payload.project_id)
        for p in papers
    ]
    return {
        "query": payload.query,
        "sources": payload.sources,
        "count": len(saved),
        "need_upload": sum(1 for p in saved if p.status == "needs_pdf_upload"),
        "papers": [PaperOut.model_validate(p).model_dump() for p in saved],
    }


@router.post("/papers/{paper_id}/download", response_model=PaperOut)
def download_oa_pdf(paper_id: str, db: Session = Depends(get_db)):
    """(Re)attempt the legal OA PDF download for a paper."""
    paper = paper_service.get_paper(db, paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="Paper not found")
    record = {
        "doi": paper.doi, "title": paper.title, "oa_url": paper.oa_url,
        "pdf_path": None, "status": paper.status, "source_api": paper.source_api,
        "source_link": paper.source_link,
    }
    updated = check_open_access_and_download(record, str(PDF_DIR))
    paper.oa_url = updated["oa_url"]
    if updated.get("pdf_path"):
        paper.pdf_path = updated["pdf_path"]
    paper_service.set_status(db, paper, updated["status"])
    return PaperOut.model_validate(paper)


@router.post("/papers/{paper_id}/upload-pdf", response_model=PaperOut)
def upload_pdf(paper_id: str, file: UploadFile = File(...),
               db: Session = Depends(get_db)):
    """Manual PDF upload for papers stuck in `needs_pdf_upload`.

    Accepts only real PDF files; stores under data/pdfs/<paper_id>.pdf.
    """
    paper = paper_service.get_paper(db, paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="Paper not found")
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only .pdf files are accepted")

    dest = Path(PDF_DIR) / f"{paper_id}.pdf"
    with open(dest, "wb") as out:
        shutil.copyfileobj(file.file, out)

    # Verify it is really a PDF (magic bytes).
    with open(dest, "rb") as f:
        if not f.read(5).lstrip().startswith(b"%PDF"):
            dest.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail="Uploaded file is not a PDF")

    paper.pdf_path = str(dest)
    paper_service.set_status(db, paper, "pdf_acquired")
    return PaperOut.model_validate(paper)
