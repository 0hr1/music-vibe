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


def _location(r):
    assert r.status_code == 303, r.status_code
    return r.headers["location"]


def test_batch_walks_the_import_in_order(client, make_album):
    a, b = make_album("Alpha", "Artist A", 1999), make_album("Beta", "Artist B")
    batch = f"{a.id}-{b.id}"
    assert _location(client.get(f"/triage?batch={batch}", follow_redirects=False)) == f"/triage/{a.id}?batch={batch}"
    r = client.get(f"/triage/{a.id}?batch={batch}")
    assert "Alpha" in r.text and "Album <b>1</b> of 2" in r.text and "0</b> tagged" in r.text
    assert f'href="/triage/{b.id}?batch={batch}"' in r.text  # next
    r = client.get(f"/triage/{b.id}?batch={batch}")
    assert "Beta" in r.text and "Finish →" in r.text and f'href="/triage/done?batch={batch}"' in r.text
    r = client.get(f"/triage/done?batch={batch}")
    assert "all 2 albums from this import" in r.text and "Tag all 2 without vibes" in r.text


def test_batch_resumes_at_the_first_album_without_vibes(client, make_album, make_vibe):
    winter = make_vibe("winter")
    a, b, c = make_album("A", vibes=[winter]), make_album("B"), make_album("C")
    r = client.get(f"/triage?batch={a.id}-{c.id}", follow_redirects=False)
    assert _location(r) == f"/triage/{b.id}?batch={a.id}-{c.id}"
    r = client.get(f"/triage/{b.id}?batch={a.id}-{c.id}")
    assert "Album <b>2</b> of 3" in r.text and "1</b> tagged" in r.text  # tagged albums stay in a batch
    assert f'href="/triage/{a.id}?batch={a.id}-{c.id}"' in r.text  # back


def test_untagged_goes_through_albums_without_vibes(client, make_album, make_vibe):
    winter = make_vibe("winter")
    a, b, c = make_album("A"), make_album("B", vibes=[winter]), make_album("C")
    assert _location(client.get("/triage", follow_redirects=False)) == f"/triage/{a.id}"
    r = client.get(f"/triage/{a.id}")
    assert "<b data-left>2</b> left" in r.text
    assert f'href="/triage/{c.id}"' in r.text  # skips B, which has vibes
    assert "data-history hidden" in r.text  # nothing before it: Back only via the browser's history
    r = client.get(f"/triage/{c.id}")
    assert "Finish →" in r.text and 'href="/triage/done"' in r.text
    r = client.get("/triage/done")
    assert "2 albums still have" in r.text and "Start again →" in r.text


def test_nothing_to_tag(client, make_album, make_vibe):
    make_album("A", vibes=[make_vibe("winter")])
    assert _location(client.get("/triage", follow_redirects=False)) == "/triage/done"
    assert "Every album has vibes." in client.get("/triage/done").text


def test_old_long_links_still_work(client, make_album):
    a, b, c = make_album("A"), make_album("B"), make_album("C")
    r = client.get(f"/triage?ids={a.id},{b.id},{c.id}&i=1", follow_redirects=False)
    assert _location(r) == f"/triage/{b.id}?batch={a.id}-{c.id}"


def test_other_users_albums_are_never_shown(client, make_album, db):
    other = User(username="other", password_hash="x")
    db.add(other)
    db.commit()
    theirs = Item(user_id=other.id, title="Theirs")
    db.add(theirs)
    db.commit()
    mine = make_album("Mine")
    lo, hi = sorted((theirs.id, mine.id))
    r = client.get(f"/triage/{mine.id}?batch={lo}-{hi}")
    assert "Album <b>1</b> of 1" in r.text and "Theirs" not in r.text
    assert "Theirs" not in client.get(f"/triage/{theirs.id}").text  # moves on instead


def test_library_offers_to_tag_albums_without_vibes(client, make_album, make_vibe):
    make_album("A", vibes=[make_vibe("winter")])
    assert "untagged" not in client.get("/").text
    make_album("B")
    make_album("C")
    assert "Tag 2 untagged" in client.get("/").text


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
    r = client.get(f"/triage/{a.id}")
    assert f'hx-post="/triage/{a.id}/refine"' in r.text  # the card itself
    assert f'hx-post="/triage/{b.id}/refine" hx-trigger="load delay' in r.text
    assert "delay:500ms" not in client.get(f"/triage/{b.id}").text  # last album: nothing to prefetch


def test_remove_moves_on_to_the_next_album(client, make_album, db):
    a, b = make_album("A").id, make_album("B").id
    r = client.post(f"/triage/{a}/delete", data={"batch": f"{a}-{b}"}, follow_redirects=False)
    assert _location(r) == f"/triage/{b}?batch={a}-{b}"
    db.expire_all()
    assert db.get(Item, a) is None
    assert "<h2>B</h2>" in client.get(r.headers["location"]).text
    # the removed album's page (e.g. reached with the browser's back button) moves on too
    assert _location(client.get(f"/triage/{a}?batch={a}-{b}", follow_redirects=False)) == f"/triage/{b}?batch={a}-{b}"


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
