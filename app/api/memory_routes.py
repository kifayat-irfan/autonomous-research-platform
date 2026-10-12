"""Agent 8 API: projects, decisions, workspace state, activity feed."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.agents import agent8_memory as agent8
from app.core.database import get_db
from app.models.schemas import (
    ActivityCreate,
    ActivityOut,
    DecisionCreate,
    DecisionOut,
    ProjectCreate,
    ProjectOut,
)

router = APIRouter(prefix="/memory", tags=["memory"])


@router.post("/projects", response_model=ProjectOut)
def create_project(payload: ProjectCreate, db: Session = Depends(get_db)):
    project = agent8.create_project(
        db, payload.name, payload.description, payload.keywords
    )
    return ProjectOut.model_validate(project).model_dump()


@router.get("/projects", response_model=list[ProjectOut])
def list_projects(db: Session = Depends(get_db)):
    return [ProjectOut.model_validate(p).model_dump()
            for p in agent8.list_projects(db)]


@router.get("/projects/{project_id}/state", response_model=dict)
def workspace_state(project_id: str, db: Session = Depends(get_db)):
    """Live research workspace: summary, paper bank, tasks, evidence, feed."""
    try:
        return agent8.get_project_workspace_state(db, project_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/projects/{project_id}/decisions", response_model=DecisionOut)
def save_decision(project_id: str, payload: DecisionCreate,
                  db: Session = Depends(get_db)):
    try:
        d = agent8.save_project_decision(
            db, project_id, payload.decision_type, payload.details,
            payload.user_approved, task_id=payload.task_id,
            evidence_link=payload.evidence_link,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return DecisionOut.model_validate(d).model_dump()


@router.get("/projects/{project_id}/decisions/latest", response_model=dict)
def latest_decision(project_id: str, decision_type: str,
                    db: Session = Depends(get_db)):
    """Latest decision of a type (e.g. persisted style_profile)."""
    d = agent8.get_latest_decision(db, project_id, decision_type)
    return {"decision": d}


@router.post("/projects/{project_id}/activity", response_model=ActivityOut)
def log_activity(project_id: str, payload: ActivityCreate,
                 db: Session = Depends(get_db)):
    try:
        a = agent8.log_activity_feed(
            db, project_id, payload.agent_name, payload.action,
            evidence_link=payload.evidence_link, task_id=payload.task_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return ActivityOut.model_validate(a).model_dump()


@router.get("/projects/{project_id}/feed", response_model=list[ActivityOut])
def get_feed(project_id: str, limit: int = 200, db: Session = Depends(get_db)):
    return [ActivityOut.model_validate(a).model_dump()
            for a in agent8.get_activity_feed(db, project_id, limit)]
