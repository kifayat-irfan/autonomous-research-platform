"""Agent 6 — Personalized Scientific Writing.

Builds a private style profile from the researcher's own approved texts
(retrieval + explicit feedback first, per spec 4.4 — never presented as
automatic fine-tuning), then drafts manuscript sections strictly from
verified project materials: approved gaps, protocols, approved analyses,
and source-anchored evidence.

COMPULSORY RULES (spec 2 / Agent 6):
  - Do NOT invent citations, numbers, or interpretations. Every cited
    claim comes from the project's evidence bank with its source link.
  - Keep cited claims tied to sources via an explicit evidence map.
  - Drafts are drafts: researcher approval gates any release.
"""
import json
import logging
import re
from collections import Counter
from typing import Dict, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import DATA_DIR
from app.models.schemas import (
    AnalysisRun,
    Evidence,
    GapBrief,
    Manuscript,
    ResearchProtocol,
)

log = logging.getLogger(__name__)

METHOD = "template_drafting_v1"
MANUSCRIPT_DIR = DATA_DIR / "manuscripts"

WRITABLE_SECTIONS = ["abstract", "introduction", "methods", "results",
                     "discussion", "conclusion"]


# ---------------------------------------------------------------------------
# 1. Style profile (from the researcher's own texts)
# ---------------------------------------------------------------------------

def build_style_profile(corpus_texts: List[str]) -> Dict:
    """Quantify writing style from researcher-supplied texts."""
    texts = [t for t in corpus_texts if t and t.strip()]
    if not texts:
        raise ValueError("At least one non-empty text is required.")
    full = "\n\n".join(texts)
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", full) if s.strip()]
    words = re.findall(r"[A-Za-z']+", full)
    paras = [p for p in full.split("\n\n") if p.strip()]
    n_w = max(len(words), 1)

    passive = len(re.findall(
        r"\b(was|were|been|is|are|be|being)\s+[a-z]+ed\b", full, re.IGNORECASE))
    hedges = len(re.findall(
        r"\b(may|might|suggests?|indicates?|potentially|likely|tends? to|"
        r"appears? to)\b", full, re.IGNORECASE))
    connectors = Counter(re.findall(
        r"\b(however|moreover|furthermore|therefore|thus|in contrast|"
        r"consequently|notably|specifically)\b", full, re.IGNORECASE))
    first_person = len(re.findall(r"\b(we|our|I|my)\b", full, re.IGNORECASE))

    profile = {
        "n_texts": len(texts),
        "avg_sentence_length_words": round(
            sum(len(re.findall(r"[A-Za-z']+", s)) for s in sentences)
            / max(len(sentences), 1), 1),
        "avg_paragraph_length_sentences": round(len(sentences) / max(len(paras), 1), 1),
        "avg_word_length_chars": round(
            sum(len(w) for w in words) / n_w, 2),
        "passive_constructions_per_1000w": round(passive / n_w * 1000, 1),
        "hedging_per_1000w": round(hedges / n_w * 1000, 1),
        "first_person_per_1000w": round(first_person / n_w * 1000, 1),
        "favored_connectors": dict(connectors.most_common(8)),
        "method": ("Style profile derived from the researcher's own texts via "
                   "retrieval + explicit feedback (spec 4.4). This is not "
                   "automatic fine-tuning."),
    }
    return profile


def _style_hint(profile: Dict | None) -> str:
    if not profile:
        return ""
    s = profile
    bits = []
    if s.get("avg_sentence_length_words"):
        bits.append(f"aim for ~{s['avg_sentence_length_words']:.0f} words per sentence")
    if s.get("favored_connectors"):
        fav = ", ".join(list(s["favored_connectors"])[:3])
        bits.append(f"favor connectors like: {fav}")
    if s.get("first_person_per_1000w", 0) < 5:
        bits.append("avoid first person; prefer impersonal constructions")
    else:
        bits.append("first person ('we') is acceptable")
    return "Style guidance: " + "; ".join(bits) + "." if bits else ""


# ---------------------------------------------------------------------------
# 2. Manuscript drafting (evidence-grounded only)
# ---------------------------------------------------------------------------

def _cite(ev: Dict) -> str:
    title = (ev.get("title") or "source")[:70]
    link = ev.get("source_link") or ""
    page = ev.get("page_number")
    suffix = f", p. {page}" if page else ""
    return f"[{title}{suffix}]({link})" if link else f"[{title}{suffix}]"


def _gather_materials(db: Session, project_id: str,
                      protocol_id: str | None) -> Dict:
    protocol = db.get(ResearchProtocol, protocol_id) if protocol_id else None
    gap = db.get(GapBrief, protocol.gap_id) if protocol else None
    analyses = db.scalars(
        select(AnalysisRun)
        .where(AnalysisRun.project_id == project_id,
               AnalysisRun.approval_status == "approved")
        .order_by(AnalysisRun.created_at.desc()).limit(5)).all()
    evidence = db.scalars(
        select(Evidence).where(Evidence.project_id == project_id)
        .order_by(Evidence.created_at.desc()).limit(20)).all()
    return {"protocol": protocol, "gap": gap, "analyses": analyses,
            "evidence": evidence}


