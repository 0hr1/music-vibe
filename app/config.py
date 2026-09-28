import os
import secrets
from pathlib import Path


def _bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


DATA_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
COVERS_DIR = DATA_DIR / "covers"
DB_PATH = DATA_DIR / "music_vibe.db"
BACKUPS_DIR = DATA_DIR / "backups"

ALLOW_SIGNUP = _bool("ALLOW_SIGNUP", False)
# Who can manage invite codes and users (comma-separated). Empty: the first account.
ADMIN_USERNAMES = {n.strip().lower() for n in os.environ.get("ADMIN_USERNAMES", "").split(",") if n.strip()}
HTTPS_ONLY = _bool("HTTPS_ONLY", False)
MB_CONTACT = os.environ.get("MB_CONTACT", "").strip() or "no-contact-configured"
USER_AGENT = f"music-vibe/0.1 ( {MB_CONTACT} )"
SPOTIFY_CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID", "").strip()
SPOTIFY_CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET", "").strip()

SESSION_MAX_AGE = 60 * 60 * 24 * 365  # stay logged in for a year
BACKUP_KEEP = max(1, int(os.environ.get("BACKUP_KEEP", "14") or 14))  # daily database snapshots to keep

DATA_DIR.mkdir(parents=True, exist_ok=True)
COVERS_DIR.mkdir(parents=True, exist_ok=True)
BACKUPS_DIR.mkdir(parents=True, exist_ok=True)


def _secret_key() -> str:
    if key := os.environ.get("SECRET_KEY", "").strip():
        return key
    path = DATA_DIR / "secret_key"
    if not path.exists():
        path.write_text(secrets.token_urlsafe(48))
        path.chmod(0o600)
    return path.read_text().strip()


SECRET_KEY = _secret_key()
