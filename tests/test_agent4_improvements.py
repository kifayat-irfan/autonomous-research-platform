"""Tests for the post-dry-run improvements (2026-10-13).

1. Evidence-grounded construct extraction (Agent 4) — no more word-frequency
   fragments like 'atheoretical' / 'base' / 'objectives' / 'invoke'.
2. Protocol regeneration — rebuild a weak/rejected protocol without
   restarting the pipeline; old row marked 'superseded', never deleted.
"""
import json
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.agents import agent4_design as agent4
from app.models.schemas import Base, GapBrief, Project


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _make_gap(db, **kw):
    proj = Project(name="test-proj")
    db.add(proj)
    db.flush()
    gap = GapBrief(
        project_id=proj.id,
        gap_statement=kw.get("gap_statement", "Test gap"),
        gap_type=kw.get("gap_type", "theoretical"),
        unknown_summary=kw.get("unknown_summary", ""),
        supporting_evidence=json.dumps(kw.get("evidence", [])),
        researcher_decision="approved",
    )
    db.add(gap)
    db.commit()
    db.refresh(gap)
    return gap


# ---------------------------------------------------------------------------
# 1. extract_constructs
# ---------------------------------------------------------------------------

CLIENT_TITLES = [
    "Understanding electric motorcycle adoption in Pakistan",
    "Why Do Riders Go Electric? Understanding Electric Motorcycle Use "
    "Intention",
    "Perceived risk of crime on driverless public bus and ride-pooling "
    "services in China",
]


def test_extract_constructs_client_case():
    gap = ("Theoretical gap: atheoretical base. Objectives invoke trust and "
           "acceptance of autonomous vehicles among vulnerable road users.")
    out = agent4.extract_constructs(gap, "", CLIENT_TITLES)
    assert out, "should find constructs"
    assert "trust" in out
    assert "technology acceptance" in out or "behavioral intention" in out
    assert "perceived risk" in out
    # the old word-fragment bug must never resurface
    for junk in ("atheoretical", "base", "objectives", "invoke",
                 "theoretical", "evidence"):
        assert junk not in out


def test_extract_constructs_empty_returns_empty():
    assert agent4.extract_constructs("", "", []) == []
    assert agent4.extract_constructs(None, None, None) == []


def test_extract_constructs_junk_only_returns_empty():
    out = agent4.extract_constructs(
        "atheoretical base objectives invoke", "", [])
    assert out == []


def test_extract_constructs_respects_top_n():
    gap = "trust acceptance risk workload satisfaction affect personality"
    out = agent4.extract_constructs(gap, "", [], top_n=2)
    assert len(out) == 2


# ---------------------------------------------------------------------------
# 2. regenerate_protocol
# ---------------------------------------------------------------------------

def test_regenerate_marks_old_superseded_and_creates_new(db):
    gap = _make_gap(
        db,
        gap_statement="Trust and acceptance of autonomous vehicles among "
                      "vulnerable road users.",
        evidence=[{"title": t} for t in CLIENT_TITLES],
    )
    old = agent4.propose_research_protocol(gap.id, "mixed_methods", db)
    old_id = old["id"]
    agent4.review_protocol(db, old_id, approved=False,
                           note="constructs were weak")

    new = agent4.regenerate_protocol(
        db, old_id,
        constructs=["trust", "perceived risk", "acceptance"],
        note="retry with explicit constructs",
    )
    assert new["id"] != old_id
    assert new["approval_status"] == "pending"
    assert new["gap_id"] == gap.id

    old_row = agent4.get_protocol(db, old_id)
    assert old_row.approval_status == "superseded"
    assert new["id"][:8] in (old_row.researcher_note or "")

    constructs = [m["construct"] for m in new["measurement_model"]]
    assert constructs == ["trust", "perceived risk", "acceptance"]


def test_regenerate_without_constructs_uses_evidence_extraction(db):
    gap = _make_gap(
        db,
        gap_statement="Trust and acceptance of autonomous vehicles among "
                      "vulnerable road users.",
        evidence=[{"title": t} for t in CLIENT_TITLES],
    )
    old = agent4.propose_research_protocol(
        gap.id, "mixed_methods", db, constructs=["atheoretical", "base"])
    new = agent4.regenerate_protocol(db, old["id"])
    constructs = [m["construct"] for m in new["measurement_model"]]
    assert "atheoretical" not in constructs
    assert "base" not in constructs
    assert "trust" in constructs


def test_regenerate_unknown_protocol_raises(db):
    with pytest.raises(ValueError, match="Unknown protocol_id"):
        agent4.regenerate_protocol(db, "no-such-id")
