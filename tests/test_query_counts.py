"""No page's number of SQL statements may grow with the size of the library.

A template that touches a relationship the query didn't load (or a dropped `selectinload`) makes one
statement per album: invisible in a test library of 3 albums, slow in a real one of thousands.
"""
import pytest
from sqlalchemy import event

from app.db import engine

HTMX = {"HX-Request": "true"}  # the library answers with just the grid
PAGES = {
    "library": (lambda albums: "/", None),
    "library grid": (lambda albums: "/", HTMX),
    "library sorted": (lambda albums: "/?sort=title&order=asc", HTMX),
    "library random": (lambda albums: "/?order=random&seed=1", HTMX),
    "library filtered": (lambda albums: "/?genre=rock&year_min=1990", HTMX),
    "triage card": (lambda albums: f"/triage/{albums[1].id}", None),
    "album page": (lambda albums: f"/albums/{albums[1].id}", None),
    "stats": (lambda albums: "/stats", None),
    "genres": (lambda albums: "/genres", None),
    "vibes": (lambda albums: "/vibes", None),
    "export": (lambda albums: "/export.csv", None),
}


def _statements(client, url, headers=None) -> int:
    count = 0

    def on_execute(*_):
        nonlocal count
        count += 1

    event.listen(engine, "before_cursor_execute", on_execute)
    try:
        r = client.get(url, headers=headers or {}, follow_redirects=False)
    finally:
        event.remove(engine, "before_cursor_execute", on_execute)
    assert r.status_code == 200, (url, r.status_code)
    return count


@pytest.mark.parametrize("page", PAGES)
def test_statement_count_does_not_grow_with_library(client, make_album, make_vibe, page):
    url, headers = PAGES[page]
    vibes = [make_vibe("calm"), make_vibe("loud")]

    def add(n, start):
        # Every other album is left without vibes, so triage has cards to show
        return [make_album(f"Album {i}", f"Artist {i}", 1990 + i, vibes=vibes if i % 2 else [],
                           genres=["rock", f"genre {i}"], notes="n", spotify_url="https://open.spotify.com/album/x")
                for i in range(start, start + n)]

    albums = add(4, 0)
    few = _statements(client, url(albums), headers)
    albums += add(30, 4)
    many = _statements(client, url(albums), headers)
    assert many == few, f"{page}: {few} statements with 4 albums, {many} with 34"
