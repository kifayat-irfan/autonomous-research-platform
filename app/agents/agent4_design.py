"""Agent 4 — Research Design & Measurement.

Builds reviewable study protocols from researcher-approved gaps, assembles
measurement models from validated scales (with original citations), and
produces sampling + ethics plans.

COMPULSORY RULES (spec 2 / Agent 4):
  1. Every recommended scale/item carries its original validated citation
     link or source DOI.
  2. Newly written items are NEVER labeled "validated" — they are marked
     "proposed / unvalidated adaptation" and require piloting.
  3. An explicit human approval checkpoint gates any move to data
     collection or analysis.
"""
import json
import logging
import re
from typing import Dict, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.schemas import DESIGN_TYPES, GapBrief, ResearchProtocol

log = logging.getLogger(__name__)

METHOD = "protocol_builder_v1"


# ---------------------------------------------------------------------------
# Validated scale library (original citations — never invent these)
# ---------------------------------------------------------------------------

SCALE_LIBRARY: List[Dict] = [
    {
        "keywords": {"trust", "automation", "reliance", "autonomous"},
        "scale_name": "Checklist for Trust between People and Automation",
        "authors": "Jian, Bisantz & Drury",
        "year": 2000,
        "citation": ("Jian, J.-Y., Bisantz, A. M., & Drury, C. G. (2000). "
                     "Foundations for an empirically determined scale of trust "
                     "in automated systems. International Journal of Cognitive "
                     "Ergonomics, 4(1), 53–71."),
        "source_link": "https://doi.org/10.1207/S15327590IJHC1201_7",
        "n_items": 12,
        "example_item": "The system is dependable.",
        "provenance": "validated",
    },
    {
        "keywords": {"technology acceptance", "usefulness", "ease of use",
                     "adoption", "intention to use"},
        "scale_name": "Technology Acceptance Model (TAM) scales",
        "authors": "Davis",
        "year": 1989,
        "citation": ("Davis, F. D. (1989). Perceived usefulness, perceived ease "
                     "of use, and user acceptance of information technology. "
                     "MIS Quarterly, 13(3), 319–340."),
        "source_link": "https://doi.org/10.2307/249008",
        "n_items": 12,
        "example_item": "Using the system improves my performance.",
        "provenance": "validated",
    },
    {
        "keywords": {"technology acceptance", "utaut", "performance expectancy",
                     "effort expectancy", "social influence"},
        "scale_name": "UTAUT",
        "authors": "Venkatesh, Morris, Davis & Davis",
        "year": 2003,
        "citation": ("Venkatesh, V., Morris, M. G., Davis, G. B., & Davis, F. D. "
                     "(2003). User acceptance of information technology: Toward "
                     "a unified view. MIS Quarterly, 27(3), 425–478."),
        "source_link": "https://doi.org/10.2307/30036540",
        "n_items": 24,
        "example_item": "I find the system useful in my daily life.",
        "provenance": "validated",
    },
    {
        "keywords": {"usability", "satisfaction", "system usability"},
        "scale_name": "System Usability Scale (SUS)",
        "authors": "Brooke",
        "year": 1996,
        "citation": ("Brooke, J. (1996). SUS: A 'quick and dirty' usability "
                     "scale. In P. W. Jordan et al. (Eds.), Usability Evaluation "
                     "in Industry (pp. 189–194). Taylor & Francis."),
        "source_link": "https://www.usability.gov/how-to-and-tools/methods/system-usability-scale.html",
        "n_items": 10,
        "example_item": "I thought the system was easy to use.",
        "provenance": "validated",
    },
    {
        "keywords": {"affect", "emotion", "mood", "wellbeing", "well-being"},
        "scale_name": "Positive and Negative Affect Schedule (PANAS)",
        "authors": "Watson, Clark & Tellegen",
        "year": 1988,
        "citation": ("Watson, D., Clark, L. A., & Tellegen, A. (1988). "
                     "Development and validation of brief measures of positive "
                     "and negative affect. Journal of Personality and Social "
                     "Psychology, 54(6), 1063–1070."),
        "source_link": "https://doi.org/10.1037/0022-3514.54.6.1063",
        "n_items": 20,
        "example_item": "Indicate to what extent you feel 'enthusiastic' right now.",
        "provenance": "validated",
    },
    {
        "keywords": {"workload", "cognitive load", "mental demand", "task load"},
        "scale_name": "NASA Task Load Index (NASA-TLX)",
        "authors": "Hart & Staveland",
        "year": 1988,
        "citation": ("Hart, S. G., & Staveland, L. E. (1988). Development of "
                     "NASA-TLX (Task Load Index). In P. A. Hancock & N. Meshkati "
                     "(Eds.), Human Mental Workload (pp. 139–183). North-Holland."),
        "source_link": "https://humansystems.arc.nasa.gov/groups/TLX/",
        "n_items": 6,
        "example_item": "Mental Demand: How much mental activity was required?",
        "provenance": "validated",
    },
    {
        "keywords": {"personality", "big five", "openness", "conscientiousness",
                     "extraversion", "agreeableness", "neuroticism"},
        "scale_name": "Big Five Inventory (BFI)",
        "authors": "John, Donahue & Kentle; John & Srivastava",
        "year": 1991,
        "citation": ("John, O. P., & Srivastava, S. (1999). The Big Five trait "
                     "taxonomy. In L. A. Pervin & O. P. John (Eds.), Handbook of "
                     "personality (2nd ed., pp. 102–138). Guilford Press."),
        "source_link": "https://www.ocf.berkeley.edu/~johnlab/bfi.htm",
        "n_items": 44,
        "example_item": "I see myself as someone who is talkative.",
        "provenance": "validated",
    },
]


