"""Tests for the Round 4 protocol-RQ fix (2026-11-15, live failures).

1. v1 protocol: LLM RQs drifted off-topic (perceived organizational support
   -> employee engagement) for an AV-trust project — the prompt carried no
   domain anchor and the gap text yielded no constructs.
2. v2 protocol: LLM call failed with "Unexpected LLM response shape:
   'content'" (SenseNova HTTP 200 with a content-less message) and the
   template fallback — which never received the constructs — produced
   placeholder RQs naming no variable ("focal predictor").
3. Background section rendered supporting evidence as "Title (None)" when
   no source_link exists on the evidence item.
"""
import json
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import requests  # noqa: E402

from app.agents import agent4_design as agent4  # noqa: E402
from app.core import llm as llm_mod  # noqa: E402
from app.models.schemas import Base, GapBrief, Project, Task  # noqa: E402

TOPIC = "Trust and acceptance of autonomous vehicles among vulnerable road users"
GAP_TEXT = ("Atheoretical evidence base: 45 of 45 study objectives invoke no "
            "named theory or framework, so there is no articulated mechanism "
            "explaining why the effects occur.")
OFF_TOPIC_LLM = (
    "RQ1: Does perceived organizational support predict employee engagement "
    "in the workplace?\n"
    "RQ2: What is the mechanism through which perceived organizational "
    "support influences employee engagement?\n"
    "RQ3: Does job autonomy moderate the relationship between perceived "
    "organizational support and employee engagement?\n"
    "H1: Perceived organizational support positively predicts employee "
    "engagement.\n"
    "H2: Psychological contract fulfillment mediates the relationship "
    "between perceived organizational support and employee engagement.\n"
    "H3: Job autonomy strengthens the positive relationship between "
    "perceived organizational support and employee engagement."
)
ON_TOPIC_LLM = (
    "RQ1: Does trust in autonomous vehicles predict acceptance intention "
    "among vulnerable road users?\n"
    "RQ2: Through which mechanism does trust influence acceptance "
    "intention?\n"
    "RQ3: For whom is the trust-acceptance relationship strongest?\n"
    "H1: Trust positively predicts acceptance intention.\n"
    "H2: Perceived risk mediates the effect of trust on acceptance "
    "intention.\n"
    "H3: Technology acceptance moderates the relationship between trust "
    "and acceptance intention."
)


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _make_task_and_gap(db, evidence=None):
    proj = Project(name="test-proj")
    db.add(proj)
    db.flush()
    task = Task(query=TOPIC, project_id=proj.id)
    db.add(task)
    db.flush()
    gap = GapBrief(
        project_id=proj.id,
        task_id=task.task_id,
        gap_statement=GAP_TEXT,
        gap_type="theoretical",
        unknown_summary="What mechanism explains the effects?",
        supporting_evidence=json.dumps(evidence or []),
        researcher_decision="approved",
    )
    db.add(gap)
    db.commit()
    db.refresh(gap)
    return task, gap


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


def _content_resp(text):
    return _Resp({"choices": [{"message": {"content": text}}]})


def _no_content_resp():
    return _Resp({"choices": [{"message": {}}]})


# ---------------------------------------------------------------------------
# 1. llm.complete: content-less message -> retry once, then succeed / fail
# ---------------------------------------------------------------------------

def test_llm_complete_retries_on_missing_content(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "x")
    calls = []

    def fake_post(*a, **k):
        calls.append(1)
        return _no_content_resp() if len(calls) == 1 else _content_resp("hello")

    monkeypatch.setattr(requests, "post", fake_post)
    assert llm_mod.complete("hi") == "hello"
    assert len(calls) == 2


def test_llm_complete_missing_content_twice_raises(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "x")
    calls = []

    def fake_post(*a, **k):
        calls.append(1)
        return _no_content_resp()

    monkeypatch.setattr(requests, "post", fake_post)
    with pytest.raises(llm_mod.LLMError):
        llm_mod.complete("hi")
    assert len(calls) == 2


def test_llm_complete_empty_choices_raises(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "x")
    monkeypatch.setattr(requests, "post",
                        lambda *a, **k: _Resp({"choices": []}))
    with pytest.raises(llm_mod.LLMError):
        llm_mod.complete("hi")


# ---------------------------------------------------------------------------
# 2. Template fallback now names the real constructs
# ---------------------------------------------------------------------------

CONSTRUCTS_4 = ["trust", "perceived risk", "technology acceptance",
                "behavioral intention"]


def test_template_names_constructs():
    rqs, hyps = agent4._template_rqs_and_hypotheses("unknown", CONSTRUCTS_4)
    joined = " ".join(rqs + hyps).lower()
    assert "trust" in joined
    assert "behavioral intention" in joined
    assert "perceived risk" in joined
    assert "technology acceptance" in joined
    assert "focal predictor" not in joined


