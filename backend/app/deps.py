"""FastAPI dependencies: current user + role guards."""
from __future__ import annotations

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from .db import get_db
from .models import User
from .security import parse_token


def current_user(
    authorization: str = Header(default=""),
    db: Session = Depends(get_db),
) -> User:
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    payload = parse_token(authorization[7:])
    if not payload:
        raise HTTPException(401, "invalid or expired token")
    user = db.get(User, payload["uid"])
    if not user:
        raise HTTPException(401, "unknown user")
    return user


def require_role(*roles: str):
    def guard(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(403, f"requires role: {'/'.join(roles)}")
        return user

    return guard
