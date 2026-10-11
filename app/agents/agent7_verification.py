"""Agent 7 — Scientific Verification & Audit.

Independently checks cited claims against indexed passages, cross-checks
reported numbers against extraction records, and validates DOIs.

COMPULSORY RULES (spec 2 / Agent 7):
  1. Unresolved HIGH-severity issues block progression — the supervisor
     must set the task to 'blocked' / 'needs_approval', never silently
     auto-fix or proceed.
  2. Every audit flag points to the specific passage text, page number,
     and canonical source link.

Checks are heuristic and honestly labeled; Agent 7 is an independent
verification stage, not a proof of correctness.
"""
import logging
import re
from typing import Dict, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.schemas import AuditFlag, AuditReport, Evidence, Paper

log = logging.getLogger(__name__)

METHOD = "heuristic_audit_v1"

_DOI_RE = re.compile(r"^10\.\d{4,9}/[-._;()/:A-Z0-9]+$", re.IGNORECASE)

# Causal-overreach phrasing to flag for researcher review (medium severity).
_CAUSAL_PATTERNS = [
    (r"\bproves?\b", "prove/proves"),
    (r"\bproven\b", "proven"),
    (r"\bcausal effect\b", "causal effect"),
    (r"\bestablishes? causality\b", "establishes causality"),
    (r"\bdefinitively\b", "definitively"),
    (r"\bwithout (a )?doubt\b", "without doubt"),
    (r"\bgroundbreaking\b", "groundbreaking"),
]


# ---------------------------------------------------------------------------
# Retrieval helpers (monkeypatchable for tests)
# ---------------------------------------------------------------------------

def _paper_has_passages(paper_id: str) -> bool:
    try:
        from app.core.chroma import get_passages_collection
        res = get_passages_collection().get(
            where={"paper_id": paper_id}, limit=1, include=["metadatas"])
        return bool((res.get("ids") or []))
    except Exception as exc:
        log.warning("ChromaDB unavailable for passage check: %s", exc)
        return False


def _retrieve_passages(paper_id: str, query_text: str, n: int = 3) -> List[Dict]:
    from app.core.chroma import get_passages_collection
    res = get_passages_collection().query(
        query_texts=[query_text], n_results=n,
        where={"paper_id": paper_id},
        include=["documents", "metadatas", "distances"],
    )
    docs = (res.get("documents") or [[]])[0]
    metas = (res.get("metadatas") or [[]])[0]
    return [{"passage_text": d, "metadata": m}
            for d, m in zip(docs, metas)]


def _word_overlap(a: str, b: str) -> float:
    wa = set(re.findall(r"[a-z]{4,}", (a or "").lower()))
    wb = set(re.findall(r"[a-z]{4,}", (b or "").lower()))
    if not wa:
        return 0.0
    return len(wa & wb) / len(wa)


def _numbers(text: str | float | int | None) -> List[str]:
    if text is None:
        return []
    return re.findall(r"\d+(?:\.\d+)?", str(text).replace(",", ""))


# ---------------------------------------------------------------------------
# 1. Claim–citation audit
# ---------------------------------------------------------------------------

def audit_claim_citations(manuscript_or_text: str,
                          cited_paper_ids: List[str],
                          db: Session) -> Dict:
    """Check each cited paper: exists, DOI valid, passages indexed, text support."""
    keywords = " ".join(sorted(
        set(re.findall(r"[a-z]{4,}", manuscript_or_text.lower())) - set(),
        key=lambda w: -manuscript_or_text.lower().count(w))[:40])
    results, flags = [], []
    for pid in cited_paper_ids:
        paper = db.get(Paper, pid)
        entry: Dict = {"paper_id": pid, "status": "supported", "flags": []}
        pflags: List[Dict] = []
        if paper is None:
            entry["status"] = "unsupported"
            pflags.append(_mkflag(
                "high", "citation", f"Cited paper id '{pid}' is not in the paper bank.",
                expected="paper record with DOI + source link",
                found="no such paper record", source_link=None))
            entry["flags"] = pflags
            flags.extend(pflags)
            results.append(entry)
            continue
        entry["title"] = paper.title
        entry["source_link"] = paper.source_link

        if not paper.doi or not _DOI_RE.match(paper.doi.strip()):
            pflags.append(_mkflag(
                "medium", "doi",
                f"Paper '{paper.title[:60]}' has no valid DOI (doi={paper.doi!r}).",
                expected="valid DOI (10.xxxx/...)",
                found=str(paper.doi), source_link=paper.source_link))
        if not _paper_has_passages(pid):
            pflags.append(_mkflag(
                "medium", "citation",
                f"No indexed full-text passages for '{paper.title[:60]}' — "
                "any citation to it is abstract/metadata-only, not full-text evidence.",
                expected="parsed + indexed PDF passages",
                found=f"paper status={paper.status}", source_link=paper.source_link))
            entry["status"] = "partially_supported"
        else:
            try:
                passages = _retrieve_passages(pid, keywords or paper.title, n=3)
            except Exception as exc:
                passages = []
                log.warning("Retrieval failed for %s: %s", pid, exc)
            best, best_score = None, 0.0
            for ps in passages:
                s = _word_overlap(keywords, ps["passage_text"])
                if s > best_score:
                    best, best_score = ps, s
            if best:
                entry["best_passage"] = {
                    "passage_text": best["passage_text"][:600],
                    "page_number": best["metadata"].get("page_number"),
                    "source_link": paper.source_link,
                }
            if best_score < 0.10:
                pflags.append(_mkflag(
                    "medium", "citation",
                    f"Claim text shows low overlap with indexed passages of "
                    f"'{paper.title[:60]}' (overlap {best_score:.0%}) — verify the "
                    "citation manually.",
                    expected="passage-level textual support",
                    found=f"best overlap {best_score:.0%}",
                    passage_text=(best["passage_text"][:400] if best else None),
                    page_number=(best["metadata"].get("page_number") if best else None),
                    source_link=paper.source_link))
                if entry["status"] == "supported":
                    entry["status"] = "partially_supported"
        entry["flags"] = pflags
        flags.extend(pflags)
        results.append(entry)

    return {
        "papers_checked": len(cited_paper_ids),
        "results": results,
        "flags": flags,
        "method": METHOD,
    }


