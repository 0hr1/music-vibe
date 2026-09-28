"""Album lookup via MusicBrainz + Cover Art Archive (no API key needed).

MusicBrainz search can be slow (some queries take 30s+), so every call here uses a
short timeout and callers treat failure as "no data" rather than an error."""

import httpx

from .config import USER_AGENT

MB = "https://musicbrainz.org/ws/2"
CAA = "https://coverartarchive.org"
MAX_GENRES = 5


def _client(timeout: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT}, timeout=httpx.Timeout(timeout, connect=5), follow_redirects=True
    )


def _artist(credit: list[dict]) -> str:
    return "".join(c.get("name", "") + c.get("joinphrase", "") for c in credit or [])


def _year(date: str | None) -> int | None:
    return int(date[:4]) if date and date[:4].isdigit() else None


def _plain(text: str) -> str:
    """Strip Lucene syntax so user text is treated as plain words."""
    return " ".join("".join(ch if ch.isalnum() or ch in "'&" else " " for ch in text).split())


def _top_genres(rg: dict) -> list[str]:
    genres = sorted(rg.get("genres", []), key=lambda g: -g.get("count", 0))
    return [g["name"] for g in genres[:MAX_GENRES]]


def cover_url(mbid: str, size: int = 500) -> str:
    return f"{CAA}/release-group/{mbid}/front-{size}"


async def search_albums(query: str, limit: int = 10, timeout: float = 12) -> list[dict]:
    words = _plain(query)
    if not words:
        return []
    params = {"query": f"({words}) AND (primarytype:album OR primarytype:ep)", "fmt": "json", "limit": limit}
    async with _client(timeout) as client:
        resp = await client.get(f"{MB}/release-group", params=params)
        resp.raise_for_status()
    return [
        {
            "source": "mb",
            "id": rg["id"],
            "title": rg.get("title", ""),
            "artist": _artist(rg.get("artist-credit")),
            "year": _year(rg.get("first-release-date")),
            "type": rg.get("primary-type", ""),
            "thumb": cover_url(rg["id"], 250),
        }
        for rg in resp.json().get("release-groups", [])
    ]


async def get_album(mbid: str, timeout: float = 10) -> dict:
    async with _client(timeout) as client:
        resp = await client.get(f"{MB}/release-group/{mbid}", params={"inc": "genres artist-credits", "fmt": "json"})
        resp.raise_for_status()
    rg = resp.json()
    return {
        "external_id": f"mb:{rg['id']}",
        "title": rg.get("title", ""),
        "artist": _artist(rg.get("artist-credit")),
        "year": _year(rg.get("first-release-date")),
        "genres": _top_genres(rg),
        "cover_url": cover_url(rg["id"]),
    }


async def find_details(title: str, artist: str, timeout: float = 15) -> dict:
    """Best-effort detailed genres and original release year for an album found elsewhere
    (Deezer often reports the reissue date)."""
    t, a = _plain(title), _plain(artist)
    if not t:
        return {}
    query = f'releasegroup:"{t}"' + (f' AND artist:"{a}"' if a else "")
    async with _client(timeout) as client:
        resp = await client.get(f"{MB}/release-group", params={"query": query, "fmt": "json", "limit": 1})
        resp.raise_for_status()
        hits = resp.json().get("release-groups", [])
        if not hits or hits[0].get("score", 0) < 90:
            return {}
        resp = await client.get(f"{MB}/release-group/{hits[0]['id']}", params={"inc": "genres", "fmt": "json"})
        resp.raise_for_status()
    rg = resp.json()
    return {"genres": _top_genres(rg), "year": _year(rg.get("first-release-date"))}
