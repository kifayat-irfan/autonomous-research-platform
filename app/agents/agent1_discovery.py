"""Agent 1 — Literature Discovery and Access.

Searches Semantic Scholar and PubMed, deduplicates by DOI/title,
and attempts legal open-access PDF downloads.

Paper-dict contract (consumed by services + supervisor):
    {
        "doi": str | None, "title": str, "authors": [str],
        "journal": str | None, "year": int | None, "abstract": str | None,
        "oa_url": str | None, "pdf_path": str | None,
        "status": str,                      # one of PAPER_STATUSES
        "source_api": str,                  # "semantic_scholar" | "pubmed"
        "source_link": str,                 # COMPULSORY functional link
        "download_error": str | None,
    }

Every returned record MUST include a functional `source_link`
(DOI URL, paper web page, or an explicit search link as last resort).
"""
import logging
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List
from urllib.parse import quote_plus

import requests

from app.core.config import settings
from app.models.schemas import PAPER_STATUSES

log = logging.getLogger(__name__)

S2S_FIELDS = "title,abstract,venue,year,authors,doi,openAccessPdf,url,publicationDate"
_VALID_SOURCES = {"semantic_scholar", "pubmed"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _s2s_headers() -> dict:
    headers = {"User-Agent": f"{settings.ncbi_tool}/1.0"}
    if settings.semantic_scholar_api_key:
        headers["x-api-key"] = settings.semantic_scholar_api_key
    return headers


def _ncbi_params(extra: dict | None = None) -> dict:
    params = {"tool": settings.ncbi_tool, "retmode": "json"}
    if settings.ncbi_email:
        params["email"] = settings.ncbi_email
    if extra:
        params.update(extra)
    return params


def _doi_link(doi: str | None) -> str | None:
    doi = (doi or "").strip()
    return f"https://doi.org/{doi}" if doi else None


def _safe_filename(name: str, suffix: str = ".pdf", max_len: int = 80) -> str:
    stem = re.sub(r"[^a-zA-Z0-9_-]+", "_", name).strip("_")[:max_len] or "paper"
    return stem + suffix


def _get_with_backoff(url: str, params: dict | None = None,
                      headers: dict | None = None,
                      timeout: int = 30, tries: int = 3):
    """GET with exponential backoff on 429 / 5xx (spec 6.3: be polite)."""
    last_resp = None
    for attempt in range(tries):
        resp = requests.get(url, params=params, headers=headers, timeout=timeout)
        if resp.status_code == 429 or resp.status_code >= 500:
            last_resp = resp
            try:
                wait = float(resp.headers.get("Retry-After", 2 ** attempt))
            except (TypeError, ValueError):
                wait = 2 ** attempt
            log.info("Throttled (%s), backing off %.1fs (attempt %d/%d)",
                     resp.status_code, min(wait, 20), attempt + 1, tries)
            time.sleep(min(wait, 20))
            continue
        resp.raise_for_status()
        return resp
    assert last_resp is not None
    last_resp.raise_for_status()
    raise AssertionError("unreachable")  # pragma: no cover


# ---------------------------------------------------------------------------
# 1. Semantic Scholar search
# ---------------------------------------------------------------------------

def search_semantic_scholar(query: str, limit: int = 10) -> List[Dict]:
    """Fetch papers from the Semantic Scholar Graph API."""
    url = f"{settings.semantic_scholar_base_url}/paper/search"
    params = {"query": query, "limit": max(1, min(limit, 100)), "fields": S2S_FIELDS}
    resp = _get_with_backoff(url, params=params, headers=_s2s_headers(),
                             timeout=settings.http_timeout_seconds)
    papers: List[Dict] = []
    for item in resp.json().get("data", []):
        doi = (item.get("doi") or "").strip() or None
        oa = item.get("openAccessPdf") or {}
        oa_url = oa.get("url")
        paper_url = item.get("url")
        source_link = (
            _doi_link(doi)
            or paper_url
            or f"https://www.semanticscholar.org/search?q={quote_plus(query)}"
        )
        authors = [a.get("name") for a in (item.get("authors") or []) if a.get("name")]
        papers.append({
            "doi": doi,
            "title": (item.get("title") or "").strip(),
            "authors": authors,
            "journal": item.get("venue"),
            "year": item.get("year"),
            "abstract": item.get("abstract"),
            "oa_url": oa_url,
            "pdf_path": None,
            "status": "oa_url_found" if oa_url else "metadata_only",
            "source_api": "semantic_scholar",
            "source_link": source_link,
            "download_error": None,
        })
    return papers


# ---------------------------------------------------------------------------
# 2. PubMed search (E-utilities: esearch -> esummary -> efetch abstract)
# ---------------------------------------------------------------------------

def _pubmed_esearch(query: str, limit: int) -> List[str]:
    url = f"{settings.pubmed_base_url}/esearch.fcgi"
    params = _ncbi_params({"db": "pubmed", "term": query, "retmax": limit,
                           "sort": "relevance"})
    resp = _get_with_backoff(url, params=params,
                             timeout=settings.http_timeout_seconds)
    return resp.json().get("esearchresult", {}).get("idlist", [])


def _pubmed_esummary(pmids: List[str]) -> Dict[str, dict]:
    url = f"{settings.pubmed_base_url}/esummary.fcgi"
    params = _ncbi_params({"db": "pubmed", "id": ",".join(pmids)})
    resp = _get_with_backoff(url, params=params,
                             timeout=settings.http_timeout_seconds)
    data = resp.json().get("result", {})
    return {pid: data[pid] for pid in pmids if pid in data}


def _pubmed_abstracts(pmids: List[str]) -> Dict[str, str]:
    """Best-effort abstract fetch via efetch XML (order-mapped)."""
    try:
        url = f"{settings.pubmed_base_url}/efetch.fcgi"
        params = {"db": "pubmed", "id": ",".join(pmids), "retmode": "xml",
                  "tool": settings.ncbi_tool}
        if settings.ncbi_email:
            params["email"] = settings.ncbi_email
        resp = _get_with_backoff(url, params=params,
                                 timeout=settings.http_timeout_seconds)
        root = ET.fromstring(resp.content)
        articles = root.findall("PubmedArticle")
        out: Dict[str, str] = {}
        for pmid, article in zip(pmids, articles):
            texts = article.findall(
                ".//Abstract/AbstractText"
            )
            abstract = " ".join(
                "".join(t.itertext()).strip() for t in texts if "".join(t.itertext()).strip()
            )
            if abstract:
                out[pmid] = abstract
        return out
    except Exception as exc:  # abstracts are nice-to-have, never fatal
        log.warning("PubMed abstract fetch failed: %s", exc)
        return {}


def _extract_pubmed_doi(summary: dict) -> str | None:
    for aid in summary.get("articleids", []):
        if aid.get("idtype") == "doi" and aid.get("value"):
            return str(aid["value"]).strip()
    # elocationid sometimes carries the DOI
    eloc = str(summary.get("elocationid") or "")
    m = re.search(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", eloc, re.IGNORECASE)
    return m.group(0) if m else None


def search_pubmed(query: str, limit: int = 10) -> List[Dict]:
    """Fetch paper metadata from PubMed via E-utilities."""
    pmids = _pubmed_esearch(query, limit)
    if not pmids:
        return []
    time.sleep(0.4)  # NCBI politeness
    summaries = _pubmed_esummary(pmids)
    time.sleep(0.4)
    abstracts = _pubmed_abstracts(pmids)

    papers: List[Dict] = []
    for pmid in pmids:
        s = summaries.get(pmid)
        if not s:
            continue
        doi = _extract_pubmed_doi(s)
        authors = [a.get("name") for a in s.get("authors", []) if a.get("name")]
        pubdate = str(s.get("pubdate") or "")
        year_m = re.search(r"\b(19|20)\d{2}\b", pubdate)
        source_link = (
            _doi_link(doi)
            or f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
        )
        papers.append({
            "doi": doi,
            "title": (s.get("title") or "").strip(),
            "authors": authors,
            "journal": s.get("source"),
            "year": int(year_m.group(0)) if year_m else None,
            "abstract": abstracts.get(pmid),
            "oa_url": None,  # PubMed metadata has no OA URL; Unpaywall lookup is Phase 2
            "pdf_path": None,
            "status": "metadata_only",
            "source_api": "pubmed",
            "source_link": source_link,
            "download_error": None,
        })
    return papers


# ---------------------------------------------------------------------------
# 3. Deduplication
# ---------------------------------------------------------------------------

def _norm_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    d = doi.strip().lower()
    d = re.sub(r"^https?://(dx\.)?doi\.org/", "", d)
    return d or None


def _norm_title(title: str | None) -> str:
    t = (title or "").lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", t)).strip()


def deduplicate_papers(paper_list: List[Dict]) -> List[Dict]:
    """Deduplicate by normalized DOI, falling back to exact normalized title.

    Keeps the first occurrence; merges a missing OA URL from a duplicate.
    """
    seen_doi: dict[str, Dict] = {}
    seen_title: dict[str, Dict] = {}
    out: List[Dict] = []
    for p in paper_list:
        doi = _norm_doi(p.get("doi"))
        title = _norm_title(p.get("title"))
        existing = (seen_doi.get(doi) if doi else None) or seen_title.get(title)
        if existing is not None:
            # Merge: prefer whichever record actually has an OA URL.
            if not existing.get("oa_url") and p.get("oa_url"):
                existing["oa_url"] = p["oa_url"]
                existing["status"] = "oa_url_found"
            continue
        if doi:
            seen_doi[doi] = p
        if title:
            seen_title[title] = p
        out.append(p)
    return out


# ---------------------------------------------------------------------------
# 4. Open-access download (legal OA copies only — never paywall circumvention)
# ---------------------------------------------------------------------------

def check_open_access_and_download(paper: Dict, download_dir: str) -> Dict:
    """Try to download the paper's legal OA PDF.

    On success sets pdf_path + status="pdf_acquired".
    Otherwise marks status="needs_pdf_upload" (upload queue).
    """
    oa_url = paper.get("oa_url")
    if not oa_url:
        paper["status"] = "needs_pdf_upload"
        return paper
    try:
        dest_dir = Path(download_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        base = paper.get("doi") or paper.get("title") or "paper"
        dest = dest_dir / _safe_filename(base)

        max_bytes = settings.max_pdf_mb * 1024 * 1024
        with requests.get(oa_url, stream=True, timeout=settings.http_timeout_seconds,
                          headers={"User-Agent": f"{settings.ncbi_tool}/1.0"}) as r:
            r.raise_for_status()
            ctype = r.headers.get("content-type", "").lower()
            first = True
            downloaded = 0
            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    if not chunk:
                        continue
                    if first:
                        first = False
                        if not chunk.lstrip().startswith(b"%PDF") and "pdf" not in ctype:
                            raise ValueError(
                                f"URL did not return a PDF (content-type={ctype!r})"
                            )
                    downloaded += len(chunk)
                    if downloaded > max_bytes:
                        raise ValueError(
                            f"PDF exceeds size cap ({settings.max_pdf_mb} MB)"
                        )
                    f.write(chunk)
        paper["pdf_path"] = str(dest)
        paper["status"] = "pdf_acquired"
        paper["download_error"] = None
    except Exception as exc:
        log.warning("OA download failed for %r: %s", paper.get("title"), exc)
        paper["status"] = "needs_pdf_upload"
        paper["download_error"] = str(exc)[:500]
    return paper


# ---------------------------------------------------------------------------
# 5. Orchestrator entry point (called by the Supervisor)
# ---------------------------------------------------------------------------

def run_discovery(query: str, limit: int = 10,
                  sources: List[str] | None = None,
                  download_dir: str | None = None,
                  download_pdfs: bool = True) -> List[Dict]:
    """Run the full Agent 1 pipeline: search -> dedupe -> OA download."""
    from app.core.config import PDF_DIR  # local import: keeps module light

    sources = [s for s in (sources or ["semantic_scholar", "pubmed"])
               if s in _VALID_SOURCES]
    download_dir = download_dir or str(PDF_DIR)

    collected: List[Dict] = []
    errors: List[str] = []
    for source in sources:
        try:
            if source == "semantic_scholar":
                collected.extend(search_semantic_scholar(query, limit))
            elif source == "pubmed":
                collected.extend(search_pubmed(query, limit))
        except Exception as exc:
            log.warning("Discovery source %s failed: %s", source, exc)
            errors.append(f"{source}: {exc}")
        time.sleep(0.5)  # be polite between sources

    papers = deduplicate_papers(collected)

    if download_pdfs:
        for p in papers:
            check_open_access_and_download(p, download_dir)

    # Guarantee the compulsory source_link on every record.
    for p in papers:
        if not p.get("source_link"):
            p["source_link"] = (
                f"https://scholar.google.com/scholar?q={quote_plus(p.get('title', query))}"
            )

    for p in papers:
        assert p.get("status") in PAPER_STATUSES, f"bad status {p.get('status')}"
    if errors:
        log.info("Discovery completed with source errors: %s", "; ".join(errors))
    return papers
