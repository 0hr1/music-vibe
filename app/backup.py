"""Daily snapshots of the SQLite database into data/backups, keeping the newest BACKUP_KEEP.

Runs inside the app process (checked hourly), so it needs no cron. Covers are plain files in
data/covers and aren't copied; back up the whole data folder to keep those too."""

import asyncio
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .config import BACKUP_KEEP, BACKUPS_DIR, DB_PATH

log = logging.getLogger("uvicorn.error").getChild("backup")  # shows up in the server/docker logs

EVERY = 24 * 60 * 60
CHECK_EVERY = 60 * 60


def backups() -> list[Path]:
    """Oldest first (names are timestamps)."""
    return sorted(BACKUPS_DIR.glob("music_vibe-*.db"))


def make_backup() -> Path:
    """Consistent copy via SQLite's online backup API (safe while the app is writing)."""
    path = BACKUPS_DIR / f"music_vibe-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.db"
    src, dst = sqlite3.connect(DB_PATH), sqlite3.connect(path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    for old in backups()[:-BACKUP_KEEP]:
        old.unlink()
    return path


def backup_due() -> bool:
    existing = backups()
    return not existing or datetime.now().timestamp() - existing[-1].stat().st_mtime >= EVERY


async def run_forever() -> None:
    while True:
        try:
            if backup_due():
                path = await asyncio.to_thread(make_backup)
                log.info("database backed up to %s", path.name)
        except Exception:  # a failed backup must never take the app down
            log.exception("database backup failed")
        await asyncio.sleep(CHECK_EVERY)