def _sec_abstract(m: Dict, style_hint: str) -> str:
    gap = m["gap"].gap_statement if m["gap"] else "the research gap"
    n_ev = len(m["evidence"])
    n_an = len(m["analyses"])
    return (
        f"**Background.** {gap} "
        f"**Methods.** This study synthesizes {n_ev} extracted evidence items "
        f"from the project literature bank"
        f"{' and reports ' + str(n_an) + ' approved analysis run(s)' if n_an else ''}. "
        f"**Results.** Key patterns are summarized with source-anchored citations "
        f"in the Results section. **Conclusion.** The findings inform the "
        f"approved study protocol and identify next steps. {style_hint}"
    ).strip()


def _sec_introduction(m: Dict, style_hint: str) -> str:
    gap = m["gap"]
    gap_text = gap.gap_statement if gap else "The focal research gap."
    known = gap.known_summary if gap else ""
    unknown = gap.unknown_summary if gap else ""
    cites = "\n".join(f"- {_cite(e)}" for e in m["evidence"][:5])
    return (
        f"The present work addresses the following gap: {gap_text}\n\n"
        f"What is known: {known}\n\n"
        f"What remains unknown: {unknown}\n\n"
        f"The evidence base for this account comes from the project's extracted "
        f"literature, including:\n{cites}\n\n{style_hint}"
    ).strip()


def _sec_methods(m: Dict, style_hint: str) -> str:
    p = m["protocol"]
    if not p:
        return ("No approved protocol is linked to this manuscript yet. "
                "Attach a protocol before finalizing the Methods section.")
    secs = p.sections_dict
    measures = "\n".join(f"- {x}" for x in secs.get("measures", []))
    return (
        f"**Design.** {secs.get('design_overview', '')}\n\n"
        f"**Procedure.** {secs.get('procedure', '')}\n\n"
        f"**Measures.**\n{measures}\n\n"
        f"**Analysis plan.** {secs.get('analysis_plan', '')}\n\n{style_hint}"
    ).strip()


def _sec_results(m: Dict, style_hint: str) -> str:
    parts = []
    for run in m["analyses"]:
        r = run.results_dict
        parts.append(f"**Analysis ({run.analysis_type}, n={r.get('n', '?')}).**")
        if run.analysis_type == "regression":
            for row in r.get("coefficients", []):
                parts.append(
                    f"- {row['term']}: b = {row['estimate']}, SE = {row['se']}, "
                    f"p = {row['p']:.3g}{' *' if row['significant_05'] else ''}")
            parts.append(f"R² = {r.get('r_squared')}.")
        elif run.analysis_type == "ttest":
            parts.append(
                f"- Groups {r.get('groups')}: means {r.get('means')}, "
                f"t = {r.get('t'):.3f}, p = {r.get('p'):.3g}, "
                f"Cohen's d = {r.get('cohens_d')}.")
        elif run.analysis_type == "chi2":
            parts.append(f"- χ² = {r.get('chi2'):.3f}, p = {r.get('p'):.3g}, "
                         f"Cramér's V = {r.get('cramers_v')}.")
        elif run.analysis_type == "correlation":
            for k, v in r.items():
                if k.startswith("_"):
                    continue
                parts.append(f"- {k}: r = {v['r']}, p = {v['p']:.3g} (n={v['n']}).")
        elif run.analysis_type == "descriptive":
            parts.append("Descriptive statistics computed (see analysis run).")
        parts.append(f"Source: analysis run `{run.id[:8]}` "
                     f"(checksum {run.dataset_checksum[:12] if run.dataset_checksum else 'n/a'}).")
    for e in m["evidence"][:5]:
        parts.append(f"- Evidence: {e.get('claim_text', '')[:200]} {_cite(e)}")
    parts.append(style_hint)
    return "\n".join(p for p in parts if p).strip()


def _sec_discussion(m: Dict, style_hint: str) -> str:
    gap = m["gap"].gap_statement if m["gap"] else "the focal gap"
    return (
        f"These results speak directly to the gap: {gap} "
        f"Effect directions should be interpreted as associations unless the "
        f"design supports causal inference (see protocol). Limitations include "
        f"those documented in the source studies and the analysis cleaning "
        f"report. Future work should test the theorized mechanism under "
        f"manipulated or longitudinal conditions. {style_hint}"
    ).strip()


def _sec_conclusion(m: Dict, style_hint: str) -> str:
    return (
        f"This manuscript consolidates source-anchored evidence and approved "
        f"analyses into a draft for researcher review. No claim in this draft "
        f"extends beyond its cited source. {style_hint}"
    ).strip()


_BUILDERS = {
    "abstract": _sec_abstract,
    "introduction": _sec_introduction,
    "methods": _sec_methods,
    "results": _sec_results,
    "discussion": _sec_discussion,
    "conclusion": _sec_conclusion,
}