# ---------------------------------------------------------------------------
# 2. Measurement model
# ---------------------------------------------------------------------------

def construct_measurement_model(constructs: List[str]) -> List[Dict]:
    """Map constructs to validated scales; unmatched -> proposed items.

    Newly written items are ALWAYS marked 'proposed_unvalidated' — never
    'validated' (compulsory rule 2).
    """
    model = []
    for construct in constructs:
        c = (construct or "").strip()
        if not c:
            continue
        words = set(re.findall(r"[a-z]+", c.lower()))
        best, best_score = None, 0
        for scale in SCALE_LIBRARY:
            score = len(words & scale["keywords"])
            # partial credit for substring overlap
            score += sum(1 for w in words for k in scale["keywords"]
                         if len(w) > 4 and (w in k or k in w)) * 0.5
            if score > best_score:
                best, best_score = scale, score
        if best and best_score > 0:
            model.append({
                "construct": c,
                "scale_name": best["scale_name"],
                "citation": best["citation"],
                "source_link": best["source_link"],
                "n_items": best["n_items"],
                "example_item": best["example_item"],
                "provenance": "validated",
                "usage_note": ("Use the original validated items; any wording "
                               "changes make it an adaptation requiring re-validation."),
            })
        else:
            model.append({
                "construct": c,
                "scale_name": f"Proposed {c} items (study-specific)",
                "citation": None,
                "source_link": None,
                "n_items": 4,
                "example_items": [
                    f"Item 1 (proposed): Please rate your {c}.",
                    f"Item 2 (proposed): To what extent do you experience {c}?",
                    f"Item 3 (proposed): How would you describe your {c}?",
                    f"Item 4 (proposed, reverse-scored): I rarely experience {c}.",
                ],
                "provenance": "proposed_unvalidated",
                "usage_note": ("NEWLY WRITTEN — NOT validated. Pilot with n≥30, "
                               "check reliability (Cronbach's α), and run a "
                               "confirmatory factor analysis before main data "
                               "collection. Do not report as a validated scale."),
            })
    return model


