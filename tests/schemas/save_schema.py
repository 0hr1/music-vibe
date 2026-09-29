"""Saves the current database layout as tests/schemas/v<N>.sql, N being the number of migration steps.

Run it after adding a step to app/migrations.py (and the matching change to app/models.py):
    .venv/bin/python tests/schemas/save_schema.py
Keep the older files: test_schema_upgrades.py checks that each one upgrades to the current layout."""

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="music-vibe-schema-")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app import migrations, models  # noqa: E402, F401  (models registers the tables)
from app.db import DB_PATH, engine  # noqa: E402


def dump(conn: sqlite3.Connection) -> str:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    rows = conn.execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' "
                        "ORDER BY type DESC, name")
    return f"-- user_version {version}\n" + "".join(sql.strip() + ";\n\n" for (sql,) in rows)


if __name__ == "__main__":
    migrations.upgrade(engine)
    engine.dispose()
    with sqlite3.connect(DB_PATH) as conn:
        path = Path(__file__).with_name(f"v{len(migrations.STEPS)}.sql")
        path.write_text(dump(conn))
    print(f"wrote {path}")
