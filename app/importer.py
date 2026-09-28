"""Bulk import: match free-form text lines ("Fishmans - Long Season", "long season by fishmans", ...)
to Deezer albums, then fetch the chosen ones in full."""

import asyncio
import re

import httpx

from . import covers, deezer, spotify

MAX_LINES = 200
CONCURRENCY = 5  # Deezer allows ~50 requests per 5 seconds
_LIST_MARKER = re.compile(r"^\s*(?:[-*•·]|\d+[.)])\s*")


def norm(text: str) -> str:
    return "".join(ch for ch in text.casefold() if ch.isalnum())


def parse_lines(text: str) -> list[str]:
    """One album per line; list bullets/numbering, blank lines and repeats are dropped."""
    lines, seen = [], set()
    for raw in text.splitlines():
        line = " ".join(_LIST_MARKER.sub("", raw).split())[:200]
        if line and norm(line) not in seen:
            seen.add(norm(line))
            lines.append(line)
    return lines[:MAX_LINES]


def _contains(line: str, result: dict) -> tuple[bool, bool]:
    n = norm(line)
    return norm(result["title"]) in n, norm(result["artist"]) in n


async def match_line(line: str, sem: asyncio.Semaphore) -> dict:
    """Deezer results for a line, with those whose title and artist both appear in the line first.
    `sure` is False when the best result doesn't clearly match, so the review screen flags it."""
    async with sem:
        try:
            results = await deezer.search_albums(line, limit=8)
        except httpx.HTTPError:
            return {"line": line, "results": [], "sure": False, "error": True}
    results.sort(key=lambda r: -sum(_contains(line, r)))  # stable: Deezer's order breaks ties
    return {"line": line, "results": results[:6], "sure": bool(results) and all(_contains(line, results[0])),
            "error": False}


async def match_all(lines: list[str]) -> list[dict]:
    sem = asyncio.Semaphore(CONCURRENCY)
    return await asyncio.gather(*(match_line(line, sem) for line in lines))


async def _fetch(deezer_id: str, sem: asyncio.Semaphore) -> dict | None:
    async with sem:
        try:
            info = await deezer.get_album(deezer_id)
        except httpx.HTTPError:
            return None
        cover_url = info.get("cover_url", "")
        info["cover_file"] = await covers.download(cover_url) if covers.is_trusted_url(cover_url) else None
        try:
            info["spotify_url"] = await spotify.find_album_url(info["title"], info["artist"])
        except httpx.HTTPError:
            info["spotify_url"] = None
        return info


async def fetch_albums(deezer_ids: list[str]) -> list[dict | None]:
    """Full details (year, genres, stored cover, Spotify link) per id; None where Deezer failed."""
    sem = asyncio.Semaphore(CONCURRENCY)
    return await asyncio.gather(*(_fetch(i, sem) for i in deezer_ids))
