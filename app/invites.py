"""Invite codes: how new people get an account once sign-up is closed."""

import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .models import InviteCode

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O or 1/I, so codes survive being read aloud
GROUPS, GROUP_LEN = 3, 4


def normalize(code: str) -> str:
    """'abcd efgh-jklm' -> 'ABCD-EFGH-JKLM', so people can type codes however they like."""
    chars = "".join(ch for ch in code.upper() if ch.isalnum())
    return "-".join(chars[i : i + GROUP_LEN] for i in range(0, len(chars), GROUP_LEN))


def _new_code() -> str:
    return normalize("".join(secrets.choice(ALPHABET) for _ in range(GROUPS * GROUP_LEN)))


def create(db: Session, *, max_uses: int | None = 1, days: int | None = 14, note: str = "",
           created_by: int | None = None) -> InviteCode:
    expires = datetime.now(timezone.utc) + timedelta(days=days) if days else None
    invite = InviteCode(code=_new_code(), max_uses=max_uses, expires_at=expires, note=note[:200],
                        created_by=created_by)
    db.add(invite)
    db.commit()
    return invite


def find_usable(db: Session, code: str) -> InviteCode | None:
    invite = db.scalar(select(InviteCode).where(InviteCode.code == normalize(code))) if code.strip() else None
    return invite if invite and invite.usable() else None


def redeem(db: Session, invite: InviteCode) -> bool:
    """Count one use, unless someone else just took the last one. Caller commits."""
    stmt = update(InviteCode).where(InviteCode.id == invite.id).values(uses=InviteCode.uses + 1)
    if invite.max_uses is not None:
        stmt = stmt.where(InviteCode.uses < InviteCode.max_uses)
    return db.execute(stmt).rowcount == 1
