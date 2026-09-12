"""Database engine and request-session construction for the MALG API."""

from __future__ import annotations

import os
from collections.abc import Generator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

DEFAULT_DATABASE_URL = "postgresql+psycopg://malg:malg-local-password@localhost:5432/malg"


def database_url() -> str:
    """Return the configured database URL without exposing its credentials."""
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


def make_engine(url: str | None = None) -> Engine:
    """Construct an engine for the configured database without creating schema objects."""
    return create_engine(url or database_url(), pool_pre_ping=True)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create request-scoped sessions that do not expire returned ORM objects."""
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def session_dependency(factory: sessionmaker[Session]) -> Generator[Session]:
    """Yield one transaction-capable session and always close it after the request."""
    session = factory()
    try:
        yield session
    finally:
        session.close()
