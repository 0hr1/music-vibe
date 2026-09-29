import pytest
from playwright.sync_api import expect

from app import deezer

ALBUMS = {
    "1": {"title": "Long Season", "artist": "Fishmans", "year": 1996},
    "2": {"title": "Is This It", "artist": "The Strokes", "year": 2001},
}


@pytest.fixture(autouse=True)
def fake_deezer(monkeypatch):
    """Deezer knows the two albums above and nothing else."""
    async def search_albums(query, limit=12):
        q = query.casefold()
        return [{"source": "deezer", "id": i, "title": a["title"], "artist": a["artist"], "year": None,
                 "type": "Album", "thumb": ""}
                for i, a in ALBUMS.items() if a["artist"].casefold() in q]

    async def get_album(album_id):
        a = ALBUMS[album_id]
        return {"external_id": f"deezer:{album_id}", "title": a["title"], "artist": a["artist"],
                "year": a["year"], "genres": ["rock"], "cover_url": ""}

    monkeypatch.setattr(deezer, "search_albums", search_albums)
    monkeypatch.setattr(deezer, "get_album", get_album)


def test_import_review_and_add(logged_in):
    page = logged_in
    page.goto("/albums/import")
    page.fill("textarea[name=text]", "1. Fishmans - Long Season\n2. The Strokes - Is This It\n3. nobody - nothing")
    page.get_by_role("button", name="Find matches").click()

    expect(page.locator(".status-bar")).to_contain_text("2 of 3 matched")
    rows = page.locator(".import-row")
    expect(rows).to_have_count(3)
    expect(rows.nth(2)).to_contain_text("No match found")
    expect(rows.nth(2).locator("input[name=row]")).to_be_disabled()

    rows.nth(1).locator("input[name=row]").uncheck()  # skip The Strokes
    page.get_by_role("button", name="Add selected").click()

    expect(page.locator(".done-item")).to_have_count(1)
    expect(page.locator(".done-item")).to_contain_text("Long Season")
    page.get_by_role("link", name="Go to library").click()
    expect(page.locator(".card")).to_have_count(1)
    expect(page.locator(".card")).to_contain_text("Fishmans · 1996")
