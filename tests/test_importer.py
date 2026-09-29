import asyncio
import re

from app import importer, spotify


def test_parse_lines_cleans_bullets_blanks_and_repeats():
    text = "1. Fishmans - Long Season\n\n - The Strokes - Is This It\n• kid a\n2) Kid A\nfishmans - long season"
    assert importer.parse_lines(text) == ["Fishmans - Long Season", "The Strokes - Is This It", "kid a"]


def test_parse_lines_caps_length():
    assert len(importer.parse_lines("\n".join(f"album {i}" for i in range(500)))) == importer.MAX_LINES


def test_match_line_prefers_results_containing_title_and_artist(monkeypatch):
    async def fake_search(q, limit):
        return [
            {"id": "1", "title": "OK Computer OKNOTOK", "artist": "Radiohead", "type": "Album", "thumb": ""},
            {"id": "2", "title": "OK Computer", "artist": "Radiohead", "type": "Album", "thumb": ""},
            {"id": "3", "title": "Other", "artist": "Someone", "type": "Album", "thumb": ""},
        ]
    monkeypatch.setattr(importer.deezer, "search_albums", fake_search)
    row = asyncio.run(importer.match_line("Radiohead - OK Computer", asyncio.Semaphore(1)))
    assert row["results"][0]["id"] == "2" and row["sure"]
    row = asyncio.run(importer.match_line("something unrelated", asyncio.Semaphore(1)))
    assert not row["sure"]


def test_spotify_best_match_requires_artist():
    albums = [
        {"name": "Loveless", "artists": [{"name": "Somebody Else"}], "external_urls": {"spotify": "wrong"}},
        {"name": "Loveless (Remastered)", "artists": [{"name": "My Bloody Valentine"}], "external_urls": {"spotify": "ok"}},
    ]
    assert spotify._best(albums, "Loveless", "My Bloody Valentine") == "ok"
    assert spotify._best(albums, "Loveless", "Nobody") is None


def test_import_review_and_add(client, monkeypatch, make_album, db):
    from app.models import Item
    make_album("Long Season", "Fishmans", external_id="deezer:1")

    async def fake_search(q, limit):
        return {
            "Fishmans - Long Season": [{"id": "1", "title": "Long Season", "artist": "Fishmans", "type": "Album", "thumb": ""}],
            "Radiohead - Kid A": [{"id": "2", "title": "Kid A", "artist": "Radiohead", "type": "Album", "thumb": ""}],
        }.get(q, [])

    async def fake_get(album_id):
        return {"external_id": f"deezer:{album_id}", "title": "Kid A", "artist": "Radiohead", "year": 2000,
                "genres": ["alternative"], "cover_url": ""}

    monkeypatch.setattr(importer.deezer, "search_albums", fake_search)
    monkeypatch.setattr(importer.deezer, "get_album", fake_get)
    r = client.post("/albums/import", data={"text": "Fishmans - Long Season\nRadiohead - Kid A\nnonsense"})
    assert r.status_code == 200
    assert not re.search(r'name="row" value="0"[^>]*checked', r.text)  # already owned: unticked
    assert re.search(r'name="row" value="1"[^>]*checked', r.text)
    assert "already in library" in r.text and "No match found" in r.text
    r = client.post("/albums/import/add", data={"row": ["0", "1"], "pick-0": "1", "pick-1": "2"})
    assert "Added 1 album." in r.text
    kid_a = db.query(Item).filter_by(title="Kid A").one()
    assert f'href="/triage?batch={kid_a.id}-{kid_a.id}"' in r.text
    titles = sorted(t for (t,) in db.query(Item.title).all())
    assert titles == ["Kid A", "Long Season"]
