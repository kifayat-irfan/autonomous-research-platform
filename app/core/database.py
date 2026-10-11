"""SQLAlchemy engine, session factory and FastAPI dependency."""
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def _build_engine():
    kwargs: dict = {}
    if settings.database_url.startswith("sqlite"):
        # Needed for multithreaded FastAPI access to a file-based SQLite DB.
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_engine(settings.database_url, **kwargs)


engine = _build_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Iterator[Session]:
    """FastAPI dependency: yields a DB session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create all tables (Phase 1 uses create_all; Alembic migrations later)."""
    # Imported here to avoid a circular import (models import Base from here).
    from app.models import schemas  # noqa: F401

    Base.metadata.create_all(bind=engine)
