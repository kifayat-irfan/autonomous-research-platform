"""Agent 12 API: literature watches and significance alerts."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.agents import agent12_intelligence as agent12
from app.core.database import get_db
from app.models.schemas import AlertOut, WatchCreateRequest, WatchTopicOut

router = APIRouter(prefix="/intelligence", tags=["intelligence"])


@router.post("/watches", response_model=WatchTopicOut)
def create_watch(payload: WatchCreateRequest, db: Session = Depends(get_db)):
    w = agent12.create_watch(db, payload.project_id, payload.name,
                             payload.query, payload.sources)
    return WatchTopicOut.model_validate(w).model_dump()


@router.get("/watches", response_model=list[WatchTopicOut])
def list_watches(project_id: str | None = None, limit: int = 100,
                 db: Session = Depends(get_db)):
    rows = agent12.list_watches(db, project_id=project_id, limit=limit)
    return [WatchTopicOut.model_validate(r).model_dump() for r in rows]


@router.post("/watches/{watch_id}/run", response_model=dict)
def run_watch(watch_id: str, limit: int = 10, db: Session = Depends(get_db)):
    """Execute a monitoring run: discover new papers, raise alerts."""
    try:
        return agent12.run_watch(db, watch_id, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/alerts", response_model=list[AlertOut])
def list_alerts(project_id: str | None = None,
                acknowledged: bool | None = None, limit: int = 100,
                db: Session = Depends(get_db)):
    rows = agent12.list_alerts(db, project_id=project_id,
                               acknowledged=acknowledged, limit=limit)
    return [AlertOut.model_validate(r).model_dump() for r in rows]


@router.post("/alerts/{alert_id}/acknowledge", response_model=dict)
def acknowledge(alert_id: str, db: Session = Depends(get_db)):
    try:
        a = agent12.acknowledge_alert(db, alert_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"alert_id": a.id, "acknowledged": a.acknowledged}