# ---------------------------------------------------------------------------
# 3. Sampling & ethics plan
# ---------------------------------------------------------------------------

def generate_sampling_and_ethics_plan(target_population: str,
                                      sample_size_needed: int | None = None,
                                      design_type: str = "survey") -> Dict:
    """Recruitment, power/precision, confounders, ethics checklist."""
    recruitment = [
        "Define the sampling frame explicitly (who is in / out) before recruiting.",
        f"Primary channels for '{target_population}': university participant pools, "
        "Prolific/CloudResearch panels with screening, relevant professional or "
        "community organisations, and snowball referral with diversity quotas.",
        "Pre-register inclusion/exclusion criteria; log every exclusion with a reason.",
        "Plan for 15–20% attrition in longitudinal designs; oversample accordingly.",
    ]
    if sample_size_needed:
        power = (f"Target N = {sample_size_needed} (researcher-specified). Verify "
                 f"with an a-priori power analysis (G*Power or equivalent) for the "
                 f"primary test at α = .05, power = .80 before collecting data.")
    else:
        power = ("No target N supplied — run an a-priori power analysis before "
                 "collecting data. Rules of thumb (two-group comparison, α=.05, "
                 "power=.80): small effect d=0.2 → n≈394/group; medium d=0.5 → "
                 "n≈64/group; large d=0.8 → n≈26/group. Confirm with G*Power.")

    confounders = {
        "survey": ["common-method bias (separate predictors/outcomes in time or source where possible)",
                   "social desirability", "non-response bias"],
        "experimental": ["randomization checks", "demand characteristics",
                         "experimenter expectancy", "practice/fatigue effects"],
        "observational": ["selection bias", "unmeasured confounding",
                          "temporal ambiguity — association is not causation"],
        "mixed_methods": ["integration validity across strands",
                          "sampling alignment between strands"],
    }.get(design_type, ["unmeasured confounding"])

    ethics_checklist = [
        {"item": "IRB / ethics board approval",
         "detail": "Obtain written approval BEFORE any participant contact.",
         "status": "pending"},
        {"item": "Informed consent",
         "detail": "Purpose, procedures, risks, benefits, right to withdraw at any time without penalty.",
         "status": "pending"},
        {"item": "Data privacy & anonymization",
         "detail": "No directly identifying data stored with responses; encryption at rest; retention period defined.",
         "status": "pending"},
        {"item": "Vulnerable populations",
         "detail": "Extra safeguards if minors, patients, or employees are involved.",
         "status": "pending"},
        {"item": "Debriefing",
         "detail": "Full debrief for any deception or demanding procedure.",
         "status": "pending"},
        {"item": "HUMAN APPROVAL CHECKPOINT",
         "detail": ("The researcher must explicitly approve this protocol before "
                    "any data collection or analysis begins. The system cannot "
                    "self-authorize this transition."),
         "status": "pending"},
    ]
    return {
        "target_population": target_population,
        "recruitment": recruitment,
        "power_and_precision": power,
        "confounder_controls": confounders,
        "ethics_checklist": ethics_checklist,
        "approval_checkpoint": {
            "required": True,
            "gate": "human_approval_before_data_collection",
            "status": "pending",
            "message": ("Protocol is a draft until the researcher approves it. "
                        "No data collection or analysis may start before approval."),
        },
        "method": METHOD,
    }


# ---------------------------------------------------------------------------
# 1. Protocol proposal
# ---------------------------------------------------------------------------

