"""Agent 4 API: protocol design, measurement models, ethics plans."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.agents import agent4_design as agent4
from app.core.database import get_db
from app.models.schemas import (
    MeasurementModelRequest,
    ProtocolCreateRequest,
    ProtocolOut,
    ProtocolReviewRequest,
)

router = APIRouter(prefix="/design", tags=["design"])


@router.post("/protocols", response_model=ProtocolOut)
def create_protocol(payload: ProtocolCreateRequest, db: Session = Depends(get_db)):
    """Draft a protocol for a researcher-approved gap (Agent 4)."""
    try:
        proto = agent4.propose_research_protocol(
            payload.gap_id, payload.design_type, db,
            target_population=payload.target_population,
            sample_size_needed=payload.sample_size_needed,
            constructs=payload.constructs,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return ProtocolOut.model_validate(proto).model_dump()


@router.get("/protocols", response_model=list[ProtocolOut])
def list_protocols(project_id: str | None = None, task_id: str | None = None,
                   limit: int = 100, db: Session = Depends(get_db)):
    rows = agent4.list_protocols(db, project_id=project_id, task_id=task_id,
                                 limit=limit)
    return [ProtocolOut.model_validate(agent4.get_protocol_dict(r.id, db)).model_dump()
            for r in rows]


@router.get("/protocols/{protocol_id}", response_model=ProtocolOut)
def get_protocol(protocol_id: str, db: Session = Depends(get_db)):
    try:
        proto = agent4.get_protocol_dict(protocol_id, db)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return ProtocolOut.model_validate(proto).model_dump()


@router.post("/protocols/{protocol_id}/review", response_model=ProtocolOut)
def review_protocol(protocol_id: str, payload: ProtocolReviewRequest,
                    db: Session = Depends(get_db)):
    """Researcher approve/reject — gates any move to data collection."""
    try:
        p = agent4.review_protocol(db, protocol_id, payload.approved,
                                   payload.note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    proto = agent4.get_protocol_dict(p.id, db)
    return ProtocolOut.model_validate(proto).model_dump()


@router.post("/measurement-model", response_model=list[dict])
def measurement_model(payload: MeasurementModelRequest):
    """Map constructs to validated scales (with citations) or proposed items."""
    return agent4.construct_measurement_model(payload.constructs)


@router.post("/sampling-ethics-plan", response_model=dict)
def sampling_ethics_plan(target_population: str,
                         sample_size_needed: int | None = None,
                         design_type: str = "survey"):
    """Standalone sampling + ethics plan (includes approval checkpoint)."""
    return agent4.generate_sampling_and_ethics_plan(
        target_population, sample_size_needed, design_type)
