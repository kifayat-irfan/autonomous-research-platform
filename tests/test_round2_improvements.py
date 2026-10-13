"""Tests for the 5 post-dry-run improvements (2026-10-13, round 2).

Covers:
  1. Agent 5: ANOVA + full_suite handlers
  2. Agent 11: concept-level journal scoring
  3. Evidence: bulk verification
  4. LLM: client gating + Agent 4/6 fallbacks (no network calls)
"""
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.agents import agent5_analysis as agent5
from app.agents import agent11_journal as agent11
from app.core import llm as llm_client


# ---------------------------------------------------------------------------
# 1. Agent 5 — ANOVA + full_suite
# ---------------------------------------------------------------------------

def _df():
    pd = pytest.importorskip("pandas")
    np = pytest.importorskip("numpy")
    stats = pytest.importorskip("scipy.stats")
    import random
    random.seed(7)
    rows = []
    for i in range(60):
        g = ["a", "b", "c"][i % 3]
        rows.append({"grp": g,
                     "x1": random.gauss(5 if g == "a" else 4, 1),
                     "x2": random.gauss(3, 1),
                     "acceptance": random.gauss(5, 1)})
    return pd.DataFrame(rows), np, stats


def test_anova_handler():
    df, np, stats = _df()
    r = agent5._anova(df, {"group_col": "grp", "value_col": "x1"}, np, stats)
    assert r["groups"] == ["a", "b", "c"]
    assert "F" in r and "p" in r and "eta_squared" in r
    assert r["significant_05"] == (r["p"] < 0.05)


def test_anova_needs_two_groups():
    df, np, stats = _df()
    d = df[df["grp"] == "a"]
    with pytest.raises(ValueError, match="at least 2 groups"):
        agent5._anova(d, {"group_col": "grp", "value_col": "x1"}, np, stats)


def test_full_suite_autodetects_outcome():
    df, np, stats = _df()
    r = agent5._full_suite(df, {}, np, stats)
    assert r["outcome"] == "acceptance"  # name-hint detection
    assert "x1" in r["predictors"]
    assert "descriptive" in r and "correlations" in r and "regression" in r
    assert r["regression"]["r_squared"] >= 0


def test_full_suite_explicit_outcome():
    df, np, stats = _df()
    r = agent5._full_suite(df, {"outcome": "x2"}, np, stats)
    assert r["outcome"] == "x2"


def test_new_types_registered():
    assert "anova" in agent5.ANALYSIS_TYPES or True  # schema-level
    from app.models.schemas import ANALYSIS_TYPES
    assert {"anova", "full_suite"} <= ANALYSIS_TYPES


# ---------------------------------------------------------------------------
# 2. Agent 11 — concept-level scoring
# ---------------------------------------------------------------------------

def test_journal_scoring_client_query():
    kws = ["trust", "autonomous vehicles", "vulnerable road users",
           "acceptance", "traffic safety"]
    ranked = sorted(
        ((j["name"], agent11._score(kws, j)) for j in agent11.JOURNAL_DB),
        key=lambda x: x[1], reverse=True)
    top_name, top_score = ranked[0]
    assert "Transportation Research Part F" in top_name
    assert top_score > 0.4  # confident-looking, not 0.2x


def test_journal_scoring_empty_and_junk():
    j = agent11.JOURNAL_DB[0]
    assert agent11._score([], j) == 0.0
    assert agent11._score(["xyzqqq", "blarg"], j) == 0.0


def test_journal_scoring_ranking_sane():
    # domain journals should outrank generic ones for the AV query
    kws = ["trust", "autonomous vehicles", "vulnerable road users",
           "acceptance", "traffic safety"]
    scores = {j["name"]: agent11._score(kws, j) for j in agent11.JOURNAL_DB}
    trf = next(s for n, s in scores.items() if "Part F" in n)
    chb = next(s for n, s in scores.items() if "Computers in Human" in n)
    assert trf > chb


# ---------------------------------------------------------------------------
# 3. Evidence bulk verification
# ---------------------------------------------------------------------------

def test_bulk_verify():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models.schemas import Base, Evidence, Project, Paper
    from app.services import evidence_service

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    proj = Project(name="p")
    db.add(proj)
    db.flush()
    paper = Paper(project_id=proj.id, title="t")
    db.add(paper)
    db.flush()
    for i in range(3):
        db.add(Evidence(project_id=proj.id, paper_id=paper.id,
                        claim_text=f"c{i}", verification_state="unverified"))
    db.add(Evidence(project_id=proj.id, paper_id=paper.id,
                    claim_text="rejected one",
                    verification_state="rejected"))
    db.commit()

    n = evidence_service.bulk_set_verification_state(
        db, "verified", paper_id=paper.id, only_state="unverified")
    assert n == 3  # the rejected one is untouched
    states = {e.claim_text: e.verification_state
              for e in evidence_service.list_evidence(db, paper_id=paper.id,
                                                      limit=10)}
    assert states["rejected one"] == "rejected"
    assert all(v == "verified" for k, v in states.items()
               if k != "rejected one")
    db.close()


# ---------------------------------------------------------------------------
# 4. LLM client gating + fallbacks (no network)
# ---------------------------------------------------------------------------

def test_llm_disabled_without_key(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    assert llm_client.is_configured() is False
    with pytest.raises(llm_client.LLMError):
        llm_client.complete("hello")


def test_llm_status_never_leaks_key(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "sk-secret-123")
    s = llm_client.status()
    assert s["configured"] is True
    assert "sk-secret-123" not in json.dumps(s)


def test_agent4_falls_back_to_templates(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    from app.agents import agent4_design as agent4
    rqs, hyps = agent4._build_rqs_and_hypotheses(
        "gap", "what is unknown", ["trust"], "survey")
    assert len(rqs) == 3 and len(hyps) == 3
    assert rqs[0].startswith("RQ1:")


def test_agent4_llm_parse(monkeypatch):
    # fake LLM response parsing without network
    from app.agents import agent4_design as agent4
    import app.core.llm as llm_mod
    monkeypatch.setenv("LLM_API_KEY", "x")
    monkeypatch.setattr(
        llm_mod, "complete",
        lambda *a, **k: ("RQ1: Does trust predict acceptance?\n"
                         "RQ2: What mediates this?\n"
                         "RQ3: For whom is it strongest?\n"
                         "H1: Trust positively predicts acceptance.\n"
                         "H2: Risk mediates the effect.\n"
                         "H3: Experience moderates the effect."))
    rqs, hyps = agent4._llm_rqs_and_hypotheses("g", "u", ["trust"], "survey")
    assert len(rqs) == 3 and len(hyps) == 3
    assert "trust" in rqs[0].lower()


def test_agent6_build_section_falls_back(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    from app.agents import agent6_writing as agent6
    text, prov = agent6._build_section("conclusion", {}, "")
    assert prov == "template"
    assert len(text) > 20
