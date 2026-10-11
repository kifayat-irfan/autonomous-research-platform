"""Agent 10 — Systematic Reviews & Meta-Analysis.

PRISMA 2020-aligned workflow: protocol (PICO + criteria), exact search log,
two-stage screening with exclusion reasons, effect-size extraction with
documented conversions, Risk-of-Bias assessments, and a reproducible
fixed/random-effects meta-analysis.

COMPULSORY RULES (spec 2 / Agent 10):
  - No invented effect sizes or undocumented transformations: every
    conversion is recorded with its formula and inputs.
  - Original and converted estimates are both preserved.
  - Screening decisions require human validation for high-stakes coding
    (the researcher reviews 'maybe' and exclusion reasons).
"""
import json
import logging
import math
from typing import Dict, List

from sqlalchemy.orm import Session

from app.models.schemas import (
    REVIEW_STAGES,
    ROB_RATINGS,
    SCREENING_DECISIONS,
    ReviewProject,
)

log = logging.getLogger(__name__)

METHOD = "systematic_review_v1"

# Documented effect-size conversions (from_type, to_type) -> (formula, fn).
def _or_to_logor(v: float) -> float:
    return math.log(v)


def _logor_to_or(v: float) -> float:
    return math.exp(v)


def _r_to_d(v: float) -> float:
    if abs(v) >= 1:
        raise ValueError("r must be in (-1, 1)")
    return 2 * v / math.sqrt(1 - v ** 2)


def _d_to_r(v: float) -> float:
    return v / math.sqrt(v ** 2 + 4)


_CONVERSIONS = {
    ("or", "logor"): ("ln(OR)", _or_to_logor),
    ("logor", "or"): ("exp(logOR)", _logor_to_or),
    ("r", "d"): ("d = 2r / sqrt(1 - r^2)", _r_to_d),
    ("d", "r"): ("r = d / sqrt(d^2 + 4)", _d_to_r),
}

