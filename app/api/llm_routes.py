"""LLM status + configuration endpoints (no key material ever leaves the server)."""
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core import llm as llm_client

router = APIRouter(prefix="/llm", tags=["llm"])


class LLMConfigureRequest(BaseModel):
    api_key: str
    base_url: str | None = None
    model: str | None = None


def _env_path() -> Path:
    # repo root = parent of app/
    return Path(__file__).resolve().parents[2] / ".env"


def _write_env(updates: dict) -> None:
    """Upsert keys in the .env file (gitignored). Preserves other entries."""
    p = _env_path()
    lines = p.read_text().splitlines() if p.exists() else []
    seen = set()
    out = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            k = stripped.split("=", 1)[0].strip()
            if k in updates:
                out.append(f"{k}={updates[k]}")
                seen.add(k)
                continue
        out.append(line)
    for k, v in updates.items():
        if k not in seen:
            out.append(f"{k}={v}")
    p.write_text("\n".join(out) + "\n")


@router.get("/status", response_model=dict)
def llm_status():
    """Whether LLM enhancement is active (key configured). Safe to display."""
    s = llm_client.status()
    s["note"] = ("When disabled, all agents use deterministic templates. "
                 "Enter your API key below to enable LLM-enhanced drafting.")
    return s


@router.post("/configure", response_model=dict)
def llm_configure(payload: LLMConfigureRequest):
    """Save the LLM API key (and optional base URL / model).

    Writes to the gitignored .env AND applies to the running process
    immediately — no restart needed. The key value is never returned.
    """
    key = (payload.api_key or "").strip()
    if len(key) < 8:
        raise HTTPException(status_code=400,
                            detail="API key looks too short.")
    updates = {"LLM_API_KEY": key}
    if payload.base_url and payload.base_url.strip():
        updates["LLM_BASE_URL"] = payload.base_url.strip().rstrip("/")
    if payload.model and payload.model.strip():
        updates["LLM_MODEL"] = payload.model.strip()
    try:
        _write_env(updates)
    except OSError as exc:
        raise HTTPException(status_code=500,
                            detail=f"Could not write .env: {exc}")
    # apply live — no restart required
    for k, v in updates.items():
        os.environ[k] = v
    s = llm_client.status()
    return {"configured": s["configured"], "model": s["model"],
            "base_url": s["base_url"],
            "message": "LLM key saved and active."}
