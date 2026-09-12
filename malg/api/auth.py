"""Single-account bearer authentication for the versioned MALG API."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from malg.config import AuthConfig

TOKEN_TTL_SECONDS = 8 * 60 * 60
bearer_scheme = HTTPBearer(auto_error=False)


class TokenRequest(BaseModel):
    """Credentials submitted to the administrator login endpoint."""

    model_config = ConfigDict(extra="forbid")
    username: str
    password: str


class TokenResponse(BaseModel):
    """Bearer token returned after successful authentication."""

    access_token: str
    token_type: str = Field(pattern="^bearer$")
    expires_in: int


class AuthService:
    """Issue and validate stateless tokens for the configured account."""

    def __init__(self, config: AuthConfig) -> None:
        self._config = config

    def login(self, credentials: TokenRequest) -> TokenResponse:
        """Authenticate credentials or raise a generic operational HTTP error."""
        if not self._config.password:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="authentication is not configured",
            )
        if not self._valid_credentials(credentials.username, credentials.password):
            raise self._unauthorized()
        issued_at = int(time.time())
        payload = {
            "username": self._config.username,
            "iat": issued_at,
            "exp": issued_at + TOKEN_TTL_SECONDS,
        }
        encoded_payload = self._encode(json.dumps(payload, separators=(",", ":")).encode())
        signature = self._sign(encoded_payload)
        return TokenResponse(
            access_token=f"{encoded_payload}.{signature}",
            token_type="bearer",
            expires_in=TOKEN_TTL_SECONDS,
        )

    async def require_authenticated(
        self,
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    ) -> None:
        """Validate the request bearer token without exposing failure reasons."""
        if (
            not self._config.password
            or credentials is None
            or credentials.scheme.lower() != "bearer"
        ):
            raise self._unauthorized()
        try:
            encoded_payload, signature = credentials.credentials.split(".", 1)
            expected_signature = self._sign(encoded_payload)
            if not hmac.compare_digest(signature, expected_signature):
                raise ValueError
            payload = json.loads(self._decode(encoded_payload))
            now = int(time.time())
            if (
                not isinstance(payload, dict)
                or payload.get("username") != self._config.username
                or not isinstance(payload.get("iat"), int)
                or not isinstance(payload.get("exp"), int)
                or payload["exp"] <= now
                or payload["exp"] - payload["iat"] != TOKEN_TTL_SECONDS
            ):
                raise ValueError
        except (ValueError, TypeError, KeyError, json.JSONDecodeError, UnicodeDecodeError):
            raise self._unauthorized() from None

    def _valid_credentials(self, username: str, password: str) -> bool:
        """Compare both credential fields in constant-time comparisons."""
        return hmac.compare_digest(username, self._config.username) and hmac.compare_digest(
            password, self._config.password
        )

    def _sign(self, encoded_payload: str) -> str:
        return self._encode(
            hmac.new(
                self._config.password.encode(), encoded_payload.encode(), hashlib.sha256
            ).digest()
        )

    @staticmethod
    def _encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")

    @staticmethod
    def _decode(value: str) -> bytes:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))

    @staticmethod
    def _unauthorized() -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )


def token_endpoint(service: AuthService) -> Callable[[TokenRequest], Awaitable[TokenResponse]]:
    """Build the unauthenticated token endpoint bound to startup configuration."""

    async def endpoint(credentials: TokenRequest) -> TokenResponse:
        return service.login(credentials)

    return endpoint