_DESIGN_TEMPLATES = {
    "survey": {
        "label": "Cross-sectional survey",
        "procedure": ("Online questionnaire administered once per participant; "
                      "validated scales plus demographics; attention checks included."),
        "analysis": ("Descriptive statistics, reliability analysis (α, ω), "
                     "confirmatory factor analysis, then regression / SEM testing "
                     "the hypothesized paths. Report effect sizes with 95% CIs."),
    },
    "experimental": {
        "label": "Randomized experiment",
        "procedure": ("Participants randomized to conditions manipulating the key "
                      "independent variable; manipulation check; validated outcome "
                      "measures; debriefing."),
        "analysis": ("Randomization checks, ANOVA / regression with condition "
                     "contrasts, mediation analysis for the mechanism, "
                     "pre-registered exclusion rules."),
    },
    "observational": {
        "label": "Observational / longitudinal study",
        "procedure": ("Repeated measurements over ≥3 waves; validated scales at "
                      "each wave; attrition tracking and missing-data plan."),
        "analysis": ("Multilevel / latent growth models; cross-lagged panel models "
                     "for directionality; sensitivity analyses for attrition."),
    },
    "mixed_methods": {
        "label": "Mixed-methods (explanatory sequential)",
        "procedure": ("Phase 1: survey (n per power analysis). Phase 2: purposive "
                      "interviews (n≈15–25) sampled from survey respondents to "
                      "explain quantitative patterns."),
        "analysis": ("Quantitative strand as per survey template; qualitative "
                     "strand via thematic analysis; joint display integration."),
    },
}


