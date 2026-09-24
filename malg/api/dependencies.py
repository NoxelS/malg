"""Dependencies shared by MALG API routers."""

from __future__ import annotations

from collections.abc import Generator
from typing import Annotated, cast

from fastapi import Depends, HTTPException, Request
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from malg.crm.client import TwentyClient


def get_session() -> Generator[Session]:
    """Declare the session boundary configured by the application factory."""
    raise RuntimeError("The MALG application did not configure a database session.")
    yield


SessionDependency = Annotated[Session, Depends(get_session)]


def memory_store_engine(session: Session) -> Engine:
    """Return the engine bound to the current request session."""
    return cast(Engine, session.get_bind())


def get_crm_client(request: Request) -> TwentyClient:
    """Return the lifespan-owned runtime client, never a schema-admin client."""
    client = getattr(request.app.state, "crm_client", None)
    if not isinstance(client, TwentyClient):
        raise HTTPException(status_code=503, detail="crm_unavailable")
    return client


CrmClientDependency = Annotated[TwentyClient, Depends(get_crm_client)]
