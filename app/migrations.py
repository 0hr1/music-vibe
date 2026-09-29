"""Schema changes for databases that already exist.

`create_all` makes missing tables but never changes existing ones, so each change to a table goes
here as a step (and into models.py, which is what new databases are built from). SQLite's
`PRAGMA user_version` records how many steps a database has had. Only ever append steps."""

from sqlalchemy import Connection, Engine, inspect

from .db import Base


def _username_unique_ignoring_case(conn: Connection) -> None:
    conn.exec_driver_sql("CREATE UNIQUE INDEX IF NOT EXISTS ux_users_username_lower ON users (lower(username))")


STEPS = [
    _username_unique_ignoring_case,
]


def upgrade(engine: Engine) -> None:
    with engine.begin() as conn:
        new = not inspect(conn).has_table("users")
        Base.metadata.create_all(conn)
        version = conn.exec_driver_sql("PRAGMA user_version").scalar()
        if not new:
            for step in STEPS[version:]:
                step(conn)
        conn.exec_driver_sql(f"PRAGMA user_version = {len(STEPS)}")
