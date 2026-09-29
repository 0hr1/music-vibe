import asyncio

import httpx
import pytest

from app import main, musicbrainz
from app.db import SessionLocal
from app.models import Genre, Item, User, Vibe


@pytest.fixture
def fake_mb(monkeypatch):
    """MusicBrainz answers with `fake_mb.answer` (a dict, or an exception to raise) and counts calls."""
    class Fake:
        answer: dict | Exception = {}
        calls = 0

    async def find_details(title, artist):
        Fake.calls += 1
        if isinstance(Fake.answer, Exception):
            raise Fake.answer
        return Fake.answer

    monkeypatch.setattr(musicbrainz, "find_details", find_details)
    return Fake


def test_page_walks_the_batch(client, make_album):
    a, b = make_album("Alpha", "Artist A", 1999), make_album("Beta", "Artist B")
    ids = f"{a.id},{b.id}"
    r = client.get(f"/triage?ids={ids}")
    assert "Alpha" in r.text and "Beta" not in r.text and "<b>1</b> of 2" in r.text
    assert f'href="/triage?ids={ids}&amp;i=1"' in r.text  # next
    r = client.get(f"/triage?ids={ids}&i=1")
    assert "Beta" in r.text and "Finish →" in r.text
    r = client.get(f"/triage?ids={ids}&i=2")
    assert "That's all 2 albums." in r.text


def test_page_skips_missing_and_other_users_albums(client, make_album, db):
    other = User(username="other", password_hash="x")
    db.add(other)
    db.commit()
    theirs = Item(user_id=other.id, title="Theirs")
    db.add(theirs)
    db.commit()
    mine = make_album("Mine")
    r = client.get(f"/triage?ids={theirs.id},99999,{mine.id}")
    assert "Mine" in r.text and "Theirs" not in r.text and "<b>1</b> of 1" in r.text
    assert "nothing here to tag" in client.get("/triage").text


def test_set_vibes(client, make_album, make_vibe, db):
    winter, summer = make_vibe("winter"), make_vibe("summer")
    a = make_album("A", vibes=[winter])
    r = client.post(f"/triage/{a.id}/vibes", data={"vibes": [summer.id]})
    assert r.status_code == 204
    db.expire_all()
    assert [v.name for v in db.get(Item, a.id).vibes] == ["summer"]


def test_add_and_remove_genres(client, make_album, db):
    a = make_album("A", genres=["pop", "rock"])
    r = client.post(f"/triage/{a.id}/genres", data={"add": "Dream Pop, shoegaze"})
    assert "dream pop" in r.text and "shoegaze" in r.text
    client.post(f"/triage/{a.id}/genres", data={"remove": "pop"})
    db.expire_all()
    item = db.get(Item, a.id)
    assert [g.name for g in item.genres] == ["dream pop", "rock", "shoegaze"] and item.genres_checked


def test_refine_uses_musicbrainz_once(client, make_album, db, fake_mb):
    fake_mb.answer = {"genres": ["shoegaze", "dream pop"], "year": 1991}
    a = make_album("Loveless", "My Bloody Valentine", 2012, genres=["rock"])
    r = client.post(f"/triage/{a.id}/refine")
    assert "shoegaze" in r.text and "Genres from MusicBrainz." in r.text and "· 1991" in r.text
    db.expire_all()
    item = db.get(Item, a.id)
    assert sorted(g.name for g in item.genres) == ["dream pop", "shoegaze"] and item.year == 1991
    assert item.genres_checked
    client.post(f"/triage/{a.id}/refine")  # already checked: no second lookup
    assert fake_mb.calls == 1


def test_refine_keeps_deezer_genres_when_musicbrainz_has_none(client, make_album, db, fake_mb):
    fake_mb.answer = {}
    a = make_album("Obscure", genres=["rock"])
    client.post(f"/triage/{a.id}/refine")
    db.expire_all()
    assert [g.name for g in db.get(Item, a.id).genres] == ["rock"] and db.get(Item, a.id).genres_checked


def test_refine_failure_tries_again_next_time(client, make_album, db, fake_mb):
    fake_mb.answer = httpx.ConnectError("down")
    a = make_album("A", genres=["rock"])
    r = client.post(f"/triage/{a.id}/refine")
    assert "MusicBrainz didn&#39;t answer" in r.text
    db.expire_all()
    assert not db.get(Item, a.id).genres_checked


