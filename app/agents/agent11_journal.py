"""Agent 11 — Journal Selection & Review Response.

Compares journal scope/requirements from a curated, date-stamped journal
knowledge base and organizes reviewer comments into a response matrix.

COMPULSORY RULES (spec 2 / Agent 11):
  - Rankings, APCs and requirements are date-stamped; the researcher must
    verify against the official current journal page before submitting.
  - The system NEVER submits articles or sends correspondence — drafting
    the response matrix is the limit; sending is human-only.
"""
import json
import logging
import re
from datetime import date
from typing import Dict, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.schemas import JournalMatch, ReviewerResponse

log = logging.getLogger(__name__)

METHOD = "journal_match_v1"
CHECKED = "2026-10-09"  # knowledge-base date stamp

# Curated journal knowledge base. Scope keywords drive matching;
# requirements/APCs are date-stamped and MUST be re-verified officially.
JOURNAL_DB: List[Dict] = [
    {
        "name": "Computers in Human Behavior",
        "scope_keywords": {"human-computer interaction", "trust", "ai",
                           "technology acceptance", "user behavior", "chatbot"},
        "scope_summary": "Human behavior in relation to computer systems and technology.",
        "requirements": {"article_types": ["full length article", "short communication"],
                         "word_limit": "~8000 excluding references",
                         "data_policy": "data availability statement encouraged"},
        "apc_info": "Hybrid; check current APC on the official page.",
        "source_link": "https://www.sciencedirect.com/journal/computers-in-human-behavior",
    },
    {
        "name": "International Journal of Human-Computer Studies",
        "scope_keywords": {"hci", "usability", "user study", "interface",
                           "human factors", "interaction design"},
        "scope_summary": "Human-computer interaction theory, method and application.",
        "requirements": {"article_types": ["research article"],
                         "word_limit": "no strict limit; concise preferred",
                         "data_policy": "data availability statement required"},
        "apc_info": "Hybrid; check current APC on the official page.",
        "source_link": "https://www.sciencedirect.com/journal/international-journal-of-human-computer-studies",
    },
    {
        "name": "MIS Quarterly",
        "scope_keywords": {"information systems", "technology acceptance",
                           "digital", "organizations", "is research"},
        "scope_summary": "Top-tier information systems research.",
        "requirements": {"article_types": ["research article"],
                         "word_limit": "follows MISQ guidelines",
                         "data_policy": "replication materials encouraged"},
        "apc_info": "Subscription; no APC for accepted articles (verify).",
        "source_link": "https://misq.umn.edu/",
    },
    {
        "name": "Journal of the American Medical Informatics Association",
        "scope_keywords": {"health informatics", "clinical", "ehr",
                           "health ai", "biomedical"},
        "scope_summary": "Biomedical and health informatics research.",
        "requirements": {"article_types": ["research article", "brief communication"],
                         "word_limit": "3500 (research), check current guide",
                         "data_policy": "data availability statement required"},
        "apc_info": "Hybrid; check current APC on the official page.",
        "source_link": "https://academic.oup.com/jamia",
    },
    {
        "name": "Frontiers in Psychology",
        "scope_keywords": {"psychology", "behavior", "cognition", "survey",
                           "experimental", "human behavior"},
        "scope_summary": "Broad-scope open-access psychology.",
        "requirements": {"article_types": ["original research", "review", "brief report"],
                         "word_limit": "12000 max",
                         "data_policy": "data availability statement required"},
        "apc_info": "Open access APC applies; check current fee officially.",
        "source_link": "https://www.frontiersin.org/journals/psychology",
    },
    {
        "name": "PLOS ONE",
        "scope_keywords": {"multidisciplinary", "empirical", "replication",
                           "methods", "data-driven"},
        "scope_summary": "Multidisciplinary; judges technical soundness, not novelty.",
        "requirements": {"article_types": ["research article"],
                         "word_limit": "no strict limit",
                         "data_policy": "data availability mandatory"},
        "apc_info": "Open access APC applies; check current fee officially.",
        "source_link": "https://journals.plos.org/plosone/",
    },
    {
        "name": "Human Factors",
        "scope_keywords": {"human factors", "ergonomics", "safety",
                           "automation", "operator", "workload"},
        "scope_summary": "Human factors and ergonomics science.",
        "requirements": {"article_types": ["research article", "practitioner article"],
                         "word_limit": "check current author guidelines",
                         "data_policy": "data availability encouraged"},
        "apc_info": "Hybrid; check current APC on the official page.",
        "source_link": "https://journals.sagepub.com/home/hfs",
    },
    {
        "name": "Journal of Medical Internet Research",
        "scope_keywords": {"digital health", "mhealth", "telemedicine",
                           "health ai", "ehealth"},
        "scope_summary": "Digital health and medical internet research.",
        "requirements": {"article_types": ["original paper", "viewpoint"],
                         "word_limit": "check current guidelines",
                         "data_policy": "data sharing encouraged"},
        "apc_info": "Open access APC applies; check current fee officially.",
        "source_link": "https://www.jmir.org/",
    },
    # --- Transportation / mobility / human-factors cluster (added 2026-10-12
    # for a client in emerging mobility + human-AI interaction research) ---
    {
        "name": "Transportation Research Part F: Traffic Psychology and Behaviour",
        "scope_keywords": {"traffic psychology", "driver behavior", "human factors",
                           "trust", "autonomous vehicles", "risk perception",
                           "attention", "mental workload"},
        "scope_summary": "Human behaviour in traffic: driver psychology, trust in automation, attention and workload.",
        "requirements": {"article_types": ["full length article", "short communication"],
                         "word_limit": "~8000 excluding references",
                         "data_policy": "data availability statement encouraged"},
        "apc_info": "Hybrid; check current APC on the official page.",
        "source_link": "https://www.sciencedirect.com/journal/transportation-research-part-f-traffic-psychology-and-behaviour",
    },
    {
        "name": "Accident Analysis & Prevention",
        "scope_keywords": {"road crash analysis", "injury severity", "traffic safety",
                           "pedestrian safety", "motorcycles", "statistical modeling"},
        "scope_summary": "Road safety: crash analysis, injury severity, countermeasures.",
        "requirements": {"article_types": ["full length article"],
                         "word_limit": "~8000 excluding references",
                         "data_policy": "data availability statement encouraged"},
        "apc_info": "Hybrid; check current APC on the official page.",
        "source_link": "https://www.sciencedirect.com/journal/accident-analysis-and-prevention",
    },
    {
        "name": "Transportation Research Part C: Emerging Technologies",
        "scope_keywords": {"emerging technologies", "autonomous vehicles", "robotaxis",
                           "intelligent transportation", "machine learning",
                           "connected vehicles", "urban air mobility"},
        "scope_summary": "Emerging transport tech: automation, connectivity, AI methods.",
        "requirements": {"article_types": ["full length article"],
                         "word_limit": "~8000 excluding references",
                         "data_policy": "data availability statement encouraged"},
        "apc_info": "Hybrid; check current APC on the official page.",
        "source_link": "https://www.sciencedirect.com/journal/transportation-research-part-c-emerging-technologies",
    },
    {
        "name": "IEEE Transactions on Intelligent Transportation Systems",
        "scope_keywords": {"intelligent transportation systems", "autonomous vehicles",
                           "deep learning", "machine learning", "remote driving",
                           "decision-making", "explainable ai"},
        "scope_summary": "ITS: vehicle automation, AI/ML methods, systems engineering.",
        "requirements": {"article_types": ["research article"],
                         "word_limit": "check current author guidelines",
                         "data_policy": "reproducibility materials encouraged"},
        "apc_info": "Hybrid; check current APC on the official page.",
        "source_link": "https://ieeexplore.ieee.org/xpl/RecentIssue.jsp?punumber=6979",
    },
    {
        "name": "Journal of Safety Research",
        "scope_keywords": {"safety", "vulnerable road users", "pedestrian safety",
                           "e-scooters", "e-bikes", "injury prevention", "human behavior"},
        "scope_summary": "Safety science across transport and occupational domains.",
        "requirements": {"article_types": ["research article"],
                         "word_limit": "check current author guidelines",
                         "data_policy": "data availability encouraged"},
        "apc_info": "Hybrid; check current APC on the official page.",
        "source_link": "https://www.sciencedirect.com/journal/journal-of-safety-research",
    },
    {
        "name": "Traffic Injury Prevention",
        "scope_keywords": {"traffic injury", "injury severity", "pedestrians",
                           "two-wheelers", "crash analysis", "biomechanics"},
        "scope_summary": "Traffic injury epidemiology, biomechanics and prevention.",
        "requirements": {"article_types": ["original article", "brief report"],
                         "word_limit": "check current author guidelines",
                         "data_policy": "data availability encouraged"},
        "apc_info": "Hybrid; check current APC on the official page.",
        "source_link": "https://www.tandfonline.com/journals/gcpi20",
    },
    {
        "name": "Transportation Research Part A: Policy and Practice",
        "scope_keywords": {"transportation policy", "travel behavior",
                           "technology adoption", "behavioral intentions",
                           "emerging mobility acceptance"},
        "scope_summary": "Transport policy and practice: behavior, adoption, regulation.",
        "requirements": {"article_types": ["full length article"],
                         "word_limit": "~11000",
                         "data_policy": "data availability statement encouraged"},
        "apc_info": "Hybrid; check current APC on the official page.",
        "source_link": "https://www.sciencedirect.com/journal/transportation-research-part-a-policy-and-practice",
    },
    {
        "name": "Travel Behaviour and Society",
        "scope_keywords": {"travel behavior", "travel choices", "technology acceptance",
                           "behavioral intentions", "mobility", "survey"},
        "scope_summary": "Travel behaviour, choices and societal impacts of mobility.",
        "requirements": {"article_types": ["research article"],
                         "word_limit": "check current author guidelines",
                         "data_policy": "data availability encouraged"},
        "apc_info": "Hybrid; check current APC on the official page.",
        "source_link": "https://www.sciencedirect.com/journal/travel-behaviour-and-society",
    },
    {
        "name": "Transport Policy",
        "scope_keywords": {"transportation policy", "regulation", "governance",
                           "autonomous vehicles", "urban air mobility", "emerging mobility"},
        "scope_summary": "Policy analysis for transport systems and emerging modes.",
        "requirements": {"article_types": ["research article"],
                         "word_limit": "8000-10000",
                         "data_policy": "data availability encouraged"},
        "apc_info": "Hybrid; check current APC on the official page.",
        "source_link": "https://www.sciencedirect.com/journal/transport-policy",
    },
    {
        "name": "Transportation Research Interdisciplinary Perspectives",
        "scope_keywords": {"interdisciplinary", "emerging mobility", "urban air mobility",
                           "sustainability", "equity", "future mobility"},
        "scope_summary": "Open-access interdisciplinary transport research.",
        "requirements": {"article_types": ["research article", "review"],
                         "word_limit": "check current author guidelines",
                         "data_policy": "data availability statement required"},
        "apc_info": "Open access APC applies; check current fee officially.",
        "source_link": "https://www.sciencedirect.com/journal/transportation-research-interdisciplinary-perspectives",
    },
    {
        "name": "Journal of Intelligent Transportation Systems",
        "scope_keywords": {"intelligent transportation", "autonomous vehicles",
                           "human-machine interaction", "llm applications",
                           "data analytics", "statistical modeling"},
        "scope_summary": "ITS technology, planning and human-systems integration.",
        "requirements": {"article_types": ["research article"],
                         "word_limit": "check current author guidelines",
                         "data_policy": "data availability encouraged"},
        "apc_info": "Hybrid; check current APC on the official page.",
        "source_link": "https://www.tandfonline.com/journals/gits20",
    },
    {
        "name": "Applied Ergonomics",
        "scope_keywords": {"ergonomics", "human factors", "mental workload",
                           "attention", "human-machine interaction", "trust",
                           "operator behavior"},
        "scope_summary": "Applied ergonomics and human factors across domains.",
        "requirements": {"article_types": ["full length article", "short communication"],
                         "word_limit": "~8000 excluding references",
                         "data_policy": "data availability statement encouraged"},
        "apc_info": "Hybrid; check current APC on the official page.",
        "source_link": "https://www.sciencedirect.com/journal/applied-ergonomics",
    },
]