def _mkflag(severity: str, category: str, claim_text: str, expected=None,
            found=None, passage_text=None, page_number=None,
            source_link=None, paper_id=None) -> Dict:
    d = {"severity": severity, "category": category, "claim_text": claim_text,
         "expected": expected, "found": found, "passage_text": passage_text,
         "page_number": page_number, "source_link": source_link}
    if paper_id:
        d["_pid"] = paper_id
    return d


# ---------------------------------------------------------------------------
# 2. Numerical consistency audit
# ---------------------------------------------------------------------------

def audit_numerical_consistency(reported_table: List[Dict],
                                source_evidence: List[Dict]) -> Dict:
    """Cross-check reported numbers against original extraction records."""
    flags, checked, matched = [], 0, 0
    by_paper: Dict[str, List[Dict]] = {}
    for ev in source_evidence:
        by_paper.setdefault(ev.get("paper_id"), []).append(ev)

    for row in reported_table:
        checked += 1
        pid = row.get("paper_id")
        evs = by_paper.get(pid, [])
        if not evs:
            flags.append(_mkflag(
                "high", "numerical",
                f"Reported value '{row.get('label')}' cites paper {pid}, "
                "which has no extraction records.",
                expected="extraction record for cited paper",
                found="none", source_link=None))
            continue
        rep_nums = _numbers(row.get("value"))
        metric = str(row.get("metric", "")).lower()
        ok = True

        # Sample-size check (high severity on mismatch).
        if "sample" in metric or re.search(r"\bn\b", metric):
            ev_nums = []
            for ev in evs:
                ev_nums.extend(_numbers(ev.get("sample_size")))
            if rep_nums and ev_nums and rep_nums[0] != ev_nums[0]:
                ok = False
                flags.append(_mkflag(
                    "high", "numerical",
                    f"Sample-size mismatch for '{row.get('label')}' "
                    f"({row.get('location', '')}).",
                    expected=f"n={ev_nums[0]} (extraction record)",
                    found=f"n={rep_nums[0]} (reported)",
                    passage_text=(evs[0].get("paragraph_quote") or "")[:400],
                    page_number=evs[0].get("page_number"),
                    source_link=evs[0].get("source_url")))
        # Value-presence check (medium severity).
        if rep_nums:
            hay = " ".join((ev.get("paragraph_quote") or "") for ev in evs)
            hay_nums = set(_numbers(hay))
            if rep_nums[0] not in hay_nums:
                ok = False
                flags.append(_mkflag(
                    "medium", "numerical",
                    f"Reported value {rep_nums[0]} for '{row.get('label')}' not "
                    f"found in the source passage text.",
                    expected="value present in source passage",
                    found="absent from extracted quotes",
                    passage_text=(evs[0].get("paragraph_quote") or "")[:400],
                    page_number=evs[0].get("page_number"),
                    source_link=evs[0].get("source_url")))
        if ok:
            matched += 1

    return {"checked": checked, "matched": matched, "flags": flags,
            "method": METHOD}


# ---------------------------------------------------------------------------
# 3. Full audit report
# ---------------------------------------------------------------------------