def test_template_single_construct():
    rqs, hyps = agent4._template_rqs_and_hypotheses("unknown", ["trust"])
    assert "trust" in rqs[0].lower()
    assert "trust" in hyps[0].lower()


def test_template_no_constructs_keeps_generic():
    rqs, hyps = agent4._template_rqs_and_hypotheses("unknown")
    assert "focal predictor" in " ".join(hyps)


# ---------------------------------------------------------------------------
# 3. LLM RQ validation: off-topic output is refused, topic anchors prompt
# ---------------------------------------------------------------------------

def test_llm_rqs_rejects_off_topic_output(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "x")
    monkeypatch.setattr(llm_mod, "complete", lambda *a, **k: OFF_TOPIC_LLM)
    rqs, hyps = agent4._build_rqs_and_hypotheses(
        GAP_TEXT, "unknown", ["trust", "perceived risk"],
        "mixed_methods", topic=TOPIC)
    joined = " ".join(rqs + hyps).lower()
    assert "employee engagement" not in joined
    assert "organizational support" not in joined
    assert "trust" in joined


def test_llm_rqs_prompt_contains_topic(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "x")
    seen = {}

    def fake_complete(prompt, **k):
        seen["prompt"] = prompt
        return ON_TOPIC_LLM

    monkeypatch.setattr(llm_mod, "complete", fake_complete)
    rqs, hyps = agent4._llm_rqs_and_hypotheses(
        GAP_TEXT, "unknown", ["trust", "perceived risk"],
        "mixed_methods", topic=TOPIC)
    assert TOPIC in seen["prompt"]
    assert "trust" in rqs[0].lower()


# ---------------------------------------------------------------------------
# 4. Construct mining includes the research question
# ---------------------------------------------------------------------------

def test_extract_constructs_mines_research_question():
    out = agent4.extract_constructs(
        GAP_TEXT, "What mechanism explains the effects?",
        ["Understanding motorcycle crash involvement in Vietnam"],
        extra_text=TOPIC)
    assert "trust" in out
    assert "technology acceptance" in out
    for junk in ("atheoretical", "objectives", "invoke", "framework"):
        assert junk not in out


# ---------------------------------------------------------------------------
# 6. .env is loaded at import so a saved key survives backend restarts
# ---------------------------------------------------------------------------

def test_load_dotenv_sets_missing_key(monkeypatch, tmp_path):
    import os

    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\nLLM_API_KEY=sk-test-123\nLLM_MODEL=test-model\n")
    llm_mod._load_dotenv(env_file)
    assert os.environ["LLM_API_KEY"] == "sk-test-123"
    assert os.environ["LLM_MODEL"] == "test-model"


def test_load_dotenv_never_overwrites(monkeypatch, tmp_path):
    import os

    monkeypatch.setenv("LLM_API_KEY", "keep-me")
    env_file = tmp_path / ".env"
    env_file.write_text("LLM_API_KEY=sk-other\n")
    llm_mod._load_dotenv(env_file)
    assert os.environ["LLM_API_KEY"] == "keep-me"


def test_load_dotenv_missing_file_is_noop(tmp_path):
    llm_mod._load_dotenv(tmp_path / ".env")  # must not raise


# ---------------------------------------------------------------------------
# 5. End-to-end protocol: topic-anchored RQs, no "(None)" citations
# ---------------------------------------------------------------------------

def test_protocol_background_no_none_citation(monkeypatch, db):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    _, gap = _make_task_and_gap(db, evidence=[
        {"title": "Understanding motorcycle crash involvement in Vietnam"},
        {"title": "Risky riding behaviors among motorcyclists in Pakistan"},
    ])
    out = agent4.propose_research_protocol(
        gap.id, "mixed_methods", db, task_id=gap.task_id)
    assert "(None)" not in out["sections"]["background"]
    rqs = out["research_questions"]
    assert any("trust" in q.lower() for q in rqs)


def test_protocol_llm_path_uses_task_query(monkeypatch, db):
    monkeypatch.setenv("LLM_API_KEY", "x")
    seen = {}

    def fake_complete(prompt, **k):
        seen["prompt"] = prompt
        return ON_TOPIC_LLM

    monkeypatch.setattr(llm_mod, "complete", fake_complete)
    _, gap = _make_task_and_gap(db, evidence=[
        {"title": "Understanding motorcycle crash involvement in Vietnam"},
    ])
    out = agent4.propose_research_protocol(
        gap.id, "mixed_methods", db, task_id=gap.task_id)
    assert TOPIC in seen["prompt"]
    rqs = out["research_questions"]
    assert "trust" in rqs[0].lower()
    assert "employee engagement" not in " ".join(rqs).lower()
