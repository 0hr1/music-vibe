"""Fast album search via Deezer's public API (no key needed). Genres are coarse, so
MusicBrainz is used to refine them when it answers."""

import httpx

API = "https://api.deezer.com"


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(8, connect=5))


def _year(date: str | None) -> int | None:
    return int(date[:4]) if date and date[:4].isdigit() and date[:4] != "0000" else None


async def search_albums(query: str, limit: int = 12) -> list[dict]:
    async with _client() as client:
        resp = await client.get(f"{API}/search/album", params={"q": query, "limit": limit})
        resp.raise_for_status()
    return [
        {
            "source": "deezer",
            "id": str(a["id"]),
            "title": a.get("title", ""),
            "artist": (a.get("artist") or {}).get("name", ""),
            "year": None,  # search results don't include dates
            "type": (a.get("record_type") or "").capitalize(),
            "thumb": a.get("cover_medium") or "",
        }
        for a in resp.json().get("data", [])
        if a.get("record_type") != "single"
    ]


async def get_album(album_id: str) -> dict:
    async with _client() as client:
        resp = await client.get(f"{API}/album/{album_id}")
        resp.raise_for_status()
    a = resp.json()
    if "error" in a:
        raise httpx.HTTPError(str(a["error"]))
    return {
        "external_id": f"deezer:{a['id']}",
        "title": a.get("title", ""),
        "artist": (a.get("artist") or {}).get("name", ""),
        "year": _year(a.get("release_date")),
        "genres": [g["name"].lower() for g in (a.get("genres") or {}).get("data", [])],
        "cover_url": a.get("cover_big") or a.get("cover_xl") or "",
    }
