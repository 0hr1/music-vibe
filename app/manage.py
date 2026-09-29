"""Admin commands for when the web page isn't an option (e.g. you're locked out).

    python -m app.manage users
    python -m app.manage invite [--uses N] [--days N] [--note TEXT]
    python -m app.manage reset-password USERNAME

On Fly.io: fly ssh console -C "python -m app.manage users"
"""

import argparse
import secrets

from sqlalchemy import func, select

from . import invites, migrations
from .auth import hash_password
from .db import SessionLocal, engine
from .models import User


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.manage")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("users", help="list accounts")
    inv = sub.add_parser("invite", help="make an invite code")
    inv.add_argument("--uses", type=int, default=1, help="accounts it can make; 0 for unlimited (default 1)")
    inv.add_argument("--days", type=int, default=14, help="days until it expires; 0 for never (default 14)")
    inv.add_argument("--note", default="")
    reset = sub.add_parser("reset-password", help="give an account a new random password")
    reset.add_argument("username")
    args = parser.parse_args(argv)

    migrations.upgrade(engine)
    with SessionLocal() as db:
        if args.cmd == "users":
            for u in db.scalars(select(User).order_by(User.id)):
                print(f"{u.id:>4}  {u.username}  (joined {u.created_at:%Y-%m-%d})")
        elif args.cmd == "invite":
            invite = invites.create(db, max_uses=args.uses or None, days=args.days or None, note=args.note)
            print(invite.code)
        elif args.cmd == "reset-password":
            user = db.scalar(select(User).where(func.lower(User.username) == args.username.lower()))
            if not user:
                print(f"No user called {args.username!r}.")
                return 1
            temp = secrets.token_urlsafe(9)
            user.password_hash = hash_password(temp)
            db.commit()
            print(f"New password for {user.username}: {temp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
