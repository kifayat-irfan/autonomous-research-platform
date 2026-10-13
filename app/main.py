"""FastAPI application entry point — Autonomous Research Platform (Phase 1)."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import (
    analysis_routes,
    audit_routes,
    cross_domain_routes,
    design_routes,
    discovery_routes,
    extraction_routes,
    gap_routes,
    intelligence_routes,
    journal_routes,
    llm_routes,
    memory_routes,
    papers_routes,
    review_routes,
    supervisor_routes,
    writing_routes,
)
from app.core.config import settings
from app.core.database import init_db

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    log.info("Database initialised at %s", settings.database_url)
    yield


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description=(
        "Phase 1 (Literature-bank MVP): multi-source paper discovery "
        "(Agent 1), passage-level extraction with source-anchored evidence "
        "(Agent 2), project memory (Agent 8), and a gated supervisor "
        "orchestrator. Phase 2: gap discovery & novelty challenge (Agent 3), "
        "research design & measurement (Agent 4), scientific audit (Agent 7), "
        "cross-domain ideas (Agent 9). Phase 3: reproducible analysis "
        "(Agent 5), manuscript drafting (Agent 6), systematic review & "
        "meta-analysis (Agent 10), journal matching (Agent 11), literature "
        "intelligence (Agent 12). Every factual output links to its source."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(discovery_routes.router, prefix="/api")
app.include_router(extraction_routes.router, prefix="/api")
app.include_router(supervisor_routes.router, prefix="/api")
app.include_router(memory_routes.router, prefix="/api")
app.include_router(papers_routes.router, prefix="/api")
# Phase 2 agents
app.include_router(gap_routes.router, prefix="/api")
app.include_router(design_routes.router, prefix="/api")
app.include_router(audit_routes.router, prefix="/api")
app.include_router(cross_domain_routes.router, prefix="/api")
# Phase 3 agents
app.include_router(analysis_routes.router, prefix="/api")
app.include_router(writing_routes.router, prefix="/api")
app.include_router(review_routes.router, prefix="/api")
app.include_router(journal_routes.router, prefix="/api")
app.include_router(intelligence_routes.router, prefix="/api")
app.include_router(llm_routes.router, prefix="/api")


@app.get("/", tags=["root"])
def root():
    return {
        "service": settings.app_name,
        "phase": "1 — Literature-bank MVP",
        "docs": "/docs",
        "health": "/api/health",
    }


@app.get("/api/health", tags=["root"])
def health():
    return {"status": "ok", "version": "0.1.0"}
