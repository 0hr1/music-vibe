"""Finds an album's Spotify link via the Spotify Web API (client-credentials key, no user login).
Optional: everything here is a no-op unless SPOTIFY_CLIENT_ID/SECRET are set."""

import time

import httpx

from .config import SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET

API = "https://api.spotify.com/v1"
TOKEN_URL = "https://accounts.spotify.com/api/token"

enabled = bool(SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET)
_token: tuple[str, float] | None = None  # (access token, expires at)


def _norm(text: str) -> str:
    return "".join(ch for ch in text.casefold() if ch.isalnum())


async def _get_token(client: httpx.AsyncClient) -> str:
    global _token
    if _token and _token[1] > time.time() + 60:
        return _token[0]
    resp = await client.post(TOKEN_URL, data={"grant_type": "client_credentials"},
                             auth=(SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET))
    resp.raise_for_status()
    data = resp.json()
    _token = (data["access_token"], time.time() + data.get("expires_in", 3600))
    return _token[0]


def _best(albums: list[dict], title: str, artist: str) -> str | None:
    """Exact title match beats a prefix match (e.g. "... (Deluxe)"); the artist must match when known."""
    t, a = _norm(title), _norm(artist)
    ranked = []
    for alb in albums:
        artists = [_norm(x.get("name", "")) for x in alb.get("artists", [])]
        if a and not any(x and x in a for x in artists):  # "A & B" credits match either artist
            continue
        name = _norm(alb.get("name", ""))
        rank = 0 if name == t else 1 if name.startswith(t) or t.startswith(name) else 2
        ranked.append((rank, alb))
    ranked.sort(key=lambda r: r[0])
    if not ranked or ranked[0][0] == 2:
        return None
    return ranked[0][1].get("external_urls", {}).get("spotify")


async def find_album_url(title: str, artist: str) -> str | None:
    if not enabled or not title.strip():
        return None
    async with httpx.AsyncClient(timeout=httpx.Timeout(8, connect=5)) as client:
        headers = {"Authorization": f"Bearer {await _get_token(client)}"}
        for q in (f'album:"{title}" artist:"{artist}"' if artist else f'album:"{title}"', f"{title} {artist}"):
            resp = await client.get(f"{API}/search", params={"q": q, "type": "album", "limit": 10}, headers=headers)
            resp.raise_for_status()
            if url := _best(resp.json().get("albums", {}).get("items", []), title, artist):
                return url
    return None
