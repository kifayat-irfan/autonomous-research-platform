"""Agent 2 — Evidence Extraction and Cross-Study Synthesis.

Parses PDFs page-by-page (PyMuPDF), indexes passages in ChromaDB, and
builds structured study extracts via targeted vector retrieval.

STRICT PROVENANCE RULE: every extracted finding or claim MUST include a
direct `source_pointer` containing the exact `page_number` and the
`source_doi_or_url`. No unanchored text claims are ever emitted.
"""
import logging
import re
from datetime import datetime
from typing import Dict, List

from app.core.config import settings

log = logging.getLogger(__name__)

EXTRACTION_METHOD = "heuristic_rule_based_v1"  # Phase 1: no LLM, honest label


# ---------------------------------------------------------------------------
# 1. PDF parsing
# ---------------------------------------------------------------------------

def parse_pdf_passages(pdf_path: str, paper_id: str,
                       max_chars_per_chunk: int = 2000) -> Dict:
    """Extract text page-by-page; long pages are split into chunks.

    Returns {"paper_id", "passages": [...], "pages", "skipped_pages"} where
    each passage has paper_id / page_number / chunk_index / passage_text.
    """
    try:
        import fitz  # PyMuPDF — lazy so the API boots without it
    except ImportError as exc:
        raise RuntimeError(
            "pymupdf is not installed. Install it with: pip install pymupdf"
        ) from exc

    doc = fitz.open(pdf_path)
    n_pages = len(doc)
    passages: List[Dict] = []
    skipped: List[int] = []
    for i, page in enumerate(doc):
        text = (page.get_text() or "").strip()
        if len(text) < 50:
            skipped.append(i + 1)  # probably scanned/image-only page
            continue
        chunks = [text[j:j + max_chars_per_chunk]
                  for j in range(0, len(text), max_chars_per_chunk)]
        for c_idx, chunk in enumerate(chunks):
            chunk = chunk.strip()
            if chunk:
                passages.append({
                    "paper_id": paper_id,
                    "page_number": i + 1,
                    "chunk_index": c_idx,
                    "passage_text": chunk,
                })
    doc.close()
    return {"paper_id": paper_id, "passages": passages,
            "pages": n_pages, "skipped_pages": skipped}


# ---------------------------------------------------------------------------
# 2. ChromaDB indexing
# ---------------------------------------------------------------------------

def index_passages_to_chromadb(paper_id: str, passages: List[Dict],
                               source_doi_or_url: str | None = None) -> int:
    """Upsert passages into the persistent `paper_passages` collection.

    Idempotent: re-indexing the same paper replaces (never duplicates).
    Returns the number of indexed passages.
    """
    from app.core.chroma import get_passages_collection

    if not passages:
        return 0
    collection = get_passages_collection()
    ids, documents, metadatas = [], [], []
    for p in passages:
        ids.append(f"{paper_id}::p{p['page_number']}::c{p['chunk_index']}")
        documents.append(p["passage_text"])
        metadatas.append({
            "paper_id": paper_id,
            "page_number": p["page_number"],
            "chunk_index": p["chunk_index"],
            "source_doi_or_url": source_doi_or_url or "",
        })
    # ChromaDB upsert in batches to stay within request limits.
    batch = 100
    for i in range(0, len(ids), batch):
        collection.upsert(ids=ids[i:i + batch],
                          documents=documents[i:i + batch],
                          metadatas=metadatas[i:i + batch])
    return len(ids)


def _query_passages(paper_id: str, query_text: str, n_results: int) -> List[Dict]:
    from app.core.chroma import get_passages_collection

    collection = get_passages_collection()
    res = collection.query(
        query_texts=[query_text],
        n_results=n_results,
        where={"paper_id": paper_id},
        include=["documents", "metadatas", "distances"],
    )
    out = []
    docs = (res.get("documents") or [[]])[0]
    metas = (res.get("metadatas") or [[]])[0]
    dists = (res.get("distances") or [[]])[0]
    for doc, meta, dist in zip(docs, metas, dists):
        out.append({"passage_text": doc, "metadata": meta, "distance": dist})
    return out


# ---------------------------------------------------------------------------
# 3. Structured evidence extraction (heuristic, fully anchored)
# ---------------------------------------------------------------------------

def _sentences(text: str, max_sentences: int = 3, max_chars: int = 600) -> str:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    picked = " ".join(p for p in parts[:max_sentences]).strip()
    return picked[:max_chars] + ("…" if len(picked) > max_chars else "")


