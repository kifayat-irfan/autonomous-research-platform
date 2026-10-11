"""Agent 9 — Cross-Domain Research Idea Development.

Connects theories/constructs from the source domain's evidence to a target
domain, producing concept notes with dual-domain citations.

COMPULSORY RULES (spec 2 / Agent 9):
  1. Every concept cites foundational papers from BOTH the source domain
     and the target domain, with active source links.
  2. All ideas are labeled "hypotheses pending novelty search" until they
     pass through Agent 3's counter-search pipeline.
"""
import json
import logging
import re
from collections import Counter
from typing import Dict, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.agent1_discovery import deduplicate_papers, search_semantic_scholar
from app.models.schemas import ConceptNote, Evidence, Paper

log = logging.getLogger(__name__)

METHOD = "cross_domain_bridge_v1"

# Construct keyword -> transfer pattern (target-agnostic templates).
_TRANSFER_PATTERNS = [
    ({"trust", "reliance", "calibration"},
     "Trust calibration transfer",
     "Apply trust-calibration constructs (appropriate reliance, trust repair) "
     "from {source} to operator oversight in {target}.",
     ["Design reliance dashboards that show system uncertainty, not just outputs.",
      "Validate the trust scale in the target operator population first."]),
    ({"acceptance", "adoption", "usefulness", "ease of use"},
     "Technology acceptance transfer",
     "Use acceptance models (usefulness, ease of use, social influence) to "
     "explain adoption barriers for {target} systems.",
     ["Re-validate acceptance scales for the new user group.",
      "Add domain-specific barriers (regulation, safety culture) as new constructs."]),
    ({"workload", "cognitive load", "mental demand", "fatigue"},
     "Workload-aware allocation transfer",
     "Transfer workload measurement to adaptive task allocation in {target} "
     "operations.",
     ["Calibrate workload thresholds for the target task context.",
      "Combine subjective (NASA-TLX) with behavioral/physiological indicators."]),
    ({"satisfaction", "usability", "experience"},
     "Experience evaluation transfer",
     "Port usability/experience evaluation instruments to {target} interfaces.",
     ["Adapt SUS/UEQ items to target-domain terminology (mark as adaptations).",
      "Triangulate with task performance metrics."]),
]


def _keywords(text: str, top_n: int = 12) -> List[str]:
    stop = {"the", "and", "with", "from", "that", "this", "were", "have",
            "study", "studies", "results", "research", "findings"}
    toks = re.findall(r"[a-z]{4,}", (text or "").lower())
    return [w for w, _ in Counter(t for t in toks if t not in stop).most_common(top_n)]


def _match_pattern(constructs: List[str]) -> Dict:
    words = set()
    for c in constructs:
        words.update(re.findall(r"[a-z]+", c.lower()))
    best, best_score = None, 0
    for kws, name, template, adaptations in _TRANSFER_PATTERNS:
        score = len(words & kws)
        if score > best_score:
            best = {"name": name, "template": template,
                    "adaptations": adaptations}
            best_score = score
    if best is None:
        best = {"name": "Construct portability transfer",
                "template": ("Port the '{constructs}' measurement approach from "
                             "{source} to evaluation in {target}."),
                "adaptations": ["Re-validate all scales in the target population.",
                                "Document every item adaptation explicitly."]}
    return best


def _target_domain_citations(target_domain: str, constructs: List[str],
                             limit: int = 3) -> List[Dict]:
    """Foundational target-domain papers via Agent 1 (live discovery)."""
    query = f"{target_domain} {' '.join(constructs[:3])}".strip()
    try:
        found = deduplicate_papers(
            search_semantic_scholar(query, limit=limit))
    except Exception as exc:
        log.warning("Target-domain discovery failed: %s", exc)
        return []
    return [{
        "title": p.get("title"),
        "doi": p.get("doi"),
        "source_link": p.get("source_link"),
        "domain": "target",
    } for p in found if p.get("source_link")]


# ---------------------------------------------------------------------------
# 1. Bridge identification
# ---------------------------------------------------------------------------

