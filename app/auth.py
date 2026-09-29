import hashlib
import hmac
import secrets

from fastapi import Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import ADMIN_USERNAMES, SECRET_KEY
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


_dummy_hash: str | None = None


def check_login(user: User | None, password: str) -> bool:
    """Like verify_password, but takes as long when there's no such user, so the response time
    doesn't reveal which usernames exist."""
    global _dummy_hash
    if user is None:
        _dummy_hash = _dummy_hash or hash_password(secrets.token_urlsafe(16))
        verify_password(password, _dummy_hash)
        return False
    return verify_password(password, user.password_hash)


class LoginRequired(Exception):
    pass


def _session_check(user: User) -> str:
    """Ties a session to this account *and* its current password: a new password, or a new account
    that gets a deleted account's id, doesn't match an old cookie."""
    return hmac.new(SECRET_KEY.encode(), f"{user.id}:{user.password_hash}".encode(), hashlib.sha256).hexdigest()[:32]


def log_in(request: Request, user: User) -> None:
    request.session.clear()
    request.session["user_id"] = user.id
    request.session["check"] = _session_check(user)


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user_id = request.session.get("user_id")
    user = db.get(User, user_id) if isinstance(user_id, int) else None
    if user is None or not hmac.compare_digest(str(request.session.get("check", "")), _session_check(user)):
        request.session.clear()
        raise LoginRequired()
    return user


def admins_named() -> bool:
    return bool(ADMIN_USERNAMES)


def is_reserved_admin_name(username: str) -> bool:
    """Names in ADMIN_USERNAMES that invite codes can't claim (see the register page)."""
    return username.strip().lower() in ADMIN_USERNAMES


def is_admin(db: Session, user: User) -> bool:
    if ADMIN_USERNAMES:
        return user.username.lower() in ADMIN_USERNAMES
    return user.id == db.scalar(select(func.min(User.id)))


def admin_user(user: User = Depends(current_user), db: Session = Depends(get_db)) -> User:
    if not is_admin(db, user):
        raise HTTPException(404)
    return user