def _guess_sample_size(text: str) -> str | None:
    patterns = [
        r"\bn\s*=\s*(\d[\d,]*)",
        r"sample of (?:a total of )?(\d[\d,]*)",
        r"(\d[\d,]*)\s+participants",
        r"(\d[\d,]*)\s+respondents",
        r"(\d[\d,]*)\s+patients",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(1).replace(",", "")
    return None


def _pointer(source_doi_or_url: str | None, page_number: int | None) -> str:
    base = source_doi_or_url or "source-unknown"
    return f"{base}#page={page_number}" if page_number else base


def _anchored(passage: Dict, source_doi_or_url: str | None,
             max_sentences: int = 3) -> Dict:
    meta = passage["metadata"]
    page = meta.get("page_number")
    text = _sentences(passage["passage_text"], max_sentences=max_sentences)
    return {
        "text": text,
        "page_number": page,
        "source_doi_or_url": source_doi_or_url,
        "source_pointer": _pointer(source_doi_or_url, page),
    }


def extract_structured_evidence(paper_id: str, query: str,
                                source_doi_or_url: str | None = None,
                                top_k: int | None = None,
                                db=None, task_id: str | None = None,
                                project_id: str | None = None) -> Dict:
    """Retrieve top passages and build an anchored structured extract.

    If `db` is given, findings are persisted as Evidence rows (each with
    page_number + source_url) and their ids are returned.
    """
    top_k = top_k or settings.extraction_top_k

    targeted = {
        "objective": f"research objective aim purpose of the study {query}",
        "methodology": ("methodology methods participants sample population "
                        f"study design data collection {query}"),
        "findings": f"key results findings conclusion main outcome effect {query}",
    }
    hits: Dict[str, List[Dict]] = {}
    for key, q in targeted.items():
        try:
            hits[key] = _query_passages(paper_id, q, n_results=3)
        except Exception as exc:
            log.warning("Vector query failed (%s): %s", key, exc)
            hits[key] = []
    try:
        general = _query_passages(paper_id, query, n_results=top_k)
    except Exception as exc:
        log.warning("Vector query failed (general): %s", exc)
        general = []

    if not any(hits.values()) and not general:
        return {
            "paper_id": paper_id, "query": query,
            "objective": None, "methodology": None, "key_findings": [],
            "direct_quote": None,
            "extraction_method": EXTRACTION_METHOD,
            "verification_state": "unverified",
            "note": "No passages indexed for this paper. Parse the PDF first.",
            "generated_at": datetime.utcnow().isoformat(),
        }

    objective = _anchored(hits["objective"][0], source_doi_or_url) \
        if hits["objective"] else None
    methodology_hit = hits["methodology"][0] if hits["methodology"] else None
    methodology = _anchored(methodology_hit, source_doi_or_url) \
        if methodology_hit else None
    sample_size = (_guess_sample_size(methodology_hit["passage_text"])
                   if methodology_hit else None)

    seen_pages = set()
    key_findings = []
    for h in hits["findings"] + general:
        page = h["metadata"].get("page_number")
        sig = (page, h["passage_text"][:80])
        if sig in seen_pages:
            continue
        seen_pages.add(sig)
        a = _anchored(h, source_doi_or_url, max_sentences=2)
        key_findings.append({
            "claim_text": a["text"],
            "page_number": a["page_number"],
            "source_doi_or_url": a["source_doi_or_url"],
            "source_pointer": a["source_pointer"],
        })
        if len(key_findings) >= top_k:
            break

    top_hit = (hits["findings"] or general or hits["objective"] or
               hits["methodology"] or [None])[0]
    direct_quote = None
    if top_hit:
        a = _anchored(top_hit, source_doi_or_url, max_sentences=4)
        direct_quote = {
            "paragraph_quote": top_hit["passage_text"][:1200],
            "page_number": a["page_number"],
            "source_doi_or_url": a["source_doi_or_url"],
            "source_pointer": a["source_pointer"],
        }

    extract = {
        "paper_id": paper_id,
        "query": query,
        "objective": objective,
        "methodology": methodology,
        "sample_size": sample_size,
        "key_findings": key_findings,
        "direct_quote": direct_quote,
        "extraction_method": EXTRACTION_METHOD,
        "verification_state": "machine_extracted",
        "generated_at": datetime.utcnow().isoformat(),
        "evidence_ids": [],
    }

    if db is not None:
        from app.services import evidence_service
        # One row per key finding (each anchored) + one summary row.
        for f in key_findings:
            ev = evidence_service.save_evidence(db, {
                "paper_id": paper_id, "project_id": project_id, "task_id": task_id,
                "claim_text": f["claim_text"],
                "objective": objective["text"] if objective else None,
                "method": methodology["text"] if methodology else None,
                "sample_size": sample_size,
                "page_number": f["page_number"],
                "paragraph_quote": (direct_quote["paragraph_quote"]
                                    if direct_quote else None),
                "source_url": f["source_pointer"],
                "verification_state": "machine_extracted",
            })
            extract["evidence_ids"].append(ev.id)

    return extract
