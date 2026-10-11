"""Business logic for Task records: lifecycle, logging, metrics."""
import json
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.schemas import TASK_STATUSES, Task


def _stamp() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def create_task(db: Session, query: str, project_id: str | None = None,
                sources: list[str] | None = None, limit: int = 10,
                agent_name: str = "supervisor") -> Task:
    task = Task(
        query=query,
        project_id=project_id,
        sources=json.dumps(sources or ["semantic_scholar", "pubmed"]),
        limit=limit,
        agent_name=agent_name,
        status="queued",
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    append_log(db, task, f"Task created (agent={agent_name}, query={query!r}).")
    return task


def get_task(db: Session, task_id: str) -> Task | None:
    return db.get(Task, task_id)


def list_tasks(db: Session, status: str | None = None,
               project_id: str | None = None, limit: int = 100) -> list[Task]:
    stmt = select(Task).order_by(Task.created_at.desc())
    if status:
        stmt = stmt.where(Task.status == status)
    if project_id:
        stmt = stmt.where(Task.project_id == project_id)
    return list(db.scalars(stmt.limit(limit)).all())


def append_log(db: Session, task: Task, line: str) -> None:
    task.execution_log = (task.execution_log or "") + f"[{_stamp()}] {line}\n"
    db.commit()


def set_status(db: Session, task: Task, status: str, log_line: str | None = None) -> Task:
    if status not in TASK_STATUSES:
        raise ValueError(f"Unknown task status: {status!r}")
    task.status = status
    if log_line:
        task.execution_log = (task.execution_log or "") + f"[{_stamp()}] {log_line}\n"
    db.commit()
    db.refresh(task)
    return task


def start_task(db: Session, task: Task) -> Task:
    task.started_at = datetime.utcnow()
    task.finished_at = None
    task.duration_seconds = None
    task.error = None
    return set_status(db, task, "running", "Execution started.")


def finish_task(db: Session, task: Task, status: str,
                error: str | None = None) -> Task:
    task.finished_at = datetime.utcnow()
    if task.started_at:
        task.duration_seconds = (task.finished_at - task.started_at).total_seconds()
    if error:
        task.error = error
    line = f"Execution finished with status={status}."
    if task.duration_seconds is not None:
        line += f" Duration={task.duration_seconds:.1f}s."
    return set_status(db, task, status, line)


def counts_by_status(db: Session) -> dict:
    rows = db.execute(
        select(Task.status, func.count(Task.task_id)).group_by(Task.status)
    ).all()
    return {row[0]: row[1] for row in rows}
