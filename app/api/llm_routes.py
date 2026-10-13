"""LLM status endpoint (no key material ever leaves the server)."""
from fastapi import APIRouter

from app.core import llm as llm_client

router = APIRouter(prefix="/llm", tags=["llm"])


@router.get("/status", response_model=dict)
def llm_status():
    """Whether LLM enhancement is active (key configured). Safe to display."""
    s = llm_client.status()
    s["note"] = ("When disabled, all agents use deterministic templates. "
                 "Set LLM_API_KEY to enable LLM-enhanced drafting.")
    return s
