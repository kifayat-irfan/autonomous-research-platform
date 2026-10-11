"""Agent 3 — Research-Gap Discovery & Challenge.

Reads Agent 2's evidence matrix, proposes candidate gaps (theoretical,
empirical, population, methodological), then tries to DISPROVE each one
with a targeted prior-art counter-search via Agent 1.

COMPULSORY RULES (spec 2 / Agent 3):
  1. NO geography-only or method-swapping novelty claims without an
     explicit causal mechanism — such candidates are rejected, never emitted.
  2. Every gap brief attaches source links for supporting papers AND for
     every novelty-challenge result.
  3. If the counter-search finds conflicting prior art, the gap is flagged
     'challenged_disproved' — never presented as novel.

Extraction here is heuristic and honestly labeled; the researcher (not a
confidence score) makes the final call.
"""
import json
import logging
import re
from collections import Counter
from typing import Dict, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.agent1_discovery import (
    deduplicate_papers,
    search_pubmed,
    search_semantic_scholar,
)
from app.models.schemas import (
    DESIGN_TYPES,
    Evidence,
    GapBrief,
    Paper,
)

log = logging.getLogger(__name__)

METHOD = "heuristic_gap_analysis_v1"

_STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "were", "have",
    "has", "had", "been", "are", "was", "which", "their", "they", "them",
    "than", "then", "also", "such", "into", "between", "among", "more",
    "most", "some", "other", "used", "using", "based", "findings", "study",
    "studies", "results", "result", "research", "paper", "authors",
}

_METHOD_FAMILIES = {
    "survey": {"survey", "questionnaire", "self-report", "self report"},
    "experimental": {"experiment", "randomized", "rct", "controlled trial",
                     "manipulat", "intervention", "treatment group"},
    "qualitative": {"interview", "focus group", "ethnograph", "case study",
                    "thematic analysis", "grounded theory"},
    "observational": {"observational", "cohort", "longitudinal",
                      "cross-sectional", "secondary data", "registry",
                      "electronic health", "claims data"},
    "simulation": {"simulation", "agent-based", "monte carlo"},
}

_THEORY_WORDS = {"theory", "theories", "theoretical", "framework", "model",
                 "hypothesis-driven", "paradigm"}
