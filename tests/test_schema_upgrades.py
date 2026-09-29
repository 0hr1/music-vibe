"""A deploy upgrades the live database in place (app/migrations.py). These tests upgrade every layout
the database has had (tests/schemas/v<N>.sql) and check the result matches a freshly built one, so a
change to models.py without a matching migration step fails here instead of in production."""

import re
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from app import migrations

SCHEMAS = Path(__file__).with_name("schemas")
SAVED = sorted(SCHEMAS.glob("v*.sql"), key=lambda p: int(p.stem[1:]))


def layout(path: Path) -> dict:
    """Tables with their columns, indexes and foreign keys: what matters, not how the SQL was written."""
    with sqlite3.connect(path) as conn:
        tables = [t for (t,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        index_sql = {name: " ".join(sql.split()) for name, sql in
                     conn.execute("SELECT name, sql FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL")}
        result = {}
        for t in tables:
            indexes = []
            for _, name, unique, origin, _ in conn.execute(f"PRAGMA index_list('{t}')"):
                cols = [c[2] for c in conn.execute(f"PRAGMA index_xinfo('{name}')") if c[5]]  # key columns only
                # SQLite names indexes it makes for UNIQUE constraints itself; only their columns matter
                indexes.append((None if name.startswith("sqlite_autoindex") else name, unique, origin, tuple(cols),
                                index_sql.get(name)))
            result[t] = {
                # sorted: ALTER TABLE can only add columns at the end, so order may differ from models.py
                "columns": sorted(c[1:] for c in conn.execute(f"PRAGMA table_info('{t}')")),
                "indexes": sorted(indexes, key=repr),
                "foreign_keys": sorted(c[2:] for c in conn.execute(f"PRAGMA foreign_key_list('{t}')")),
            }
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    return {"user_version": version, "tables": result}


def differences(got: dict, want: dict) -> list[str]:
    if got["user_version"] != want["user_version"]:
        return [f"user_version {got['user_version']} != {want['user_version']}"]
    diffs = [f"table {t}: {'missing' if t not in got['tables'] else 'unexpected'}"
             for t in sorted(set(got["tables"]) ^ set(want["tables"]))]
    for t in sorted(set(got["tables"]) & set(want["tables"])):
        for part, value in want["tables"][t].items():
            if got["tables"][t][part] != value:
                diffs.append(f"{t} {part}:\n  got  {got['tables'][t][part]}\n  want {value}")
    return diffs


def build(sql: str, path: Path) -> Path:
    version = int(re.match(r"-- user_version (\d+)", sql).group(1))
    with sqlite3.connect(path) as conn:
        conn.executescript(sql)
        conn.execute(f"PRAGMA user_version = {version}")
    return path


def upgrade(path: Path) -> Path:
    engine = create_engine(f"sqlite:///{path}")
    migrations.upgrade(engine)
    engine.dispose()
    return path


@pytest.fixture
def fresh(tmp_path) -> dict:
    """The layout a brand-new install gets."""
    return layout(upgrade(tmp_path / "fresh.db"))


def test_current_layout_is_saved(tmp_path, fresh):
    latest = SCHEMAS / f"v{len(migrations.STEPS)}.sql"
    hint = "Run `.venv/bin/python tests/schemas/save_schema.py` after adding the migration step."
    assert latest.exists(), f"No saved layout for migration step {len(migrations.STEPS)}. {hint}"
    diffs = differences(layout(build(latest.read_text(), tmp_path / "saved.db")), fresh)
    assert not diffs, (f"models.py no longer matches {latest.name}: add a step to app/migrations.py for the "
                       f"change. {hint}\n" + "\n".join(diffs))


@pytest.mark.parametrize("saved", SAVED, ids=lambda p: p.stem)
def test_old_layouts_upgrade_to_current(tmp_path, fresh, saved):
    db = build(saved.read_text(), tmp_path / "old.db")
    with sqlite3.connect(db) as conn:  # some data, so steps must cope with existing rows
        conn.execute("INSERT INTO users (username, password_hash, created_at) VALUES ('Ori', 'x', '2026-01-01')")
    for _ in range(2):  # the second upgrade must change nothing
        diffs = differences(layout(upgrade(db)), fresh)
        assert not diffs, "\n".join(diffs)


def test_saved_layouts_are_sequential():
    assert [p.stem for p in SAVED] == [f"v{i}" for i in range(len(SAVED))]
