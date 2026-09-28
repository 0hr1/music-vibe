import hashlib
import hmac
import secrets

from fastapi import Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import ADMIN_USERNAMES
from .db import get_db
from .models import User

_N, _R, _P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, digest = stored.split("$")
        check = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p))
        return hmac.compare_digest(check.hex(), digest)
    except ValueError:
        return False


class LoginRequired(Exception):
    pass


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user_id = request.session.get("user_id")
    user = db.get(User, user_id) if user_id else None
    if user is None:
        raise LoginRequired()
    return user


def is_admin(db: Session, user: User) -> bool:
    if ADMIN_USERNAMES:
        return user.username.lower() in ADMIN_USERNAMES
    return user.id == db.scalar(select(func.min(User.id)))


def admin_user(user: User = Depends(current_user), db: Session = Depends(get_db)) -> User:
    if not is_admin(db, user):
        raise HTTPException(404)
    return user
