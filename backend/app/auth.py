"""
Phase 1 auth — shared-password JWT gate.

Single password set via AUTH_PASSWORD env var. On login, backend issues a
signed JWT. All protected endpoints require the JWT via Authorization
header (Bearer scheme). Health endpoint stays public.

Upgrade path: per-user accounts (Phase 1B) or M365 SSO (Phase 2) — once
Andrés grants M365 admin access.
"""
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from app.config import settings


router = APIRouter(prefix="/api/auth", tags=["auth"])
security = HTTPBearer(auto_error=False)


class LoginIn(BaseModel):
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds


@router.post("/login", response_model=TokenOut)
def login(payload: LoginIn):
    if payload.password != settings.auth_password:
        raise HTTPException(status_code=401, detail="Invalid password")
    expire = datetime.now(timezone.utc) + timedelta(hours=settings.jwt_expire_hours)
    claims = {
        "sub": "ft-team",
        "iat": int(datetime.now(timezone.utc).timestamp()),
        "exp": int(expire.timestamp()),
    }
    token = jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return TokenOut(
        access_token=token,
        token_type="bearer",
        expires_in=settings.jwt_expire_hours * 3600,
    )


def require_auth(
    creds: HTTPAuthorizationCredentials | None = Depends(security),
) -> dict:
    """Dependency for protected endpoints. Returns decoded JWT claims."""
    if creds is None or creds.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = jwt.decode(
            creds.credentials,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")
