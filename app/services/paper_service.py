"""Business logic for Paper records: normalization, upsert, queries."""
import json
import re
from datetime import date

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.schemas import PAPER_STATUSES, Evidence, Paper


def normalize_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    d = doi.strip().lower()
    d = re.sub(r"^https?://(dx\.)?doi\.org/", "", d)
    d = re.sub(r"^doi:\s*", "", d)
    return d or None


def normalize_title(title: str | None) -> str:
    t = (title or "").lower()
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _authors_to_json(authors) -> str | None:
    if not authors:
        return None
    if isinstance(authors, str):
        return json.dumps([authors])
    return json.dumps([str(a) for a in authors])


def _year_to_date(year) -> date | None:
    try:
        y = int(year)
        if 1500 <= y <= 2100:
            return date(y, 1, 1)
    except (TypeError, ValueError):
        pass
    return None


def upsert_paper(db: Session, paper_dict: dict, project_id: str | None = None,
                 task_id: str | None = None) -> Paper:
    """Insert or update a paper by DOI, falling back to normalized title.

    Idempotent: re-running discovery never creates duplicate canonical records
    (acceptance test: "run a saved query twice without duplicates").
    """
    doi = normalize_doi(paper_dict.get("doi"))
    title = (paper_dict.get("title") or "").strip()
    norm_title = normalize_title(title)

    paper: Paper | None = None
    if doi:
        paper = db.scalar(select(Paper).where(Paper.doi == doi))
    if paper is None and norm_title:
        # Title fallback: compare normalized titles in Python (portable).
        candidates = db.scalars(select(Paper)).all()
        for c in candidates:
            if normalize_title(c.title) == norm_title:
                paper = c
                break

    fields = {
        "doi": doi,
        "title": title,
        "authors": _authors_to_json(paper_dict.get("authors")),
        "journal": paper_dict.get("journal"),
        "pub_date": _year_to_date(paper_dict.get("year") or paper_dict.get("pub_year")),
        "abstract": paper_dict.get("abstract"),
        "oa_url": paper_dict.get("oa_url"),
        "source_api": paper_dict.get("source_api"),
        "source_link": paper_dict.get("source_link"),
    }
    if project_id:
        fields["project_id"] = project_id
    if task_id:
        fields["task_id"] = task_id
    if paper_dict.get("pdf_path"):
        fields["pdf_path"] = paper_dict["pdf_path"]
    new_status = paper_dict.get("status")
    if new_status in PAPER_STATUSES:
        if paper is None:
            fields["status"] = new_status
        else:
            # Never downgrade: a paper that already has an acquired PDF
            # (e.g. via manual upload) keeps its status when a later
            # discovery re-runs and finds no OA copy.
            _RANK = {"metadata_only": 0, "restricted": 0,
                     "needs_pdf_upload": 1, "oa_url_found": 1,
                     "pdf_acquired": 2, "extraction_failed": 2,
                     "full_text_parsed": 3}
            if _RANK.get(new_status, 0) >= _RANK.get(paper.status, 0):
                fields["status"] = new_status

    if paper is None:
        paper = Paper(**{k: v for k, v in fields.items() if v is not None})
        db.add(paper)
    else:
        for k, v in fields.items():
            if v is not None:
                setattr(paper, k, v)
    db.commit()
    db.refresh(paper)
    return paper


def get_paper(db: Session, paper_id: str) -> Paper | None:
    return db.get(Paper, paper_id)


def list_papers(db: Session, status: str | None = None,
                project_id: str | None = None, q: str | None = None,
                limit: int = 100) -> list[Paper]:
    stmt = select(Paper).order_by(Paper.created_at.desc())
    if status:
        stmt = stmt.where(Paper.status == status)
    if project_id:
        stmt = stmt.where(Paper.project_id == project_id)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Paper.title.ilike(like), Paper.abstract.ilike(like)))
    return list(db.scalars(stmt.limit(limit)).all())


def set_status(db: Session, paper: Paper, status: str) -> Paper:
    if status not in PAPER_STATUSES:
        raise ValueError(f"Unknown paper status: {status!r}")
    paper.status = status
    db.commit()
    db.refresh(paper)
    return paper


def delete_paper(db: Session, paper_id: str) -> bool:
    """Delete a paper, its PDF file and its evidence items. Returns True if found."""
    from pathlib import Path

    paper = get_paper(db, paper_id)
    if paper is None:
        return False
    if paper.pdf_path:
        try:
            Path(paper.pdf_path).unlink(missing_ok=True)
        except OSError:
            pass
    # Remove evidence anchored to this paper first (FK is non-nullable).
    for ev in db.scalars(select(Evidence).where(Evidence.paper_id == paper_id)).all():
        db.delete(ev)
    db.delete(paper)
    db.commit()
    return True


def clear_project_papers(db: Session, project_id: str) -> int:
    """Delete ALL papers (and their PDFs/evidence) in a project. Returns count."""
    papers = list_papers(db, project_id=project_id, limit=10000)
    n = 0
    for p in papers:
        if delete_paper(db, p.id):
            n += 1
    return n


def stats(db: Session) -> dict:
    total = db.scalar(select(func.count(Paper.id))) or 0
    by_status = {
        row[0]: row[1]
        for row in db.execute(
            select(Paper.status, func.count(Paper.id)).group_by(Paper.status)
        ).all()
    }
    return {"total_papers": total, "by_status": by_status}