def test_refine_never_overwrites_hand_edited_genres(client, make_album, db, monkeypatch):
    a = make_album("A", genres=["rock"])

    async def slow_lookup(title, artist):  # the user edits genres while MusicBrainz is answering
        with SessionLocal() as other:
            item = other.get(Item, a.id)
            item.genres = [*item.genres, Genre(name="krautrock")]
            item.genres_checked = True
            other.commit()
        return {"genres": ["something else"]}

    monkeypatch.setattr(musicbrainz, "find_details", slow_lookup)
    client.post(f"/triage/{a.id}/refine")
    db.expire_all()
    assert [g.name for g in db.get(Item, a.id).genres] == ["krautrock", "rock"]


def test_card_and_prefetch_share_one_lookup(monkeypatch):
    calls = []

    async def lookup(title, artist):
        calls.append(title)
        await asyncio.sleep(0.01)
        return {"genres": ["jazz"]}

    monkeypatch.setattr(musicbrainz, "find_details", lookup)

    async def both():
        return await asyncio.gather(main._look_up(7, "T", "A"), main._look_up(7, "T", "A"))

    assert asyncio.run(both()) == [{"genres": ["jazz"]}] * 2
    assert calls == ["T"] and not main._lookups


def test_page_prefetches_next_unchecked_album(client, make_album):
    a, b = make_album("A"), make_album("B")
    r = client.get(f"/triage?ids={a.id},{b.id}")
    assert f'hx-post="/triage/{a.id}/refine"' in r.text  # the card itself
    assert f'hx-post="/triage/{b.id}/refine" hx-trigger="load delay' in r.text
    r = client.get(f"/triage?ids={a.id},{b.id}&i=1")
    assert "delay:500ms" not in r.text  # last album: nothing to prefetch


def test_remove_moves_on_to_the_next_album(client, make_album, db):
    a, b = make_album("A").id, make_album("B").id
    r = client.post(f"/triage/{a}/delete", data={"ids": f"{a},{b}", "i": "0"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/triage?ids={a},{b}&i=0"
    db.expire_all()
    assert db.get(Item, a) is None
    assert "<h2>B</h2>" in client.get(r.headers["location"]).text


def test_cannot_triage_other_users_albums(client, db):
    other = User(username="other", password_hash="x")
    db.add(other)
    db.commit()
    theirs = Item(user_id=other.id, title="Theirs")
    db.add(theirs)
    db.commit()
    for url in ("vibes", "genres", "refine", "delete"):
        assert client.post(f"/triage/{theirs.id}/{url}").status_code == 404


def test_album_form_marks_genres_checked(client, db):
    client.post("/albums", data={"title": "Hand made", "genres": "folk"})
    assert db.query(Item).one().genres_checked


def test_refine_copes_with_album_removed_meanwhile(client, make_album, monkeypatch):
    a = make_album("Gone").id

    async def lookup(title, artist):
        with SessionLocal() as other:
            other.delete(other.get(Item, a))
            other.commit()
        return {"genres": ["jazz"]}

    monkeypatch.setattr(musicbrainz, "find_details", lookup)
    assert client.post(f"/triage/{a}/refine").status_code == 200


def test_new_vibe_is_made_and_ticked(client, make_album, make_vibe, user, db):
    fall = make_vibe("fall")
    a = make_album("A")
    r = client.post(f"/triage/{a.id}/new-vibe", data={"name": "  Late   night ", "color": "#123456", "vibes": [fall.id]})
    assert r.status_code == 200 and "late night" not in r.text and "Late night" in r.text
    db.expire_all()
    item = db.get(Item, a.id)
    assert sorted(v.name for v in item.vibes) == ["Late night", "fall"]
    assert next(v for v in item.vibes if v.name == "Late night").color == "#123456"


def test_new_vibe_with_an_existing_name_reuses_it(client, make_album, make_vibe, db):
    make_vibe("winter")
    a = make_album("A")
    client.post(f"/triage/{a.id}/new-vibe", data={"name": "WINTER", "color": "#123456"})
    db.expire_all()
    assert [v.name for v in db.get(Item, a.id).vibes] == ["winter"]
    assert db.query(Vibe).filter(Vibe.name.ilike("winter")).count() == 1


def test_new_vibe_colour_is_one_not_in_use():
    taken = [Vibe(name=str(i), color=c) for i, c in enumerate(main.VIBE_PALETTE[:3])]
    assert main._unused_color(taken) == main.VIBE_PALETTE[3]
    everything = [Vibe(name=str(i), color=c) for i, c in enumerate(main.VIBE_PALETTE)]
    assert main._unused_color(everything) in main.VIBE_PALETTE
