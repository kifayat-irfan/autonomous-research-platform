"""Agent 5 API: dataset upload, reproducible analysis, script export."""
import json
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app.agents import agent5_analysis as agent5
from app.core.config import DATA_DIR
from app.core.database import get_db
from app.models.schemas import AnalysisReviewRequest, AnalysisRunOut

router = APIRouter(prefix="/analysis", tags=["analysis"])

UPLOAD_DIR = DATA_DIR / "analysis" / "uploads"


@router.post("/runs", response_model=AnalysisRunOut)
def create_run(project_id: str = Form(...),
               task_id: str | None = Form(None),
               protocol_id: str | None = Form(None),
               analysis_type: str = Form(...),
               config: str = Form("{}"),
               file: UploadFile = File(...),
               db: Session = Depends(get_db)):
    """Upload a CSV dataset and run a reproducible analysis (Agent 5)."""
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only .csv datasets are accepted")
    try:
        cfg = json.loads(config or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"config is not valid JSON: {exc}")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    dest = UPLOAD_DIR / Path(file.filename).name
    with open(dest, "wb") as out:
        shutil.copyfileobj(file.file, out)
    try:
        run = agent5.create_analysis_run(
            db, project_id, str(dest), file.filename, analysis_type, cfg,
            task_id=task_id, protocol_id=protocol_id)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return AnalysisRunOut.model_validate(run).model_dump()


@router.get("/runs", response_model=list[AnalysisRunOut])
def list_runs(project_id: str | None = None, task_id: str | None = None,
              limit: int = 100, db: Session = Depends(get_db)):
    rows = agent5.list_runs(db, project_id=project_id, task_id=task_id,
                           limit=limit)
    return [AnalysisRunOut.model_validate(agent5.get_run_dict(r.id, db)).model_dump()
            for r in rows]


@router.get("/runs/{run_id}", response_model=AnalysisRunOut)
def get_run(run_id: str, db: Session = Depends(get_db)):
    try:
        run = agent5.get_run_dict(run_id, db)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return AnalysisRunOut.model_validate(run).model_dump()


@router.get("/runs/{run_id}/script", response_class=PlainTextResponse)
def get_script(run_id: str, db: Session = Depends(get_db)):
    """Download the exact reproduction script for an analysis run."""
    try:
        run = agent5.get_run_dict(run_id, db)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    path = run.get("script_path")
    if not path or not Path(path).exists():
        raise HTTPException(status_code=404, detail="Script file not found")
    return PlainTextResponse(Path(path).read_text(encoding="utf-8"),
                             media_type="text/x-python")


@router.post("/runs/{run_id}/review", response_model=AnalysisRunOut)
def review_run(run_id: str, payload: AnalysisReviewRequest,
               db: Session = Depends(get_db)):
    """Researcher approve/reject analysis results (gates Agent 6)."""
    try:
        r = agent5.review_analysis(db, run_id, payload.approved, payload.note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    run = agent5.get_run_dict(r.id, db)
    return AnalysisRunOut.model_validate(run).model_dump()
