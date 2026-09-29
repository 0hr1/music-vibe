"""Adding one album by hand (search, prefill, the background MusicBrainz and Spotify lookups), editing
and deleting it."""

import asyncio

import pytest
from playwright.sync_api import expect

from app import deezer, main, musicbrainz, spotify
from app.models import Item


@pytest.fixture(autouse=True)
def fake_services(monkeypatch):
    """Deezer knows Loveless (as a 2012 reissue tagged "rock"); MusicBrainz knows its real genres and year.
    `fake_services.delay` slows the background lookups, to race them against typing."""
    class Fake:
        delay = 0.0

    async def search_albums(query, limit=12):
        return [{"source": "deezer", "id": "1", "title": "Loveless", "artist": "My Bloody Valentine",
                 "year": None, "type": "Album", "thumb": ""}]

    async def get_album(album_id):
        return {"external_id": "deezer:1", "title": "Loveless", "artist": "My Bloody Valentine", "year": 2012,
                "genres": ["rock"], "cover_url": ""}

    async def mb_search(query, limit=10, timeout=12):
        return []

    async def find_details(title, artist):
        await asyncio.sleep(Fake.delay)
        return {"genres": ["shoegaze", "dream pop"], "year": 1991}

    async def find_album_url(title, artist):
        await asyncio.sleep(Fake.delay)
        return "https://open.spotify.com/album/found"

    monkeypatch.setattr(deezer, "search_albums", search_albums)
    monkeypatch.setattr(deezer, "get_album", get_album)
    monkeypatch.setattr(musicbrainz, "search_albums", mb_search)
    monkeypatch.setattr(musicbrainz, "find_details", find_details)
    monkeypatch.setattr(spotify, "find_album_url", find_album_url)
    monkeypatch.setitem(main.templates.env.globals, "spotify_enabled", True)
    return Fake


def _pick_loveless(page):
    page.goto("/albums/new")
    page.get_by_role("searchbox", name="Search albums").fill("loveless")
    page.get_by_role("button", name="Loveless").click()
    expect(page.get_by_role("textbox", name="Title")).to_have_value("Loveless")


def test_add_album_from_search(logged_in, db):
    page = logged_in
    _pick_loveless(page)
    # the background lookups fill in MusicBrainz's genres and original year, and the Spotify link
    expect(page.get_by_role("textbox", name="Genres")).to_have_value("shoegaze, dream pop")
    expect(page.get_by_role("spinbutton", name="Year")).to_have_value("1991")
    expect(page.get_by_role("textbox", name="Spotify link")).to_have_value("https://open.spotify.com/album/found")
    page.get_by_role("checkbox", name="winter").check()
    page.get_by_role("button", name="Add to library").click()

    card = page.locator(".card")
    expect(card).to_have_count(1)
    expect(card).to_contain_text("My Bloody Valentine · 1991")
    expect(card.locator(".chip")).to_have_text("winter")
    item = db.query(Item).one()
    assert [g.name for g in item.genres] == ["dream pop", "shoegaze"] and item.genres_checked


def test_lookups_never_overwrite_what_you_typed(logged_in, fake_services, db):
    fake_services.delay = 1.0
    page = logged_in
    _pick_loveless(page)
    page.get_by_role("textbox", name="Genres").fill("noise pop")  # before MusicBrainz answers
    page.get_by_role("textbox", name="Spotify link").fill("https://open.spotify.com/album/mine")
    expect(page.get_by_text("Checking MusicBrainz")).to_be_hidden(timeout=5000)
    expect(page.get_by_text("Looking on Spotify")).to_be_hidden(timeout=5000)
    expect(page.get_by_role("textbox", name="Genres")).to_have_value("noise pop")
    expect(page.get_by_text("(comma separated)")).to_be_visible()  # only the lookup's own note goes
    expect(page.get_by_role("textbox", name="Spotify link")).to_have_value("https://open.spotify.com/album/mine")
    page.get_by_role("button", name="Add to library").click()
    expect(page.locator(".card")).to_have_count(1)
    item = db.query(Item).one()
    assert [g.name for g in item.genres] == ["noise pop"] and item.spotify_url.endswith("/mine")


def test_add_by_hand_when_search_finds_nothing(logged_in, monkeypatch):
    async def nothing(query, limit=12):
        return []

    monkeypatch.setattr(deezer, "search_albums", nothing)
    page = logged_in
    page.goto("/albums/new")
    page.get_by_role("searchbox", name="Search albums").fill("some demo tape")
    expect(page.get_by_text("Nothing on Deezer.")).to_be_visible()
    page.get_by_role("textbox", name="Title").fill("Demo Tape")
    page.get_by_role("textbox", name="Artist").fill("Friends")
    page.get_by_role("button", name="Add to library").click()
    expect(page.locator(".card")).to_contain_text("Demo Tape")


def test_edit_and_delete_album(logged_in, make_album, make_vibe, db):
    make_vibe("summer")
    album = make_album("Old Title", "Band", 1999)
    page = logged_in
    page.goto("/")
    page.get_by_role("link", name="Old Title", exact=True).click()

    page.get_by_role("textbox", name="Title").fill("New Title")
    page.get_by_role("textbox", name="Genres").fill("post-rock, ambient")
    page.get_by_role("checkbox", name="summer").check()
    page.get_by_role("button", name="Save").click()
    card = page.locator(".card")
    expect(card).to_contain_text("New Title")
    expect(card.locator(".chip")).to_have_text("summer")
    db.expire_all()
    assert sorted(g.name for g in db.get(Item, album.id).genres) == ["ambient", "post-rock"]

    page.get_by_role("link", name="New Title", exact=True).click()
    page.once("dialog", lambda d: d.dismiss())  # cancelling keeps it
    page.get_by_role("button", name="Delete album").click()
    expect(page.get_by_role("button", name="Delete album")).to_be_visible()
    page.once("dialog", lambda d: d.accept())
    page.get_by_role("button", name="Delete album").click()
    expect(page.get_by_text("Your library is empty.")).to_be_visible()