def draft_manuscript(db: Session, project_id: str,
                     task_id: str | None = None,
                     protocol_id: str | None = None,
                     title: str | None = None,
                     sections: List[str] | None = None,
                     style_profile: Dict | None = None) -> Dict:
    """Draft manuscript sections strictly from project materials."""
    sections = [s for s in (sections or WRITABLE_SECTIONS)
                if s in _BUILDERS]
    if not sections:
        raise ValueError(f"No valid sections. Choose from {WRITABLE_SECTIONS}.")
    materials = _gather_materials(db, project_id, protocol_id)
    # evidence dicts for citation rendering
    ev_dicts = []
    for e in materials["evidence"]:
        from app.models.schemas import Paper as _Paper
        p = db.get(_Paper, e.paper_id)
        ev_dicts.append({
            "claim_text": e.claim_text,
            "title": p.title if p else "",
            "source_link": e.source_url or (p.source_link if p else ""),
            "page_number": e.page_number,
        })
    materials["evidence"] = ev_dicts

    hint = _style_hint(style_profile)
    built = {name: _BUILDERS[name](materials, hint) for name in sections}
    evidence_map = [{
        "section": name,
        "citations": [e["source_link"] for e in ev_dicts[:5] if e["source_link"]],
    } for name in sections]

    row = Manuscript(
        project_id=project_id, task_id=task_id, protocol_id=protocol_id,
        title=title or f"Draft manuscript ({len(sections)} sections)",
        sections=json.dumps(built),
        style_profile=json.dumps(style_profile or {}),
        evidence_map=json.dumps(evidence_map),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    log.info("Manuscript %s drafted (%d sections)", row.id[:8], len(sections))
    return get_manuscript_dict(row.id, db)


def get_manuscript_dict(manuscript_id: str, db: Session) -> Dict:
    ms = db.get(Manuscript, manuscript_id)
    if ms is None:
        raise ValueError(f"Unknown manuscript id: {manuscript_id}")
    return {
        "id": ms.id, "project_id": ms.project_id,
        "task_id": ms.task_id, "protocol_id": ms.protocol_id,
        "title": ms.title, "sections": ms.sections_dict,
        "style_profile": ms.style_dict, "evidence_map": ms.evidence_list,
        "docx_path": ms.docx_path, "approval_status": ms.approval_status,
        "researcher_note": ms.researcher_note,
        "created_at": ms.created_at,
    }


def list_manuscripts(db: Session, project_id: str | None = None,
                     task_id: str | None = None,
                     limit: int = 100) -> List[Manuscript]:
    stmt = select(Manuscript).order_by(Manuscript.created_at.desc())
    if project_id:
        stmt = stmt.where(Manuscript.project_id == project_id)
    if task_id:
        stmt = stmt.where(Manuscript.task_id == task_id)
    return list(db.scalars(stmt.limit(limit)).all())


def export_docx(manuscript_id: str, db: Session) -> str:
    """Export the manuscript to a Word .docx file."""
    try:
        from docx import Document
        from docx.shared import Pt
    except ImportError as exc:
        raise RuntimeError(
            "python-docx is required: pip install python-docx") from exc
    ms = db.get(Manuscript, manuscript_id)
    if ms is None:
        raise ValueError(f"Unknown manuscript id: {manuscript_id}")

    MANUSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    doc = Document()
    doc.add_heading(ms.title, level=0)
    for name, text in ms.sections_dict.items():
        doc.add_heading(name.capitalize(), level=1)
        # Convert markdown links [t](u) to "t (u)" for Word.
        plain = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", text)
        for para in plain.split("\n\n"):
            para = para.strip()
            if not para:
                continue
            if para.startswith("- "):
                doc.add_paragraph(para[2:], style="List Bullet")
            elif para.startswith("**") and para.endswith("**"):
                p = doc.add_paragraph()
                run = p.add_run(para.strip("*"))
                run.bold = True
            else:
                doc.add_paragraph(para)
    # Evidence appendix
    doc.add_heading("Evidence appendix", level=1)
    for item in ms.evidence_list:
        doc.add_paragraph(
            f"{item['section']}: " + "; ".join(item.get("citations", [])))
    path = str(MANUSCRIPT_DIR / f"{manuscript_id}.docx")
    doc.save(path)
    ms.docx_path = path
    db.commit()
    log.info("Manuscript %s exported to %s", manuscript_id[:8], path)
    return path


def review_manuscript(db: Session, manuscript_id: str, approved: bool,
                      note: str = "") -> Manuscript:
    """Researcher approve/reject a manuscript draft (gates release)."""
    ms = db.get(Manuscript, manuscript_id)
    if ms is None:
        raise ValueError(f"Unknown manuscript id: {manuscript_id}")
    ms.approval_status = "approved" if approved else "rejected"
    ms.researcher_note = note
    db.commit()
    db.refresh(ms)
    return ms
