"""Application configuration.

All settings can be overridden with environment variables or a `.env`
file in the project root (e.g. DATABASE_URL, SEMANTIC_SCHOLAR_API_KEY).
"""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root: <root>/research_platform
ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT_DIR / "data"
PDF_DIR = DATA_DIR / "pdfs"
CHROMA_DIR = DATA_DIR / "chroma_db"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_name: str = "Autonomous Research Platform"
    api_version: str = "v1"
    debug: bool = False

    # SQLite for Phase 1. Swap for postgres, e.g.
    # postgresql+psycopg2://user:pass@localhost:5432/research_platform
    database_url: str = f"sqlite:///{DATA_DIR / 'research_platform.db'}"

    # --- Agent 1: discovery sources ---
    semantic_scholar_base_url: str = "https://api.semanticscholar.org/graph/v1"
    semantic_scholar_api_key: str | None = None  # optional; raises rate limits
    pubmed_base_url: str = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    ncbi_tool: str = "research-platform-phase1"
    ncbi_email: str | None = None  # NCBI asks that API users identify an email
    http_timeout_seconds: int = 30
    max_pdf_mb: int = 60

    # --- Pipeline defaults ---
    discovery_default_limit: int = 10
    discovery_sources: list[str] = ["semantic_scholar", "pubmed"]
    extraction_top_k: int = 5
    chroma_collection_name: str = "paper_passages"


settings = Settings()

# Ensure local storage directories exist on import.
for _d in (DATA_DIR, PDF_DIR, CHROMA_DIR):
    _d.mkdir(parents=True, exist_ok=True)
