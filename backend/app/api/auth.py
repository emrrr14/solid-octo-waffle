"""Sign-in, token refresh and sign-out.

The shape the mobile client needs:

    POST /api/auth/login    email + password  -> access (15 min) + refresh (30 d)
    POST /api/auth/refresh  refresh           -> new access + new refresh (rotated)
    POST /api/auth/logout   refresh           -> family revoked

Access tokens are verified statelessly on every request *and* on every websocket
handshake, which is what keeps a 1 Hz stream from hitting the database once a
second.  Refresh tokens are opaque, stored hashed, single-use and rotated - see
`app.security.tokens` for why each of those matters.

Every failure returns the same 401 with the same body.  Distinguishing "no such
user" from "wrong password" hands an attacker a free user-enumeration oracle.
"""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, EmailStr, Field

from app.db.repositories import AuthError, RefreshTokenRepository, UserRepository
from app.security.tokens import TokenError, TokenSettings, decode_access_token, issue_access_token

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])
bearer_scheme = HTTPBearer(auto_error=False)

INVALID = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=256)
    device: str | None = Field(default=None, max_length=128)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=16, max_length=512)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds, so the client can refresh before expiry


def _settings(request: Request) -> TokenSettings:
    settings = getattr(request.app.state, "token_settings", None)
    if settings is None:  # pragma: no cover - misconfiguration, not a code path
        raise HTTPException(status_code=503, detail="Auth is not configured")
    return settings


@router.post("/login", response_model=TokenPair)
async def login(request: Request, body: LoginRequest) -> TokenPair:
    settings = _settings(request)
    async with request.app.state.session_factory() as session:
        try:
            user = await UserRepository(session).authenticate(body.email, body.password)
            refresh = await RefreshTokenRepository(session, settings).issue(
                user.id, device=body.device
            )
            await session.commit()
        except AuthError:
            await session.rollback()
            raise INVALID from None

    return TokenPair(
        access_token=issue_access_token(user.id, settings),
        refresh_token=refresh,
        expires_in=int(settings.access_ttl.total_seconds()),
    )


@router.post("/refresh", response_model=TokenPair)
async def refresh(request: Request, body: RefreshRequest) -> TokenPair:
    settings = _settings(request)
    async with request.app.state.session_factory() as session:
        repo = RefreshTokenRepository(session, settings)
        try:
            new_refresh, user_id = await repo.rotate(body.refresh_token)
            await session.commit()
        except AuthError as exc:
            # A replay revokes the family, so the commit must survive the error.
            await session.commit()
            log.info("refresh rejected: %s", exc)
            raise INVALID from None

    return TokenPair(
        access_token=issue_access_token(user_id, settings),
        refresh_token=new_refresh,
        expires_in=int(settings.access_ttl.total_seconds()),
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, body: RefreshRequest) -> None:
    """Revoke the presented token's family: this device, not every device.

    Signing out on a lost phone should not sign the user out on their iPad.
    """
    settings = _settings(request)
    async with request.app.state.session_factory() as session:
        repo = RefreshTokenRepository(session, settings)
        try:
            await repo.rotate(body.refresh_token)  # consumes it
        except AuthError:
            pass  # already invalid: logout is idempotent by design
        await session.commit()


async def current_user_id(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> str:
    """FastAPI dependency: the authenticated user, or 401."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise INVALID
    try:
        return decode_access_token(credentials.credentials, _settings(request)).user_id
    except TokenError as exc:
        log.debug("access token rejected: %s", exc)
        raise INVALID from None


CurrentUser = Annotated[str, Depends(current_user_id)]
