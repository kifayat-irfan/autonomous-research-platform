"""Tests for the Round 3 limitation fixes (2026-11-01).

Covers:
  1. Agent 3: LLM gap sharpening (fallback, parsing, rule-1 safety)
  2. Agent 5: auto evidence-coding dataset builder
  3. Agent 11: journal DB transport expansion (48 journals)
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.agents import agent3_gap_discovery as agent3
from app.agents import agent5_analysis as agent5
from app.agents import agent11_journal as agent11


# ---------------------------------------------------------------------------
# 1. Agent 3 — LLM sharpening
# ---------------------------------------------------------------------------

def _candidate():
    return {
        "gap_statement": "Thin empirical coverage of 'trust': only one study addresses it.",
        "gap_type": "empirical",
        "known_summary": "One study on trust.",
        "unknown_summary": "Whether the trust finding replicates.",
        "supporting_evidence": [],
        "importance_score": 6,
        "feasibility_score": 8,
    }


def test_sharpen_disabled_without_key(monkeypatch):
    """No LLM key -> candidates pass through unchanged (no network)."""
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    cands = [_candidate()]
    out = agent3._sharpen_candidates(cands)
    assert out == cands


def test_sharpen_parses_two_lines(monkeypatch):
    """LLM returning STATEMENT + UNKNOWN lines is parsed exactly."""
    monkeypatch.setattr("app.core.llm.complete", lambda *a, **k: (
        "STATEMENT: Thin replicated evidence on trust in AVs among VRUs.\n"
        "UNKNOWN: Whether trust effects replicate across VRU groups."))
    stmt, unk = agent3._llm_sharpen_gap("draft", "known", "unknown",
                                        "empirical")
    assert stmt == "Thin replicated evidence on trust in AVs among VRUs."
    assert unk == "Whether trust effects replicate across VRU groups."


def test_sharpen_bad_llm_output_raises(monkeypatch):
    """Malformed LLM output raises so the caller can fall back."""
    monkeypatch.setattr("app.core.llm.complete",
                        lambda *a, **k: "just some prose, no labels")
    with pytest.raises(ValueError):
        agent3._llm_sharpen_gap("draft", "known", "unknown", "empirical")


def test_sharpen_rule1_keeps_original(monkeypatch):
    """A sharpened population gap without a mechanism keeps the original
    heuristic wording instead of emitting a rule-1 violation."""
    monkeypatch.setattr("app.core.llm.is_configured", lambda: True)
    monkeypatch.setattr("app.core.llm.complete", lambda *a, **k: (
        "STATEMENT: No studies in Vietnam on this topic.\n"
        "UNKNOWN: Whether it holds in Vietnam."))
    cand = _candidate()
    cand["gap_type"] = "population"
    out = agent3._sharpen_candidates([cand])
    assert len(out) == 1
    assert out[0]["gap_statement"] == cand["gap_statement"]


def test_sharpen_success_replaces_wording(monkeypatch):
    """A valid sharpened gap replaces the heuristic wording."""
    monkeypatch.setattr("app.core.llm.is_configured", lambda: True)
    monkeypatch.setattr("app.core.llm.complete", lambda *a, **k: (
        "STATEMENT: Unreplicated trust finding, mechanism untested.\n"
        "UNKNOWN: Whether the mechanism explains trust via risk perception."))
    out = agent3._sharpen_candidates([_candidate()])
    assert out[0]["gap_statement"].startswith("Unreplicated trust finding")


# ---------------------------------------------------------------------------
# 2. Agent 5 — evidence-coding dataset builder
# ---------------------------------------------------------------------------

def _seed_evidence(db):
    from app.models.schemas import Evidence, Paper
    p = Paper(id="p1", project_id="proj1", title="AV trust study",
              journal="Transportation Research Part F",
              source_link="https://example.org")
    db.add(p)
    db.add(Evidence(id="e1", paper_id="p1", project_id="proj1",
                    claim_text="Trust predicts acceptance.",
                    objective="Test trust model", method="survey",
                    sample_size="n=220", page_number=3,
                    verification_state="verified"))
    db.add(Evidence(id="e2", paper_id="p1", project_id="proj1",
                    claim_text="Risk reduces acceptance.",
                    objective="Test risk model", method="survey",
                    sample_size="n=220", page_number=5,
                    verification_state="unverified"))
    db.commit()


def _mem_db():
    """In-memory DB session, same pattern as the round-2 tests."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models.schemas import Base
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_coding_dataset_builds_csv(tmp_path, monkeypatch):
    """Builder writes one row per evidence item with paper metadata."""
    pytest.importorskip("pandas")
    monkeypatch.setattr(agent5, "ANALYSIS_DIR", tmp_path)
    db = _mem_db()
    _seed_evidence(db)
    result = agent5.build_evidence_coding_dataset("proj1", db)
    assert result["n_rows"] == 2
    assert "paper_title" in result["columns"]
    assert "verification_state" in result["columns"]
    assert "NOT respondent-level data" in result["note"]
    import pandas as pd
    df = pd.read_csv(result["path"])
    assert len(df) == 2
    assert set(df["verification_state"]) == {"verified", "unverified"}
    db.close()


def test_coding_dataset_empty_project_raises():
    """No evidence -> clear error, not an empty CSV."""
    db = _mem_db()
    with pytest.raises(ValueError, match="No extracted evidence"):
        agent5.build_evidence_coding_dataset("empty-proj", db)
    db.close()


# ---------------------------------------------------------------------------
# 3. Agent 11 — journal DB expansion
# ---------------------------------------------------------------------------

def test_journal_db_has_48_entries():
    assert len(agent11.JOURNAL_DB) == 48


def test_new_transport_journals_present():
    names = {j["name"] for j in agent11.JOURNAL_DB}
    for expected in [
        "Transportation Research Part E: Logistics and Transportation Review",
        "Journal of Transport and Health",
        "Analytic Methods in Accident Research",
        "Transportation Research Record",
        "IATSS Research",
        "Journal of Advanced Transportation",
        "International Journal of Sustainable Transportation",
        "Case Studies on Transport Policy",
        "Transportmetrica A: Transport Science",
        "Journal of Choice Modelling",
    ]:
        assert expected in names, f"missing: {expected}"


def test_new_journals_have_scope_and_links():
    names = {
        "Transportation Research Part E: Logistics and Transportation Review",
        "Journal of Transport and Health",
        "Analytic Methods in Accident Research",
        "Transportation Research Record",
        "IATSS Research",
        "Journal of Advanced Transportation",
        "International Journal of Sustainable Transportation",
        "Case Studies on Transport Policy",
        "Transportmetrica A: Transport Science",
        "Journal of Choice Modelling",
    }
    for j in agent11.JOURNAL_DB:
        if j["name"] in names:
            assert j["scope_keywords"], j["name"]
            assert j["source_link"].startswith("https://"), j["name"]


def test_client_query_still_ranks_tr_part_f_top():
    """Expansion must not break the client's top match."""
    db = _mem_db()
    results = agent11.match_journals(
        db, "proj1",
        ["trust", "autonomous vehicles", "vulnerable road users",
         "acceptance", "traffic psychology"])
    assert "Part F" in results[0]["journal_name"]
    db.close()
