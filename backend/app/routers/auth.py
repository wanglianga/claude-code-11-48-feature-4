"""Auth endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user
from ..models import User
from ..security import make_token, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str
    password: str


def user_view(u: User) -> dict:
    return {"id": u.id, "username": u.username, "role": u.role,
            "display_name": u.display_name}


@router.post("/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == body.username).first()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "用户名或密码错误")
    return {"token": make_token(user.id, user.role), "user": user_view(user)}


@router.get("/me")
def me(user: User = Depends(current_user)):
    return user_view(user)