def propose_research_protocol(approved_gap_id: str, design_type: str,
                              db: Session, task_id: str | None = None,
                              target_population: str | None = None,
                              sample_size_needed: int | None = None,
                              constructs: List[str] | None = None) -> Dict:
    """Build a complete reviewable protocol for a researcher-approved gap."""
    if design_type not in DESIGN_TYPES:
        raise ValueError(f"Unknown design_type: {design_type!r}. "
                         f"Choose from {sorted(DESIGN_TYPES)}.")
    gap = db.get(GapBrief, approved_gap_id)
    if gap is None:
        raise ValueError(f"Unknown gap_id: {approved_gap_id}")
    if gap.researcher_decision != "approved":
        raise ValueError(
            "Gap must be researcher-approved before protocol design "
            f"(current decision: {gap.researcher_decision!r})."
        )

    template = _DESIGN_TEMPLATES[design_type]
    gap_text = gap.gap_statement
    unknown = gap.unknown_summary or gap_text

    research_questions = [
        f"RQ1: {unknown.rstrip('.')}?",
        f"RQ2: Through which mechanism does the effect in RQ1 operate?",
        "RQ3: For whom / under what conditions is the effect strongest?",
    ]
    hypotheses = [
        "H1: The focal predictor is positively associated with the outcome "
        "(directional, two-tailed test).",
        "H2: The effect in H1 is mediated by the theorized mechanism.",
        "H3: The effect is moderated by the specified boundary condition.",
    ]

    construct_list = constructs or extract_constructs(
        gap_text, unknown,
        [e.get("title", "") for e in _loads(gap.supporting_evidence)],
        top_n=4) or ["focal construct"]
    measurement = construct_measurement_model(construct_list)
    sampling = generate_sampling_and_ethics_plan(
        target_population or "to be defined with the researcher",
        sample_size_needed, design_type)

    sections = {
        "background": (f"This protocol addresses the approved gap: {gap_text} "
                       f"Supporting evidence: "
                       + "; ".join(
                           f"{e.get('title', '')[:60]} ({e.get('source_link')})"
                           for e in _loads(gap.supporting_evidence)[:3])),
        "design_overview": f"{template['label']} targeting the gap above.",
        "population_and_sampling": sampling["recruitment"],
        "measures": [f"{m['construct']}: {m['scale_name']} [{m['provenance']}]"
                     for m in measurement],
        "procedure": template["procedure"],
        "analysis_plan": template["analysis"],
        "timeline": ("Weeks 1–2: ethics approval & pre-registration. Weeks 3–6: "
                     "piloting & instrument finalization. Weeks 7–12: main data "
                     "collection. Weeks 13–16: analysis & reporting."),
        "limitations": ("Single-study evidence; self-report biases where applicable; "
                        "generalizability bounded by the sampled population."),
    }

    row = ResearchProtocol(
        project_id=gap.project_id,
        task_id=task_id or gap.task_id,
        gap_id=gap.id,
        design_type=design_type,
        title=f"{template['label']}: {gap_text[:90]}",
        research_questions=json.dumps(research_questions),
        hypotheses=json.dumps(hypotheses),
        sections=json.dumps(sections),
        measurement_model=json.dumps(measurement),
        ethics_checklist=json.dumps(sampling["ethics_checklist"]),
        approval_checkpoint=json.dumps(sampling["approval_checkpoint"]),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    log.info("Protocol %s drafted for gap %s (%s)", row.id[:8], gap.id[:8],
             design_type)
    return get_protocol_dict(row.id, db)


def _loads(s: str | None) -> list:
    try:
        return json.loads(s or "[]")
    except (json.JSONDecodeError, TypeError):
        return []


# ---------------------------------------------------------------------------
# Evidence-grounded construct extraction
# ---------------------------------------------------------------------------
# Canonical research constructs mapped to the words/phrases that signal them
# in a gap statement or supporting evidence. Derived from the validated scale
# library's keyword sets plus common constructs from the literature.
# Constructs with no validated scale on file still surface here — the
# measurement model marks them "proposed / unvalidated", never "validated".
CONSTRUCT_VOCABULARY: Dict[str, List[str]] = {
    "trust": ["trust", "trusting", "trustworthiness", "distrust", "reliance",
              "trust in automation"],
    "technology acceptance": ["technology acceptance", "acceptance",
                              "adoption", "adopt", "uptake"],
    "behavioral intention": ["behavioral intention", "intention to use",
                             "intentions", "intention", "willingness"],
    "perceived usefulness": ["perceived usefulness", "usefulness"],
    "perceived ease of use": ["perceived ease of use", "ease of use"],
    "perceived risk": ["perceived risk", "risk perception", "risks", "risk",
                       "risky", "safety concern"],
    "performance expectancy": ["performance expectancy"],
    "effort expectancy": ["effort expectancy"],
    "social influence": ["social influence", "subjective norm",
                         "social norms"],
    "satisfaction": ["satisfaction", "system usability", "usability"],
    "workload": ["workload", "cognitive load", "mental demand", "task load"],
    "affect": ["affect", "emotion", "emotions", "mood", "wellbeing",
               "well-being"],
    "personality": ["personality", "big five", "openness",
                    "conscientiousness", "extraversion", "agreeableness",
                    "neuroticism"],
}


def extract_constructs(gap_text: str | None,
                       unknown_summary: str | None = None,
                       evidence_titles: List[str] | None = None,
                       top_n: int = 4) -> List[str]:
    """Derive research constructs from the approved gap + supporting evidence.

    Mines the gap statement, the unknown summary, and the titles of the
    supporting evidence for vocabulary triggers, ranking constructs by hit
    count. Only canonical constructs from CONSTRUCT_VOCABULARY can surface —
    raw word-frequency fragments (the old _keywords fallback) are never
    returned. Returns [] when nothing matches; the caller then falls back to
    the honest placeholder "focal construct".
    """
    corpus = " ".join(
        [gap_text or "", unknown_summary or ""] + list(evidence_titles or [])
    ).lower()
    if not corpus.strip():
        return []
    words = set(re.findall(r"[a-z][a-z\-]+", corpus))

    scored: List[tuple] = []
    for construct, triggers in CONSTRUCT_VOCABULARY.items():
        hits = 0
        for trig in triggers:
            trig = trig.lower()
            if " " in trig or "-" in trig:
                if trig in corpus:
                    hits += 1
            elif trig in words:
                hits += 1
        if hits:
            scored.append((hits, construct))
    scored.sort(key=lambda s: (-s[0], s[1]))
    return [c for _, c in scored[:top_n]]


def get_protocol(db: Session, protocol_id: str) -> ResearchProtocol | None:
    return db.get(ResearchProtocol, protocol_id)


def list_protocols(db: Session, project_id: str | None = None,
                   task_id: str | None = None,
                   limit: int = 100) -> List[ResearchProtocol]:
    stmt = select(ResearchProtocol).order_by(ResearchProtocol.created_at.desc())
    if project_id:
        stmt = stmt.where(ResearchProtocol.project_id == project_id)
    if task_id:
        stmt = stmt.where(ResearchProtocol.task_id == task_id)
    return list(db.scalars(stmt.limit(limit)).all())


def get_protocol_dict(protocol_id: str, db: Session) -> Dict:
    p = get_protocol(db, protocol_id)
    if p is None:
        raise ValueError(f"Unknown protocol_id: {protocol_id}")
    return {
        "id": p.id,
        "project_id": p.project_id,
        "task_id": p.task_id,
        "gap_id": p.gap_id,
        "design_type": p.design_type,
        "title": p.title,
        "research_questions": p.rq_list,
        "hypotheses": p.hypothesis_list,
        "sections": p.sections_dict,
        "measurement_model": p.measurement_list,
        "ethics_checklist": p.ethics_list,
        "approval_checkpoint": p.checkpoint_dict,
        "approval_status": p.approval_status,
        "researcher_note": p.researcher_note,
        "created_at": p.created_at,
    }


def review_protocol(db: Session, protocol_id: str, approved: bool,
                    note: str = "") -> ResearchProtocol:
    """Researcher approve/reject a protocol (gates data collection)."""
    p = get_protocol(db, protocol_id)
    if p is None:
        raise ValueError(f"Unknown protocol_id: {protocol_id}")
    p.approval_status = "approved" if approved else "rejected"
    p.researcher_note = note
    if approved:
        cp = p.checkpoint_dict
        cp["status"] = "approved"
        cp["message"] = ("Approved by the researcher. Data collection may "
                         "proceed under the approved ethics plan.")
        p.approval_checkpoint = json.dumps(cp)
    db.commit()
    db.refresh(p)
    return p


def regenerate_protocol(db: Session, protocol_id: str,
                        constructs: List[str] | None = None,
                        design_type: str | None = None,
                        target_population: str | None = None,
                        sample_size_needed: int | None = None,
                        note: str = "") -> Dict:
    """Rebuild a weak or rejected protocol without restarting the pipeline.

    Creates a NEW protocol row for the same gap (same task/project) and marks
    the old row 'superseded' — never deleted, so the full revision history
    stays in the provenance log. The new protocol starts as 'pending' and
    goes through the normal researcher approval gate.

    Args:
        constructs: explicit construct list; None re-runs the
            evidence-grounded extraction (picks up the vocabulary fix).
    """
    old = get_protocol(db, protocol_id)
    if old is None:
        raise ValueError(f"Unknown protocol_id: {protocol_id}")
    gap = db.get(GapBrief, old.gap_id)
    if gap is None:
        raise ValueError(f"Original gap {old.gap_id[:8]} no longer exists.")
    if gap.researcher_decision != "approved":
        raise ValueError(
            "Cannot regenerate: the original gap is no longer "
            f"researcher-approved (decision={gap.researcher_decision!r}).")

    new = propose_research_protocol(
        old.gap_id, design_type or old.design_type, db,
        task_id=old.task_id,
        target_population=target_population,
        sample_size_needed=sample_size_needed,
        constructs=constructs,
    )
    old.approval_status = "superseded"
    link_note = f"Superseded by protocol {new['id'][:8]}"
    if note:
        link_note += f": {note}"
    old.researcher_note = ((old.researcher_note or "") + " | " + link_note
                           ).strip(" |")
    db.commit()
    db.refresh(old)
    log.info("Protocol %s regenerated as %s (old marked superseded)",
             old.id[:8], new["id"][:8])
    return new
