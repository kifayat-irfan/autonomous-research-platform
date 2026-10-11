"""Agent 12 — Research Intelligence & Significance Monitoring.

Runs saved literature watches (Agent 1 discovery), deduplicates against the
existing paper bank, scores new papers for project relevance, and raises
alerts — including "novelty challenge" alerts when a new paper may
undermine an approved gap.

COMPULSORY RULES (spec 2 / Agent 12):
  - Agent 1 discovers; Agent 12 interprets relevance. No silent modification
    of approved research plans — alerts are for researcher review.
  - Every alert links to the paper's source.
"""
import json
import logging
import re
from datetime import datetime
from typing import Dict, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.agent1_discovery import run_discovery
from app.models.schemas import Alert, GapBrief, Paper, WatchTopic
from app.services import paper_service

log = logging.getLogger(__name__)

METHOD = "watch_monitor_v1"
RELEVANCE_THRESHOLD = 0.25


def _keywords(text: str) -> set:
    stop = {"the", "and", "with", "from", "that", "this", "study",
            "studies", "research", "using", "based"}
    return {w for w in re.findall(r"[a-z]{4,}", (text or "").lower())
            if w not in stop}


def _relevance(paper: Dict, watch: WatchTopic) -> float:
    q_terms = _keywords(watch.query)
    p_terms = _keywords(f"{paper.get('title', '')} {paper.get('abstract') or ''}")
    if not q_terms:
        return 0.0
    return round(len(q_terms & p_terms) / len(q_terms), 3)


def create_watch(db: Session, project_id: str, name: str, query: str,
                 sources: List[str] | None = None) -> WatchTopic:
    watch = WatchTopic(project_id=project_id, name=name, query=query,
                       sources=json.dumps(sources or ["semantic_scholar", "pubmed"]))
    db.add(watch)
    db.commit()
    db.refresh(watch)
    return watch


def list_watches(db: Session, project_id: str | None = None,
                 limit: int = 100) -> List[WatchTopic]:
    stmt = select(WatchTopic).order_by(WatchTopic.created_at.desc())
    if project_id:
        stmt = stmt.where(WatchTopic.project_id == project_id)
    return list(db.scalars(stmt.limit(limit)).all())


def _existing_dois(db: Session, project_id: str) -> set:
    rows = db.scalars(
        select(Paper.doi).where(Paper.project_id == project_id)).all()
    return {d.lower() for d in rows if d}


def run_watch(db: Session, watch_id: str, limit: int = 10) -> Dict:
    """Execute one monitoring run: discover, dedupe, score, alert."""
    watch = db.get(WatchTopic, watch_id)
    if watch is None:
        raise ValueError(f"Unknown watch id: {watch_id}")

    papers = run_discovery(watch.query, limit=limit,
                           sources=watch.sources_list, download_pdfs=False)
    known_dois = _existing_dois(db, watch.project_id)
    new_papers = [p for p in papers
                  if (p.get("doi") or "").lower() not in known_dois]

    # Novelty-challenge context: approved gaps whose counter-search terms
    # overlap the new paper -> potential threat to the gap's novelty.
    gaps = db.scalars(
        select(GapBrief).where(GapBrief.project_id == watch.project_id,
                               GapBrief.researcher_decision == "approved")).all()
    gap_terms = [(_keywords(g.counter_search_query), g) for g in gaps]

    alerts: List[Dict] = []
    for p in new_papers:
        paper = paper_service.upsert_paper(
            db, p, project_id=watch.project_id, task_id=None)
        rel = _relevance(p, watch)
        p_terms = _keywords(f"{p.get('title', '')} {p.get('abstract') or ''}")
        challenged = [g for terms, g in gap_terms
                      if terms and len(terms & p_terms) / len(terms) >= 0.5]
        if challenged:
            alert_type = "novelty_challenge"
            reason = ("New paper overlaps the counter-search terms of approved "
                      f"gap(s) {[g.id[:8] for g in challenged]} — novelty may be "
                      "threatened. Review before proceeding.")
            rel = max(rel, 0.8)
        elif rel >= RELEVANCE_THRESHOLD:
            alert_type = "new_paper"
            reason = (f"New paper matches watch '{watch.name}' "
                      f"(relevance {rel:.0%}).")
        else:
            continue  # low relevance: stored in bank, no alert (suppress noise)
        alert = Alert(
            project_id=watch.project_id, watch_id=watch.id,
            paper_id=paper.id, alert_type=alert_type,
            title=p.get("title") or "(untitled)",
            reason=reason, relevance_score=rel,
            source_link=p.get("source_link"),
        )
        db.add(alert)
        db.commit()
        db.refresh(alert)
        alerts.append({"alert_id": alert.id, "type": alert_type,
                       "title": alert.title, "relevance": rel,
                       "source_link": alert.source_link, "reason": reason})

    watch.last_run = datetime.utcnow()
    try:
        wm = json.loads(watch.watermark or "{}")
    except (json.JSONDecodeError, TypeError):
        wm = {}
    wm["last_new_dois"] = [p.get("doi") for p in new_papers if p.get("doi")]
    watch.watermark = json.dumps(wm)
    db.commit()

    log.info("Watch %s: %d new papers, %d alerts", watch.id[:8],
             len(new_papers), len(alerts))
    return {"watch_id": watch.id, "new_papers": len(new_papers),
            "alerts": alerts, "method": METHOD,
            "note": "Alerts are for researcher review; approved plans are "
                    "never modified automatically."}


def list_alerts(db: Session, project_id: str | None = None,
                acknowledged: bool | None = None,
                limit: int = 100) -> List[Alert]:
    stmt = select(Alert).order_by(Alert.created_at.desc())
    if project_id:
        stmt = stmt.where(Alert.project_id == project_id)
    if acknowledged is not None:
        stmt = stmt.where(Alert.acknowledged == acknowledged)
    return list(db.scalars(stmt.limit(limit)).all())


def acknowledge_alert(db: Session, alert_id: str) -> Alert:
    alert = db.get(Alert, alert_id)
    if alert is None:
        raise ValueError(f"Unknown alert id: {alert_id}")
    alert.acknowledged = True
    db.commit()
    return alert
