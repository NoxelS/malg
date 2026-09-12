"""Application factory for MALG's versioned persistence API."""

from __future__ import annotations

from collections.abc import Generator
from importlib.metadata import version as distribution_version
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, status
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from malg.api.auth import AuthService, TokenResponse, token_endpoint
from malg.api.routers.artifacts import get_session, router
from malg.api.routers.dashboard import dashboard_router
from malg.api.routers.jobs import router as jobs_router
from malg.config import AuthConfig, get_auth_config, get_worker_config, load_settings
from malg.database.session import make_engine, make_session_factory, session_dependency


def create_app(
    *, database_engine: Engine | None = None, auth_config: AuthConfig | None = None
) -> FastAPI:
    """Create the MALG API without creating or migrating database schema.

    Args:
        database_engine: Optional engine for tests or an explicitly selected database.
        auth_config: Optional startup-captured authentication configuration.

    Returns:
        A FastAPI application serving versioned campaign and ICP CRUD routes.
    """
    engine = database_engine or make_engine()
    sessions = make_session_factory(engine)
    settings = load_settings()
    auth_service = AuthService(auth_config or get_auth_config(settings))
    app = FastAPI(title="MALG API", version=distribution_version("malg"))
    app.state.database_engine = engine

    def configured_session() -> Generator[Session]:
        """Yield sessions bound to this application instance's engine."""
        yield from session_dependency(sessions)

    app.dependency_overrides[get_session] = configured_session
    app.post("/api/v1/auth/token", response_model=TokenResponse)(token_endpoint(auth_service))
    auth_dependency = Depends(auth_service.require_authenticated)
    app.include_router(router, dependencies=[auth_dependency])
    app.include_router(jobs_router, dependencies=[auth_dependency])
    worker_config = get_worker_config(settings)
    app.include_router(
        dashboard_router(worker_config.heartbeat_timeout_seconds),
        dependencies=[auth_dependency],
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        """Report that the HTTP process is running without querying dependencies."""
        return {"status": "ok"}

    @app.get("/ready")
    def ready(session: Annotated[Session, Depends(get_session)]) -> dict[str, str]:
        """Report readiness only when the configured database accepts a query."""
        try:
            session.execute(text("SELECT 1"))
        except Exception as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="database is unavailable",
            ) from error
        return {"status": "ready"}

    return app


app = create_app()