_MECHANISM_WORDS = re.compile(
    r"\b(mediat\w*|moderat\w*|mechanism\w*|pathway\w*|explains?\b|drives?\b|"
    r"because\b|through which|via which)\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

def _keywords(text: str, top_n: int = 10) -> List[str]:
    tokens = re.findall(r"[a-z]{4,}", (text or "").lower())
    counts = Counter(t for t in tokens if t not in _STOPWORDS)
    return [w for w, _ in counts.most_common(top_n)]


def _classify_method(method_text: str) -> str | None:
    text = (method_text or "").lower()
    for family, kws in _METHOD_FAMILIES.items():
        if any(k in text for k in kws):
            return family
    return None


def _sample_n(sample_size: str | None) -> int | None:
    if not sample_size:
        return None
    m = re.search(r"\d[\d,]*", str(sample_size).replace(",", ""))
    try:
        return int(m.group(0).replace(",", "")) if m else None
    except (ValueError, AttributeError):
        return None


def _has_mechanism(text: str) -> bool:
    return bool(_MECHANISM_WORDS.search(text or ""))


def _is_banned_novelty(statement: str, gap_type: str, context: str) -> str | None:
    """Enforce compulsory rule 1. Returns a rejection reason or None."""
    if gap_type == "population" and not _has_mechanism(context):
        return ("geography-only claim without an explicit causal mechanism "
                "(spec: rejected)")
    if gap_type == "methodological" and not _has_mechanism(context):
        # Pure "no one used method X" swaps are banned; mechanism-linked ones pass.
        if re.search(r"\bno (studies|research|work)\b", statement, re.IGNORECASE):
            return ("method-swapping claim without an explicit causal mechanism "
                    "(spec: rejected)")
    return None


# ---------------------------------------------------------------------------
# 1. Evidence-matrix gap analysis
# ---------------------------------------------------------------------------

def analyze_evidence_matrix_for_gaps(project_id: str, db: Session,
                                    task_id: str | None = None,
                                    max_gaps: int = 5) -> List[Dict]:
    """Identify candidate gaps from the project's evidence matrix."""
    evidence = db.scalars(
        select(Evidence).where(Evidence.project_id == project_id)
    ).all()
    if not evidence:
        log.info("No evidence for project %s — no gaps to propose.", project_id)
        return []

    papers = {p.id: p for p in db.scalars(
        select(Paper).where(Paper.project_id == project_id)).all()}

    def _cite(ev: Evidence) -> Dict:
        p = papers.get(ev.paper_id)
        return {
            "paper_id": ev.paper_id,
            "title": p.title if p else "(unknown paper)",
            "source_link": p.source_link if p else None,
            "page_number": ev.page_number,
        }

    candidates: List[Dict] = []

    # ---- (a) Empirical coverage: themes addressed by a single study ----
    theme_docs: Dict[str, List[Evidence]] = {}
    for ev in evidence:
        for kw in _keywords(f"{ev.claim_text} {ev.objective}", top_n=6):
            theme_docs.setdefault(kw, []).append(ev)
    for theme, evs in sorted(theme_docs.items(),
                             key=lambda kv: len(kv[1]))[:max_gaps * 2]:
        if len(evs) == 1:
            ev = evs[0]
            stmt = (f"Thin empirical coverage of '{theme}': only one extracted "
                    f"study addresses it, so the finding is unreplicated.")
            candidates.append({
                "gap_statement": stmt,
                "gap_type": "empirical",
                "known_summary": ev.claim_text[:300],
                "unknown_summary": (f"Whether the '{theme}' finding replicates "
                                    f"across samples, settings, and measures."),
                "supporting_evidence": [_cite(ev)],
                "importance_score": 6,
                "feasibility_score": 8,
                "_context": ev.claim_text or "",
            })

    # ---- (b) Methodological: single method family across the bank ----
    families = Counter(
        _classify_method(ev.method) for ev in evidence if ev.method)
    families = {k: v for k, v in families.items() if k}
    if families and len(families) == 1 and len(evidence) >= 2:
        only = next(iter(families))
        missing = [f for f in ("experimental", "longitudinal", "qualitative")
                   if f != only and f not in families]
        for fam in missing[:1]:
            stmt = (f"All {len(evidence)} extracted studies use {only} designs; "
                    f"no {fam} evidence tests whether the observed associations "
                    f"reflect a causal mechanism rather than correlation.")
            ctx = " ".join(ev.claim_text or "" for ev in evidence)
            candidates.append({
                "gap_statement": stmt,
                "gap_type": "methodological",
                "known_summary": f"{len(evidence)} studies, all {only}-based.",
                "unknown_summary": ("The causal mechanism: does the effect hold "
                                    "under manipulated or longitudinal conditions?"),
                "supporting_evidence": [_cite(ev) for ev in evidence[:3]],
                "importance_score": 8,
                "feasibility_score": 6,
                "_context": ctx,
            })

    # ---- (c) Sample adequacy ----
    ns = [_sample_n(ev.sample_size) for ev in evidence]
    ns = [n for n in ns if n]
    if ns and max(ns) < 200 and len(evidence) >= 2:
        candidates.append({
            "gap_statement": (f"Small-sample evidence base: the largest extracted "
                              f"study has n={max(ns)}; effect estimates are "
                              f"imprecise and subgroup mechanisms untestable."),
            "gap_type": "methodological",
            "known_summary": f"Sample sizes observed: {sorted(set(ns))}.",
            "unknown_summary": "Adequately powered estimates and mechanism tests.",
            "supporting_evidence": [_cite(ev) for ev in evidence[:3]],
            "importance_score": 7,
            "feasibility_score": 7,
            "_context": "power precision mechanism " + " ".join(
                ev.claim_text or "" for ev in evidence),
        })

    # ---- (d) Theoretical: atheoretical objectives ----
    atheoretical = [ev for ev in evidence
                    if ev.objective and not _THEORY_WORDS.intersection(
                        set(re.findall(r"[a-z]+", ev.objective.lower())))]
    if atheoretical and len(atheoretical) >= 2:
        candidates.append({
            "gap_statement": ("Atheoretical evidence base: "
                              f"{len(atheoretical)} of {len(evidence)} study "
                              f"objectives invoke no named theory or framework, "
                              f"so there is no articulated mechanism explaining "
                              f"_why_ the effects occur."),
            "gap_type": "theoretical",
            "known_summary": "Objectives describe what was studied, not why.",
            "unknown_summary": ("A theory-grounded account of the mechanism "
                                "linking the observed variables."),
            "supporting_evidence": [_cite(ev) for ev in atheoretical[:3]],
            "importance_score": 7,
            "feasibility_score": 8,
            "_context": "mechanism explains why " + " ".join(
                ev.objective or "" for ev in atheoretical),
        })

    # ---- Rule-1 filter + dedupe + rank ----
    seen = set()
    final: List[Dict] = []
    for c in candidates:
        key = c["gap_statement"][:60]
        if key in seen:
            continue
        seen.add(key)
        reason = _is_banned_novelty(c["gap_statement"], c["gap_type"],
                                    c.pop("_context", ""))
        if reason:
            log.info("Gap candidate rejected by rule 1: %s", reason)
            continue
        final.append(c)

    final.sort(key=lambda c: (c["importance_score"] + c["feasibility_score"]),
               reverse=True)
    return final[:max_gaps]


# ---------------------------------------------------------------------------
# 2. Novelty-challenge counter-search (tries to DISPROVE the gap)
# ---------------------------------------------------------------------------

def execute_novelty_challenge_search(gap_claim: str, db: Session | None = None,
                                    limit: int = 5) -> Dict:
    """Search for prior art that could disprove the gap claim.

    Returns results with source links and a verdict. 'challenged_disproved'
    means conflicting prior art was found — the gap must NOT be claimed.
    """
    terms = _keywords(gap_claim, top_n=8)
    counter_query = " ".join(terms)
    log.info("Counter-search for prior art: %r", counter_query)

    found: List[Dict] = []
    errors: List[str] = []
    try:
        found.extend(search_semantic_scholar(counter_query, limit=limit))
    except Exception as exc:
        errors.append(f"semantic_scholar: {exc}")
    try:
        found.extend(search_pubmed(counter_query, limit=limit))
    except Exception as exc:
        errors.append(f"pubmed: {exc}")
    found = deduplicate_papers(found)

    term_set = set(terms)
    scored = []
    for p in found:
        hay = f"{p.get('title', '')} {p.get('abstract') or ''}".lower()
        hay_terms = set(re.findall(r"[a-z]{4,}", hay))
        overlap = len(term_set & hay_terms) / max(len(term_set), 1)
        scored.append({
            "title": p.get("title"),
            "doi": p.get("doi"),
            "source_link": p.get("source_link"),
            "source_api": p.get("source_api"),
            "relevance_note": f"term overlap {overlap:.0%} with gap claim",
            "_overlap": overlap,
        })
    scored.sort(key=lambda r: r["_overlap"], reverse=True)

    strong_hits = [r for r in scored if r["_overlap"] >= 0.35]
    if len(strong_hits) >= 2:
        verdict = "challenged_disproved"
        note = (f"{len(strong_hits)} prior-art result(s) substantially overlap "
                f"the gap claim — novelty NOT established.")
    elif strong_hits:
        verdict = "challenged_disproved"
        note = ("One strongly overlapping prior-art result found — treat the "
                "gap as contested until a researcher reviews it.")
    else:
        verdict = "challenged_pass"
        note = ("No strongly overlapping prior art found in this counter-search. "
                "Residual uncertainty remains — researcher approval still required.")

    results = [{k: v for k, v in r.items() if not k.startswith("_")}
               for r in scored[:limit]]
    return {
        "gap_claim": gap_claim[:200],
        "counter_query": counter_query,
        "results": results,
        "verdict": verdict,
        "note": note,
        "errors": errors,
        "method": METHOD,
    }


# ---------------------------------------------------------------------------
# 3-5. Persistence, briefs, review
# ---------------------------------------------------------------------------

def run_gap_discovery(project_id: str, db: Session,
                      task_id: str | None = None,
                      max_gaps: int = 5) -> List[Dict]:
    """Full Agent 3 run: analyze -> challenge each candidate -> persist."""
    candidates = analyze_evidence_matrix_for_gaps(
        project_id, db, task_id=task_id, max_gaps=max_gaps)
    briefs = []
    for c in candidates:
        challenge = execute_novelty_challenge_search(c["gap_statement"], db)
        row = GapBrief(
            project_id=project_id,
            task_id=task_id,
            gap_statement=c["gap_statement"],
            gap_type=c["gap_type"],
            known_summary=c.get("known_summary"),
            unknown_summary=c.get("unknown_summary"),
            supporting_evidence=json.dumps(c.get("supporting_evidence", [])),
            counter_search_query=challenge["counter_query"],
            counter_search_results=json.dumps(challenge["results"]),
            importance_score=c.get("importance_score"),
            feasibility_score=c.get("feasibility_score"),
            verification_status=challenge["verdict"],
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        briefs.append(generate_gap_brief(row.id, db))
        log.info("Gap brief %s: %s (%s)", row.id[:8], c["gap_type"],
                 challenge["verdict"])
    return briefs


def get_gap(db: Session, gap_id: str) -> GapBrief | None:
    return db.get(GapBrief, gap_id)


def list_gaps(db: Session, project_id: str | None = None,
              task_id: str | None = None, limit: int = 100) -> List[GapBrief]:
    stmt = select(GapBrief).order_by(GapBrief.created_at.desc())
    if project_id:
        stmt = stmt.where(GapBrief.project_id == project_id)
    if task_id:
        stmt = stmt.where(GapBrief.task_id == task_id)
    return list(db.scalars(stmt.limit(limit)).all())


def generate_gap_brief(gap_id: str, db: Session) -> Dict:
    """Structured gap brief (spec Appendix A3 + compulsory attachments)."""
    gap = get_gap(db, gap_id)
    if gap is None:
        raise ValueError(f"Unknown gap_id: {gap_id}")
    return {
        "gap_id": gap.id,
        "project_id": gap.project_id,
        "task_id": gap.task_id,
        "gap_statement": gap.gap_statement,
        "gap_type": gap.gap_type,
        "known_vs_unknown": {
            "known": gap.known_summary,
            "unknown": gap.unknown_summary,
        },
        "supporting_evidence": gap.supporting_list,  # paper source links
        "counter_search": {
            "query": gap.counter_search_query,
            "findings": gap.counter_list,  # prior-art links
        },
        "scores": {
            "importance": gap.importance_score,
            "feasibility": gap.feasibility_score,
        },
        "verification_status": gap.verification_status,
        "researcher_decision": gap.researcher_decision,
        "researcher_note": gap.researcher_note,
        "method": METHOD,
    }


def review_gap(db: Session, gap_id: str, approved: bool, note: str = "",
               design_type: str | None = None) -> GapBrief:
    """Researcher accept/reject a gap. Disproved gaps cannot be approved."""
    gap = get_gap(db, gap_id)
    if gap is None:
        raise ValueError(f"Unknown gap_id: {gap_id}")
    if approved and gap.verification_status == "challenged_disproved":
        raise ValueError(
            "Gap was disproved by the novelty challenge — it cannot be "
            "approved as novel. Narrow or drop it instead."
        )
    if design_type is not None and design_type not in DESIGN_TYPES:
        raise ValueError(f"Unknown design_type: {design_type!r}")
    gap.researcher_decision = "approved" if approved else "rejected"
    gap.researcher_note = note
    if design_type:
        gap.design_type = design_type
    db.commit()
    db.refresh(gap)
    return gap
