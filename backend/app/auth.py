"""
Auth — per-user accounts + role-based access.

Roles:
- admin   — Andrés Yeguez, Adam Sultan. Full access incl. delete + send out-of-scope.
- manager — Gabriela Pita, Luis Cruz. Approve/send invoices for their scope.
- ops     — Andrea Palmisano, Yamisley Barros. Create/edit. Cannot send to MSC.
- viewer  — read-only.

Login:
- email + password (preferred)
- legacy password-only (matches AUTH_PASSWORD env) — kept for transitional UX.
  Legacy login produces a viewer-role JWT so it can't mutate.

JWT carries user_id, name, role, email. require_auth returns those claims.
require_roles(roles) gates endpoints to specific roles.

Rate-limit on login: 8 attempts / minute per IP (slowapi).
"""
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, EmailStr
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import User

from sqlalchemy.orm import Session as _Session  # for type

log = logging.getLogger("ft.auth")

router = APIRouter(prefix="/api/auth", tags=["auth"])
security = HTTPBearer(auto_error=False)
limiter = Limiter(key_func=get_remote_address)


class LoginIn(BaseModel):
    email: Optional[EmailStr] = None
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: dict


class UserOut(BaseModel):
    id: int
    email: str
    name: str
    role: str
    must_change_password: bool = False

    model_config = {"from_attributes": True}


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def _issue_token(user_claims: dict) -> TokenOut:
    expire = datetime.now(timezone.utc) + timedelta(hours=settings.jwt_expire_hours)
    claims = {
        **user_claims,
        "iat": int(datetime.now(timezone.utc).timestamp()),
        "exp": int(expire.timestamp()),
    }
    token = jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return TokenOut(
        access_token=token,
        token_type="bearer",
        expires_in=settings.jwt_expire_hours * 3600,
        user={
            "id": user_claims.get("user_id"),
            "email": user_claims.get("email"),
            "name": user_claims.get("name"),
            "role": user_claims.get("role"),
            "must_change_password": user_claims.get("must_change_password", False),
        },
    )


@router.post("/login", response_model=TokenOut)
@limiter.limit("8/minute")
def login(request: Request, payload: LoginIn, db: Session = Depends(get_db)):
    # Personal accounts only — legacy shared-password login removed (decided 2026-06-27).
    if not payload.email:
        raise HTTPException(status_code=400, detail="Email and password required — sign in with your own account")

    user = db.query(User).filter(User.email == payload.email.lower(), User.is_active == True).first()
    if not user or not verify_password(payload.password, user.password_hash):
        # Constant-time-ish: don't leak which is wrong
        raise HTTPException(status_code=401, detail="Invalid email or password")

    user.last_login = datetime.utcnow()
    db.commit()

    return _issue_token({
        "sub": user.email,
        "user_id": user.id,
        "email": user.email,
        "name": user.name,
        "role": user.role,
        "must_change_password": user.must_change_password,
    })


@router.get("/me", response_model=UserOut)
def me(claims: dict = Depends(lambda creds=Depends(security): _decode(creds)), db: Session = Depends(get_db)):
    if not claims.get("user_id"):
        # Stale legacy token (shared login removed) — treat as viewer, no identity
        return UserOut(id=0, email="shared@fasttrackgroup.us", name=claims.get("name", "Shared"), role="viewer", must_change_password=False)
    user = db.get(User, claims["user_id"])
    if not user:
        raise HTTPException(status_code=401, detail="User no longer exists")
    return user


def _decode(creds: Optional[HTTPAuthorizationCredentials]) -> dict:
    if creds is None or creds.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return jwt.decode(
            creds.credentials,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


def require_auth(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> dict:
    return _decode(creds)


def require_roles(*allowed_roles: str):
    """Dependency factory — gate endpoint to specific roles."""
    def _check(claims: dict = Depends(require_auth)) -> dict:
        if claims.get("role") not in allowed_roles:
            raise HTTPException(
                status_code=403,
                detail=f"Role '{claims.get('role')}' not authorized. Required: {list(allowed_roles)}",
            )
        return claims
    return _check


class PasswordChangeIn(BaseModel):
    current_password: str
    new_password: str


@router.post("/change-password", status_code=204)
def change_password(payload: PasswordChangeIn, claims: dict = Depends(require_auth), db: Session = Depends(get_db)):
    if not claims.get("user_id"):
        raise HTTPException(status_code=403, detail="Legacy viewers can't change password. Sign in with a real account.")
    user = db.get(User, claims["user_id"])
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=401, detail="Current password incorrect")
    if len(payload.new_password) < 8:
        raise HTTPException(status_code=400, detail="New password must be at least 8 chars")
    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = False
    db.commit()
    log.info("password_changed", extra={"user_id": user.id})


class ForgotPasswordIn(BaseModel):
    email: EmailStr


class ResetPasswordIn(BaseModel):
    email: EmailStr
    token: str
    new_password: str


@router.post("/forgot-password", status_code=200)
@limiter.limit("5/minute")
def forgot_password(request: Request, payload: ForgotPasswordIn, db: Session = Depends(get_db)):
    """Generate a one-time reset token + email a reset link. Same response whether or not the
    email exists (no user enumeration). Token is stored bcrypt-hashed with an expiry."""
    from app.email_send import send_email
    user = db.query(User).filter(User.email == payload.email.lower(), User.is_active == True).first()
    if user:
        token = secrets.token_urlsafe(32)
        user.reset_token_hash = hash_password(token)
        user.reset_token_expires = datetime.utcnow() + timedelta(minutes=settings.reset_token_ttl_minutes)
        db.commit()
        reset_url = f"{settings.app_base_url}/?reset={token}&email={user.email}"
        body = (f"Hi {user.name},\n\nWe received a request to reset your Fast Track password.\n"
                f"Open this link to set a new password:\n\n{reset_url}\n\n"
                f"The link expires in {settings.reset_token_ttl_minutes} minutes. "
                f"If you didn't request this, you can ignore this email.")
        method = send_email(user.email, "Fast Track — reset your password", body)
        log.info("password_reset_requested", extra={"user_id": user.id, "delivery": method})
    return {"ok": True, "message": "If that email is registered, a password reset link has been sent."}


@router.post("/reset-password", status_code=204)
@limiter.limit("10/minute")
def reset_password(request: Request, payload: ResetPasswordIn, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == payload.email.lower()).first()
    now = datetime.utcnow()
    if (not user or not user.reset_token_hash or not user.reset_token_expires
            or user.reset_token_expires < now
            or not verify_password(payload.token, user.reset_token_hash)):
        raise HTTPException(status_code=400, detail="Invalid or expired reset link. Please request a new one.")
    if len(payload.new_password) < 8:
        raise HTTPException(status_code=400, detail="New password must be at least 8 characters")
    user.password_hash = hash_password(payload.new_password)
    user.reset_token_hash = None
    user.reset_token_expires = None
    user.must_change_password = False
    db.commit()
    log.info("password_reset_done", extra={"user_id": user.id})
