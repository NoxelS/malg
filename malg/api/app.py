"""Application factory for MALG's versioned persistence API."""

from __future__ import annotations

from collections.abc import AsyncIterator, Generator
from contextlib import asynccontextmanager
from importlib.metadata import version as distribution_version
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, status
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from malg.api.auth import AuthService, TokenResponse, token_endpoint
from malg.api.dependencies import get_session
from malg.api.routers.crm import router as crm_router
from malg.api.routers.dashboard import dashboard_router
from malg.api.routers.jobs import router as jobs_router
from malg.api.routers.memory import router as memory_router
from malg.api.routers.traces import router as traces_router
from malg.config import (
    AuthConfig,
    get_auth_config,
    get_twenty_config,
    get_worker_config,
    load_settings,
)
from malg.crm.client import TwentyClient
from malg.database.session import make_engine, make_session_factory, session_dependency


def create_app(
    *,
    database_engine: Engine | None = None,
    auth_config: AuthConfig | None = None,
    crm_client: TwentyClient | None = None,
) -> FastAPI:
    """Create the MALG API without creating or migrating database schema.

    Args:
        database_engine: Optional engine for tests or an explicitly selected database.
        auth_config: Optional startup-captured authentication configuration.
        crm_client: Optional caller-owned runtime client for isolated integration.

    Returns:
        An authenticated operational job console with external CRM reads.
    """
    engine = database_engine or make_engine()
    sessions = make_session_factory(engine)
    settings = load_settings()
    auth_service = AuthService(auth_config or get_auth_config(settings))

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        """Own runtime transport without making local history depend on CRM."""
        client = crm_client
        if client is None:
            try:
                client = TwentyClient(get_twenty_config(settings))
            except ValueError:
                client = None
        application.state.crm_client = client
        try:
            yield
        finally:
            if client is not None and crm_client is None:
                await client.aclose()
            application.state.crm_client = None

    app = FastAPI(title="MALG API", version=distribution_version("malg"), lifespan=lifespan)
    app.state.crm_client = crm_client
    app.state.database_engine = engine

    def configured_session() -> Generator[Session]:
        """Yield sessions bound to this application instance's engine."""
        yield from session_dependency(sessions)

    app.dependency_overrides[get_session] = configured_session
    app.post("/api/v1/auth/token", response_model=TokenResponse)(token_endpoint(auth_service))
    auth_dependency = Depends(auth_service.require_authenticated)
    app.include_router(memory_router, dependencies=[auth_dependency])
    app.include_router(jobs_router, dependencies=[auth_dependency])
    app.include_router(crm_router, dependencies=[auth_dependency])
    app.include_router(traces_router, dependencies=[auth_dependency])
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
