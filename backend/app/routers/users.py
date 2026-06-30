"""User management endpoints — admin only."""
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from app.auth import require_auth, require_roles, hash_password
from app.database import get_db
from app.models import User
from app.audit import log_audit


router = APIRouter(prefix="/api/users", tags=["users"])


class UserOut(BaseModel):
    id: int
    email: str
    name: str
    role: str
    is_active: bool
    must_change_password: bool = False
    last_login: Optional[datetime] = None
    created_at: datetime
    model_config = {"from_attributes": True}


class UserCreate(BaseModel):
    email: EmailStr
    name: str
    role: str = "viewer"
    password: str


class UserUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None
    password: Optional[str] = None


ROLES = {"admin", "manager", "ops", "viewer"}


@router.get("", response_model=list[UserOut], dependencies=[Depends(require_auth)])
def list_users(db: Session = Depends(get_db)):
    return db.query(User).order_by(User.id).all()


@router.post("", response_model=UserOut)
def create_user(payload: UserCreate, db: Session = Depends(get_db), claims: dict = Depends(require_roles("admin"))):
    if payload.role not in ROLES:
        raise HTTPException(status_code=400, detail=f"Role must be one of {sorted(ROLES)}")
    if db.query(User).filter(User.email == payload.email.lower()).first():
        raise HTTPException(status_code=400, detail="Email already exists")
    u = User(
        email=payload.email.lower(),
        name=payload.name,
        role=payload.role,
        password_hash=hash_password(payload.password),
        is_active=True,
        must_change_password=True,  # admin sets a temp password; user must change on first login
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    log_audit(db, claims, "create", "user", entity_id=str(u.id), summary=f"Created user {u.email} ({u.role})")
    return u


@router.patch("/{user_id}", response_model=UserOut)
def update_user(user_id: int, payload: UserUpdate, db: Session = Depends(get_db), claims: dict = Depends(require_roles("admin"))):
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    changes = []
    if payload.name is not None and payload.name != u.name:
        u.name = payload.name; changes.append("name")
    if payload.email is not None:
        new_email = payload.email.lower()
        if new_email != u.email:
            if db.query(User).filter(User.email == new_email, User.id != u.id).first():
                raise HTTPException(status_code=400, detail="Email already in use")
            u.email = new_email; changes.append(f"email→{new_email}")
    if payload.role is not None:
        if payload.role not in ROLES:
            raise HTTPException(status_code=400, detail=f"Role must be one of {sorted(ROLES)}")
        if payload.role != u.role:
            u.role = payload.role; changes.append(f"role→{payload.role}")
    if payload.is_active is not None and payload.is_active != u.is_active:
        u.is_active = payload.is_active; changes.append(f"is_active→{payload.is_active}")
    if payload.password:
        u.password_hash = hash_password(payload.password)
        u.must_change_password = True  # admin reset → user must change on next login
        changes.append("password (reset, must-change)")
    db.commit()
    db.refresh(u)
    log_audit(db, claims, "update", "user", entity_id=str(u.id), summary=f"Updated {u.email}: " + ", ".join(changes))
    return u


@router.delete("/{user_id}", status_code=204)
def delete_user(user_id: int, db: Session = Depends(get_db), claims: dict = Depends(require_roles("admin"))):
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    if claims.get("user_id") == u.id:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")
    db.delete(u)
    db.commit()
    log_audit(db, claims, "delete", "user", entity_id=str(user_id), summary=f"Deleted user {u.email}")