def identify_cross_domain_bridges(source_domain_evidence: List[Dict],
                                  target_domain: str, db: Session,
                                  project_id: str, task_id: str | None = None,
                                  source_domain: str | None = None,
                                  max_ideas: int = 3) -> List[Dict]:
    """Propose cross-domain transfers; each becomes a hypothesis-pending note."""
    # Source constructs from the evidence keywords.
    corpus = " ".join(
        f"{e.get('claim_text', '')} {e.get('objective', '')}"
        for e in source_domain_evidence)
    constructs = _keywords(corpus, top_n=8) or ["focal construct"]

    # Source citations: the actual evidence papers (with links).
    source_cites = []
    seen = set()
    for e in source_domain_evidence:
        pid = e.get("paper_id")
        if not pid or pid in seen:
            continue
        seen.add(pid)
        p = db.get(Paper, pid)
        if p and p.source_link:
            source_cites.append({
                "title": p.title, "doi": p.doi,
                "source_link": p.source_link, "domain": "source",
            })
        if len(source_cites) >= 4:
            break

    target_cites = _target_domain_citations(target_domain, constructs)

    notes = []
    # Vary the construct focus across ideas for diversity.
    for i in range(min(max_ideas, max(1, len(constructs) // 2))):
        focus = constructs[i * 2:(i * 2) + 2] or constructs[:2]
        pattern = _match_pattern(focus)
        src = source_domain or "source domain literature"
        bridge_concept = pattern["template"].format(
            source=src, target=target_domain,
            constructs=", ".join(focus))
        feasibility = evaluate_transfer_feasibility({
            "bridge_concept": bridge_concept,
            "target_domain": target_domain,
            "constructs": focus,
        })
        row = ConceptNote(
            project_id=project_id, task_id=task_id,
            title=f"{pattern['name']}: {target_domain}",
            source_domain=src, target_domain=target_domain,
            bridge_concept=bridge_concept,
            shared_mechanism=(f"Shared mechanism: the psychological/behavioral "
                              f"processes underlying {', '.join(focus)} are "
                              f"expected to operate in {target_domain}, subject "
                              f"to the adaptations below."),
            source_constructs=json.dumps(focus),
            target_application=(f"Apply {', '.join(focus)} theory and measurement "
                                f"to {target_domain} problems."),
            required_adaptations=json.dumps(pattern["adaptations"]),
            source_citations=json.dumps(source_cites),
            target_citations=json.dumps(target_cites),
            feasibility=json.dumps(feasibility),
            novelty_status="pending_search",
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        notes.append(generate_concept_note(row.id, db))
        log.info("Concept note %s drafted (%s -> %s)", row.id[:8], src,
                 target_domain)
    return notes


# ---------------------------------------------------------------------------
# 2. Transfer feasibility
# ---------------------------------------------------------------------------

def evaluate_transfer_feasibility(bridge_concept: Dict) -> Dict:
    """Assess novelty risks, adaptations, data sources, contribution."""
    target = bridge_concept.get("target_domain", "the target domain")
    constructs = bridge_concept.get("constructs", [])
    return {
        "novelty_risks": [
            "The transfer may already exist in the target literature — "
            "Agent 3 counter-search is mandatory before claiming novelty.",
            "Surface similarity of constructs can mask deep contextual differences.",
        ],
        "required_adaptations": [
            "Re-validate every imported scale in the target population.",
            "Extend the construct set with target-specific factors "
            "(regulation, safety culture, workflow constraints).",
        ],
        "potential_data_sources": [
            f"Operator/participant panels in {target}",
            f"System logs and incident records from {target} deployments",
            "Expert interviews with target-domain practitioners",
        ],
        "expected_contribution": (
            f"First systematic port of {', '.join(constructs) or 'the constructs'} "
            f"theory into {target}, with validated measurement — IF the novelty "
            f"challenge passes."),
        "effort_estimate": "Medium: 1 validation study + 1 main study.",
        "open_challenges": [
            "Construct equivalence across domains is unproven.",
            "Target-domain confounders are not yet mapped.",
        ],
        "status": "hypothesis pending novelty search",
        "method": METHOD,
    }


# ---------------------------------------------------------------------------
# 3. Concept notes + novelty challenge
# ---------------------------------------------------------------------------

def generate_concept_note(note_id: str, db: Session) -> Dict:
    note = db.get(ConceptNote, note_id)
    if note is None:
        raise ValueError(f"Unknown concept note id: {note_id}")
    import json as _j

    def _loads(s, default):
        try:
            return _j.loads(s or "x")
        except Exception:
            return default

    return {
        "note_id": note.id,
        "project_id": note.project_id,
        "task_id": note.task_id,
        "title": note.title,
        "source_domain": note.source_domain,
        "target_domain": note.target_domain,
        "bridge_concept": note.bridge_concept,
        "shared_mechanism": note.shared_mechanism,
        "source_constructs": _loads(note.source_constructs, []),
        "target_application": note.target_application,
        "required_adaptations": _loads(note.required_adaptations, []),
        "source_citations": _loads(note.source_citations, []),
        "target_citations": _loads(note.target_citations, []),
        "feasibility": _loads(note.feasibility, {}),
        "novelty_status": note.novelty_status,
        "novelty_label": ("HYPOTHESIS — pending novelty search (Agent 3). "
                          "Not a finding."),
        "researcher_note": note.researcher_note,
        "method": METHOD,
    }


def challenge_concept_note(note_id: str, db: Session) -> Dict:
    """Run the idea through Agent 3's counter-search pipeline."""
    from app.agents import agent3_gap_discovery as agent3

    note = db.get(ConceptNote, note_id)
    if note is None:
        raise ValueError(f"Unknown concept note id: {note_id}")
    claim = (f"{note.bridge_concept} {note.target_application} "
             f"{note.shared_mechanism}")
    challenge = agent3.execute_novelty_challenge_search(claim, db)
    note.novelty_status = challenge["verdict"]
    note.challenge_results = json.dumps({
        "query": challenge["counter_query"],
        "results": challenge["results"],
        "note": challenge["note"],
    })
    db.commit()
    out = generate_concept_note(note_id, db)
    out["challenge"] = challenge
    return out


def list_concept_notes(db: Session, project_id: str | None = None,
                       limit: int = 100) -> List[ConceptNote]:
    stmt = select(ConceptNote).order_by(ConceptNote.created_at.desc())
    if project_id:
        stmt = stmt.where(ConceptNote.project_id == project_id)
    return list(db.scalars(stmt.limit(limit)).all())