ROB_DOMAINS = ["randomization", "deviations", "missing_data",
               "measurement", "reporting"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _loads(s: str | None, default):
    try:
        return json.loads(s or "x")
    except (json.JSONDecodeError, TypeError):
        return default


def _get(db: Session, review_id: str) -> ReviewProject:
    r = db.get(ReviewProject, review_id)
    if r is None:
        raise ValueError(f"Unknown review id: {review_id}")
    return r


def _save_list(db: Session, review: ReviewProject, attr: str, items: list):
    setattr(review, attr, json.dumps(items))
    db.commit()
    db.refresh(review)


# ---------------------------------------------------------------------------
# Protocol, search log, screening
# ---------------------------------------------------------------------------

def create_review_project(db: Session, project_id: str, title: str,
                          pico: Dict | None = None,
                          inclusion_criteria: List[str] | None = None,
                          exclusion_criteria: List[str] | None = None,
                          task_id: str | None = None) -> Dict:
    row = ReviewProject(
        project_id=project_id, task_id=task_id, title=title,
        pico=json.dumps(pico or {}),
        inclusion_criteria=json.dumps(inclusion_criteria or []),
        exclusion_criteria=json.dumps(exclusion_criteria or []),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    log.info("Review project %s created", row.id[:8])
    return get_review_dict(row.id, db)


def log_search(db: Session, review_id: str, source: str, query: str,
               hits: int, date: str | None = None) -> Dict:
    """Record the exact search string, source, date and hit count."""
    from datetime import date as _date
    review = _get(db, review_id)
    log_entries = _loads(review.search_log, [])
    log_entries.append({
        "source": source, "query": query, "hits": int(hits),
        "date": date or _date.today().isoformat(),
    })
    _save_list(db, review, "search_log", log_entries)
    return get_review_dict(review_id, db)


def record_screening(db: Session, review_id: str, title: str, decision: str,
                     reason: str = "", stage: str = "title_abstract",
                     paper_id: str | None = None,
                     source_link: str | None = None) -> Dict:
    """Two-stage screening decision with a mandatory exclusion reason."""
    if decision not in SCREENING_DECISIONS:
        raise ValueError(f"Unknown decision: {decision!r}")
    if stage not in REVIEW_STAGES:
        raise ValueError(f"Unknown stage: {stage!r}")
    if decision == "exclude" and not reason.strip():
        raise ValueError("Excluding a record requires a reason (PRISMA).")
    review = _get(db, review_id)
    items = _loads(review.screening, [])
    items.append({"title": title, "paper_id": paper_id, "decision": decision,
                  "reason": reason, "stage": stage, "source_link": source_link})
    _save_list(db, review, "screening", items)
    return get_review_dict(review_id, db)


def prisma_flow(db: Session, review_id: str) -> Dict:
    """Compute PRISMA 2020 flow counts from the search log + screening."""
    review = _get(db, review_id)
    searches = _loads(review.search_log, [])
    screening = _loads(review.screening, [])
    identified = sum(s.get("hits", 0) for s in searches)
    ti = [s for s in screening if s["stage"] == "title_abstract"]
    ft = [s for s in screening if s["stage"] == "full_text"]
    flow = {
        "identification": {
            "records_identified": identified,
            "sources": len(searches),
        },
        "screening": {
            "records_screened": len(ti),
            "records_excluded": sum(1 for s in ti if s["decision"] == "exclude"),
        },
        "eligibility": {
            "full_text_assessed": len(ft),
            "full_text_excluded": sum(1 for s in ft if s["decision"] == "exclude"),
            "exclusion_reasons": {},
        },
        "included": {
            "studies_included": sum(1 for s in ft if s["decision"] == "include"),
        },
        "method": METHOD,
        "note": "Counts are derived from the recorded search log and screening "
                "decisions — rerun after any change.",
    }
    for s in ft:
        if s["decision"] == "exclude":
            r = s.get("reason") or "no reason recorded"
            flow["eligibility"]["exclusion_reasons"][r] = \
                flow["eligibility"]["exclusion_reasons"].get(r, 0) + 1
    review.prisma = json.dumps(flow)
    db.commit()
    return flow


# ---------------------------------------------------------------------------
# Effect extraction with documented conversions
# ---------------------------------------------------------------------------

def convert_effect(value: float, from_type: str, to_type: str) -> Dict:
    """Convert an effect size, documenting the formula used."""
    if from_type == to_type:
        return {"value": value, "from": from_type, "to": to_type,
                "formula": "identity", "converted": False}
    key = (from_type, to_type)
    if key not in _CONVERSIONS:
        raise ValueError(
            f"Unsupported conversion {from_type!r} -> {to_type!r}. "
            f"Supported: {sorted(f'{a}->{b}' for a, b in _CONVERSIONS)}")
    formula, fn = _CONVERSIONS[key]
    return {"value": fn(float(value)), "from": from_type, "to": to_type,
            "formula": formula, "converted": True}


def extract_effect(db: Session, review_id: str, paper_id: str,
                   effect_type: str, value: float, se: float | None = None,
                   ci_lower: float | None = None, ci_upper: float | None = None,
                   n: int | None = None, outcome: str = "",
                   standardize_to: str = "d",
                   source_link: str | None = None) -> Dict:
    """Extract an effect size, preserving original + converted values."""
    review = _get(db, review_id)
    items = _loads(review.extractions, [])
    conv = convert_effect(value, effect_type, standardize_to)
    # Derive SE from CI when not given (95% CI assumed and documented).
    se_used, se_note = se, "as reported"
    if se_used is None and ci_lower is not None and ci_upper is not None:
        se_used = (float(ci_upper) - float(ci_lower)) / (2 * 1.96)
        se_note = "derived from 95% CI: SE = (upper - lower) / (2 * 1.96)"
    items.append({
        "paper_id": paper_id, "outcome": outcome, "n": n,
        "original": {"type": effect_type, "value": value, "se": se,
                     "ci": [ci_lower, ci_upper]},
        "standardized": {"type": standardize_to,
                         "value": conv["value"], "se": se_used,
                         "se_note": se_note,
                         "conversion_formula": conv["formula"]},
        "source_link": source_link,
    })
    _save_list(db, review, "extractions", items)
    return get_review_dict(review_id, db)


# ---------------------------------------------------------------------------
# Risk of Bias + meta-analysis
# ---------------------------------------------------------------------------

def rob_assess(db: Session, review_id: str, paper_id: str,
               domains: Dict[str, str]) -> Dict:
    """RoB assessment across standard domains (researcher-validated)."""
    bad = {k: v for k, v in domains.items()
           if k not in ROB_DOMAINS or v not in ROB_RATINGS}
    if bad:
        raise ValueError(
            f"Invalid RoB ratings: {bad}. Domains: {ROB_DOMAINS}; "
            f"ratings: {sorted(ROB_RATINGS)}.")
    review = _get(db, review_id)
    items = [r for r in _loads(review.rob, []) if r["paper_id"] != paper_id]
    overall = "high" if "high" in domains.values() else (
        "some_concerns" if "some_concerns" in domains.values() else "low")
    items.append({"paper_id": paper_id, "domains": domains,
                  "overall": overall})
    _save_list(db, review, "rob", items)
    return get_review_dict(review_id, db)


def run_meta_analysis(db: Session, review_id: str) -> Dict:
    """Fixed-effect (inverse variance) + DerSimonian-Laird random effects."""
    try:
        import numpy as np
        from scipy import stats as _stats
    except ImportError as exc:
        raise RuntimeError(
            "numpy/scipy are required: pip install numpy scipy") from exc
    review = _get(db, review_id)
    ext = [e for e in _loads(review.extractions, [])
           if e["standardized"].get("se")]
    if len(ext) < 2:
        raise ValueError("Meta-analysis needs at least 2 effects with SEs.")
    yi = np.array([e["standardized"]["value"] for e in ext], dtype=float)
    sei = np.array([e["standardized"]["se"] for e in ext], dtype=float)
    k = len(yi)

    wi = 1.0 / sei ** 2
    fe = float(np.sum(wi * yi) / np.sum(wi))
    se_fe = float(np.sqrt(1.0 / np.sum(wi)))
    Q = float(np.sum(wi * (yi - fe) ** 2))
    df = k - 1
    I2 = max(0.0, (Q - df) / Q) if Q > 0 else 0.0
    tau2 = max(0.0, (Q - df) / (np.sum(wi) - np.sum(wi ** 2) / np.sum(wi)))
    wi_re = 1.0 / (sei ** 2 + tau2)
    re_ = float(np.sum(wi_re * yi) / np.sum(wi_re))
    se_re = float(np.sqrt(1.0 / np.sum(wi_re)))
    z = re_ / se_re if se_re else 0.0
    p = float(2 * (1 - _stats.norm.cdf(abs(z))))

    result = {
        "k": k,
        "effect_metric": ext[0]["standardized"]["type"],
        "fixed_effect": {"pooled": round(fe, 4),
                         "ci95": [round(fe - 1.96 * se_fe, 4),
                                  round(fe + 1.96 * se_fe, 4)]},
        "random_effects": {"pooled": round(re_, 4),
                           "ci95": [round(re_ - 1.96 * se_re, 4),
                                    round(re_ + 1.96 * se_re, 4)],
                           "p": p},
        "heterogeneity": {"Q": round(Q, 3), "df": df,
                          "I2": round(I2, 3), "tau2": round(float(tau2), 4)},
        "studies": [{"paper_id": e["paper_id"],
                     "yi": round(e["standardized"]["value"], 4),
                     "sei": round(e["standardized"]["se"], 4),
                     "weight_pct": round(float(w / np.sum(wi_re) * 100), 1),
                     "source_link": e.get("source_link")}
                    for e, w in zip(ext, wi_re)],
        "method": ("Inverse-variance fixed effect; DerSimonian-Laird "
                   "random effects. " + METHOD),
        "note": "Pooled estimates describe the included studies; "
                "generalization depends on the review's scope and RoB.",
    }
    review.meta_results = json.dumps(result)
    db.commit()
    log.info("Meta-analysis for review %s: k=%d", review_id[:8], k)
    return result


# ---------------------------------------------------------------------------
# Retrieval + review gate
# ---------------------------------------------------------------------------

def get_review_dict(review_id: str, db: Session) -> Dict:
    r = _get(db, review_id)
    return {
        "review_id": r.id, "project_id": r.project_id, "task_id": r.task_id,
        "title": r.title, "pico": r.pico_dict,
        "inclusion_criteria": _loads(r.inclusion_criteria, []),
        "exclusion_criteria": _loads(r.exclusion_criteria, []),
        "search_log": r.search_list, "screening": r.screening_list,
        "prisma": r.prisma_dict, "extractions": r.extraction_list,
        "rob": r.rob_list, "meta_results": r.meta_dict,
        "approval_status": r.approval_status, "method": METHOD,
    }


def list_reviews(db: Session, project_id: str | None = None,
                 limit: int = 100) -> List[ReviewProject]:
    from sqlalchemy import select as _select
    stmt = _select(ReviewProject).order_by(ReviewProject.created_at.desc())
    if project_id:
        stmt = stmt.where(ReviewProject.project_id == project_id)
    return list(db.scalars(stmt.limit(limit)).all())


def review_review(db: Session, review_id: str, approved: bool,
                  note: str = "") -> ReviewProject:
    r = _get(db, review_id)
    r.approval_status = "approved" if approved else "rejected"
    db.commit()
    db.refresh(r)
    return r
