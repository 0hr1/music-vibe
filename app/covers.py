import uuid
from urllib.parse import urlparse

import httpx

from .config import COVERS_DIR, USER_AGENT

_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}
MAX_BYTES = 10 * 1024 * 1024
TRUSTED_HOSTS = ("coverartarchive.org", "dzcdn.net")


def is_trusted_url(url: str) -> bool:
    """Only fetch covers from the sources we search, never arbitrary URLs posted to the form."""
    u = urlparse(url)
    host = u.hostname or ""
    return u.scheme == "https" and any(host == h or host.endswith("." + h) for h in TRUSTED_HOSTS)


def _save(data: bytes, content_type: str) -> str | None:
    ext = _EXT.get(content_type.split(";")[0].strip().lower())
    if not ext or not data or len(data) > MAX_BYTES:
        return None
    name = f"{uuid.uuid4().hex}{ext}"
    (COVERS_DIR / name).write_bytes(data)
    return name


async def download(url: str) -> str | None:
    """Fetch a cover from a URL and store it locally; returns the file name or None."""
    try:
        async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, timeout=20, follow_redirects=True) as c:
            resp = await c.get(url)
    except httpx.HTTPError:
        return None
    if resp.status_code != 200:
        return None
    return _save(resp.content, resp.headers.get("content-type", ""))


def save_upload(data: bytes, content_type: str) -> str | None:
    return _save(data, content_type)


def delete(name: str | None) -> None:
    if name:
        (COVERS_DIR / name).unlink(missing_ok=True)
