"""Supervisor API: task orchestration with approval gates."""
import json
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.agents.supervisor import Supervisor
from app.core.config import DATA_DIR
from app.core.database import get_db
from app.models.schemas import (
    ApproveRequest,
    SupervisorAdvanceGapsRequest,
    SupervisorAdvanceWritingRequest,
    SupervisorTaskRequest,
    TaskOut,
)
from app.services import task_service

router = APIRouter(prefix="/supervisor", tags=["supervisor"])


@router.post("/tasks", response_model=TaskOut)
def create_and_run_task(payload: SupervisorTaskRequest,
                        db: Session = Depends(get_db)):
    """Create a research task and run the pipeline synchronously.

    The task may pause at gates (`needs_pdf_upload` / `needs_approval`);
    use /resume and /approve to continue.
    """
    sup = Supervisor(db)
    task = sup.create_task(
        payload.query, project_id=payload.project_id,
        sources=payload.sources, limit=payload.limit,
    )
    try:
        task = sup.run_task(task.task_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return TaskOut.model_validate(task).model_dump()


@router.get("/tasks", response_model=list[TaskOut])
def list_tasks(status: str | None = None, project_id: str | None = None,
               limit: int = 100, db: Session = Depends(get_db)):
    rows = task_service.list_tasks(db, status=status, project_id=project_id,
                                   limit=limit)
    return [TaskOut.model_validate(t).model_dump() for t in rows]


@router.get("/tasks/{task_id}", response_model=TaskOut)
def get_task(task_id: str, db: Session = Depends(get_db)):
    task = task_service.get_task(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return TaskOut.model_validate(task).model_dump()


@router.post("/tasks/{task_id}/resume", response_model=TaskOut)
def resume_task(task_id: str, db: Session = Depends(get_db)):
    """Resume a `needs_pdf_upload` task after PDFs were uploaded."""
    try:
        task = Supervisor(db).resume_after_upload(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return TaskOut.model_validate(task).model_dump()


@router.post("/tasks/{task_id}/approve", response_model=TaskOut)
def approve_task(task_id: str, payload: ApproveRequest,
                 db: Session = Depends(get_db)):
    """Researcher approve/reject at the `needs_approval` gate."""
    try:
        task = Supervisor(db).approve_task(task_id, payload.approved,
                                           payload.note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return TaskOut.model_validate(task).model_dump()


@router.post("/tasks/{task_id}/retry", response_model=TaskOut)
def retry_task(task_id: str, db: Session = Depends(get_db)):
    try:
        task = Supervisor(db).retry_task(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return TaskOut.model_validate(task).model_dump()


# --- Phase 2 gated advancement ---

@router.post("/tasks/{task_id}/advance-to-gaps", response_model=TaskOut)
def advance_to_gaps(task_id: str, db: Session = Depends(get_db)):
    """Continue a verified task into Agent 3 (gap discovery + challenge)."""
    try:
        task = Supervisor(db).advance_to_gaps(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return TaskOut.model_validate(task).model_dump()


@router.post("/tasks/{task_id}/advance-to-design", response_model=TaskOut)
def advance_to_design(task_id: str, payload: SupervisorAdvanceGapsRequest,
                      db: Session = Depends(get_db)):
    """Build Agent 4 protocols for researcher-approved gaps."""
    try:
        task = Supervisor(db).advance_to_design(task_id, payload.design_type)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return TaskOut.model_validate(task).model_dump()


@router.post("/tasks/{task_id}/advance-to-audit", response_model=TaskOut)
def advance_to_audit(task_id: str, db: Session = Depends(get_db)):
    """Run Agent 7 audit on approved protocols (HIGH severity -> blocked)."""
    try:
        task = Supervisor(db).advance_to_audit(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return TaskOut.model_validate(task).model_dump()


# --- Phase 3 gated advancement ---

@router.post("/tasks/{task_id}/advance-to-analysis", response_model=TaskOut)
def advance_to_analysis(task_id: str,
                        analysis_type: str = Form(...),
                        config: str = Form("{}"),
                        file: UploadFile = File(...),
                        db: Session = Depends(get_db)):
    """Upload a CSV dataset and run Agent 5 (needs protocol approval first)."""
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only .csv datasets are accepted")
    try:
        cfg = json.loads(config or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"config is not valid JSON: {exc}")
    dest_dir = DATA_DIR / "analysis" / "task_uploads"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{task_id}_{Path(file.filename).name}"
    with open(dest, "wb") as out:
        out.write(file.file.read())
    try:
        task = Supervisor(db).advance_to_analysis(
            task_id, str(dest), file.filename, analysis_type, cfg)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return TaskOut.model_validate(task).model_dump()


@router.post("/tasks/{task_id}/advance-to-writing", response_model=TaskOut)
def advance_to_writing(task_id: str, payload: SupervisorAdvanceWritingRequest,
                       db: Session = Depends(get_db)):
    """Draft a manuscript (Agent 6) from approved analysis runs."""
    try:
        task = Supervisor(db).advance_to_writing(task_id, payload.sections)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return TaskOut.model_validate(task).model_dump()


@router.post("/tasks/{task_id}/advance-to-final-audit", response_model=TaskOut)
def advance_to_final_audit(task_id: str, db: Session = Depends(get_db)):
    """Final Agent 7 audit over approved manuscripts (HIGH -> blocked)."""
    try:
        task = Supervisor(db).advance_to_final_audit(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return TaskOut.model_validate(task).model_dump()