def _score(keywords: List[str], journal: Dict) -> float:
    kws = set()
    for kw in keywords:
        kws.update(re.findall(r"[a-z]+", kw.lower()))
    scope = journal["scope_keywords"]
    if not kws:
        return 0.0
    hits = sum(1 for w in kws if any(w in s or s in w for s in scope))
    return round(hits / len(kws), 3)


def match_journals(db: Session, project_id: str, keywords: List[str],
                   task_id: str | None = None,
                   manuscript_id: str | None = None,
                   top_n: int = 5) -> List[Dict]:
    """Score journals by scope-keyword overlap; persist top matches."""
    ranked = sorted(
        ({"journal": j, "score": _score(keywords, j)} for j in JOURNAL_DB),
        key=lambda x: x["score"], reverse=True)[:top_n]
    out = []
    for item in ranked:
        j = item["journal"]
        row = JournalMatch(
            project_id=project_id, task_id=task_id,
            manuscript_id=manuscript_id, journal_name=j["name"],
            scope_summary=j["scope_summary"], match_score=item["score"],
            requirements=json.dumps(j["requirements"]),
            apc_info=j["apc_info"], checked_date=CHECKED,
            source_link=j["source_link"],
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        out.append({
            "id": row.id, "journal_name": j["name"],
            "scope_summary": j["scope_summary"], "match_score": item["score"],
            "requirements": j["requirements"], "apc_info": j["apc_info"],
            "checked_date": CHECKED, "source_link": j["source_link"],
            "verify_note": ("Scope/APC data date-stamped "
                            f"{CHECKED}. Verify against the official journal "
                            "page before submitting. This system never submits."),
        })
    log.info("Matched %d journals for %d keywords", len(out), len(keywords))
    return out


def list_matches(db: Session, project_id: str | None = None,
                 limit: int = 50) -> List[JournalMatch]:
    stmt = select(JournalMatch).order_by(JournalMatch.match_score.desc())
    if project_id:
        stmt = stmt.where(JournalMatch.project_id == project_id)
    return list(db.scalars(stmt.limit(limit)).all())


# ---------------------------------------------------------------------------
# Reviewer response matrix
# ---------------------------------------------------------------------------

def build_reviewer_response_matrix(db: Session, project_id: str,
                                   comments: List[Dict],
                                   manuscript_id: str | None = None) -> List[Dict]:
    """Organize reviewer comments into a response matrix.

    Responses are drafted by the RESEARCHER — the matrix tracks comment,
    response, manuscript change and status. The system never sends anything.
    """
    out = []
    for c in comments:
        row = ReviewerResponse(
            project_id=project_id, manuscript_id=manuscript_id,
            reviewer=c.get("reviewer"), comment=c["comment"],
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        out.append({"id": row.id, "reviewer": row.reviewer,
                    "comment": row.comment, "response": row.response,
                    "manuscript_change": row.manuscript_change,
                    "status": row.status})
    return out


def update_response(db: Session, response_id: str,
                    response: str | None = None,
                    manuscript_change: str | None = None,
                    status: str | None = None) -> Dict:
    row = db.get(ReviewerResponse, response_id)
    if row is None:
        raise ValueError(f"Unknown response id: {response_id}")
    if response is not None:
        row.response = response
    if manuscript_change is not None:
        row.manuscript_change = manuscript_change
    if status is not None:
        if status not in {"pending", "addressed", "rebutted", "wont_fix"}:
            raise ValueError(f"Unknown status: {status!r}")
        row.status = status
    db.commit()
    db.refresh(row)
    return {"id": row.id, "reviewer": row.reviewer,
            "comment": row.comment, "response": row.response,
            "manuscript_change": row.manuscript_change, "status": row.status}


def list_responses(db: Session, project_id: str | None = None,
                   manuscript_id: str | None = None,
                   limit: int = 200) -> List[ReviewerResponse]:
    stmt = select(ReviewerResponse).order_by(ReviewerResponse.created_at)
    if project_id:
        stmt = stmt.where(ReviewerResponse.project_id == project_id)
    if manuscript_id:
        stmt = stmt.where(ReviewerResponse.manuscript_id == manuscript_id)
    return list(db.scalars(stmt.limit(limit)).all())