def generate_audit_report(project_id: str, db: Session,
                          task_id: str | None = None,
                          protocol_id: str | None = None,
                          manuscript_text: str | None = None,
                          cited_paper_ids: List[str] | None = None) -> Dict:
    """Comprehensive pass/fail audit. HIGH severity -> overall 'blocked'."""
    flags: List[Dict] = []

    # (a) DOI / metadata audit over the paper bank.
    papers = db.scalars(
        select(Paper).where(Paper.project_id == project_id)).all()
    for p in papers:
        if not p.doi or not _DOI_RE.match(p.doi.strip()):
            flags.append(_mkflag(
                "medium", "doi",
                f"Paper '{p.title[:60]}' lacks a valid DOI (doi={p.doi!r}).",
                expected="valid DOI", found=str(p.doi),
                source_link=p.source_link))

    # (b) Evidence verification-state census.
    ev_rows = db.scalars(
        select(Evidence).where(Evidence.project_id == project_id)).all()
    n_unverified = sum(1 for e in ev_rows
                       if e.verification_state in ("machine_extracted", "unverified"))

    # (c) Claim–citation audit (optional inputs).
    claim_summary = None
    if manuscript_text and cited_paper_ids:
        claim_summary = audit_claim_citations(manuscript_text, cited_paper_ids, db)
        flags.extend(claim_summary["flags"])

    # (d) Causal-wording scan.
    causal_hits = []
    if manuscript_text:
        for i, line in enumerate(manuscript_text.splitlines(), start=1):
            for pat, label in _CAUSAL_PATTERNS:
                if re.search(pat, line, re.IGNORECASE):
                    causal_hits.append({"line": i, "wording": label,
                                        "text": line.strip()[:160]})
                    flags.append(_mkflag(
                        "medium", "causal_wording",
                        f"Causal-overreach wording '{label}' (line {i}) — "
                        "requires evidence of a causal design or softening.",
                        expected="association language unless causal design",
                        found=line.strip()[:160], source_link=None))
                    break

    n_high = sum(1 for f in flags if f["severity"] == "high")
    n_med = sum(1 for f in flags if f["severity"] == "medium")
    overall = "blocked" if n_high else ("fail" if n_med else "pass")

    summary = {
        "papers_audited": len(papers),
        "evidence_items": len(ev_rows),
        "evidence_unverified": n_unverified,
        "claims_checked": (claim_summary["papers_checked"]
                           if claim_summary else 0),
        "causal_wording_hits": len(causal_hits),
        "flags_high": n_high,
        "flags_medium": n_med,
        "flags_low": sum(1 for f in flags if f["severity"] == "low"),
        "method": METHOD,
        "note": ("HIGH-severity flags block progression until resolved by the "
                 "researcher. This audit is heuristic; final sign-off is human."),
    }

    report = AuditReport(
        project_id=project_id, task_id=task_id, protocol_id=protocol_id,
        overall_status=overall, summary=_dump(summary),
    )
    db.add(report)
    db.flush()
    for f in flags:
        db.add(AuditFlag(
            report_id=report.id, severity=f["severity"], category=f["category"],
            claim_text=f.get("claim_text"), expected=f.get("expected"),
            found=f.get("found"), passage_text=f.get("passage_text"),
            page_number=f.get("page_number"), source_link=f.get("source_link"),
        ))
    db.commit()
    db.refresh(report)
    log.info("Audit report %s: %s (%d high, %d medium)", report.id[:8],
             overall, n_high, n_med)
    return get_report_dict(report.id, db)


def _dump(o) -> str:
    import json as _j
    return _j.dumps(o)


def get_report_dict(report_id: str, db: Session) -> Dict:
    r = db.get(AuditReport, report_id)
    if r is None:
        raise ValueError(f"Unknown report_id: {report_id}")
    import json as _j
    return {
        "report_id": r.id,
        "project_id": r.project_id,
        "task_id": r.task_id,
        "protocol_id": r.protocol_id,
        "overall_status": r.overall_status,
        "summary": _j.loads(r.summary or "{}"),
        "flags": [{
            "flag_id": f.id, "severity": f.severity, "category": f.category,
            "claim_text": f.claim_text, "expected": f.expected,
            "found": f.found, "passage_text": f.passage_text,
            "page_number": f.page_number, "source_link": f.source_link,
            "status": f.status,
        } for f in r.flags],
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


def list_reports(db: Session, project_id: str | None = None,
                 limit: int = 50) -> List[AuditReport]:
    stmt = select(AuditReport).order_by(AuditReport.created_at.desc())
    if project_id:
        stmt = stmt.where(AuditReport.project_id == project_id)
    return list(db.scalars(stmt.limit(limit)).all())


def resolve_flag(db: Session, flag_id: str) -> AuditFlag:
    f = db.get(AuditFlag, flag_id)
    if f is None:
        raise ValueError(f"Unknown flag_id: {flag_id}")
    f.status = "resolved"
    db.commit()
    return f
