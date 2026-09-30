import re
import sqlite3

from fastapi.testclient import TestClient

from app.config import DB_PATH
from app.main import app
from app.models import Vibe


def test_login_required():
    r = TestClient(app).get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_library_filters(client, make_album, make_vibe):
    winter = make_vibe("winter")
    make_album("Long Season", "Fishmans", 1996, vibes=[winter], genres=["dream pop"])
    make_album("Is This It", "The Strokes", 2001, genres=["rock"])
    assert "Long Season" in client.get("/").text
    r = client.get("/", params={"vibe": winter.id})
    assert "Long Season" in r.text and "Is This It" not in r.text
    r = client.get("/", params={"genre": "rock"})
    assert "Is This It" in r.text and "Long Season" not in r.text
    r = client.get("/", params={"year_min": "2000"})
    assert "Is This It" in r.text and "Long Season" not in r.text


def test_other_users_albums_hidden(client, make_album, db):
    from app.models import Item, User
    other = User(username="other", password_hash="x")
    db.add(other)
    db.commit()
    db.add(Item(user_id=other.id, title="Secret Album"))
    db.commit()
    assert "Secret Album" not in client.get("/").text


def test_healthz_and_security_headers():
    r = TestClient(app).get("/healthz")
    assert r.status_code == 200 and r.text == "ok"
    assert r.headers["X-Content-Type-Options"] == "nosniff" and r.headers["X-Frame-Options"] == "DENY"


def test_oversized_cover_upload_is_dropped(client, db):
    from app import covers
    from app.models import Item
    big = b"\xff\xd8" + b"0" * (covers.MAX_BYTES + 10)
    client.post("/albums", data={"title": "Huge"}, files={"cover": ("c.jpg", big, "image/jpeg")})
    assert db.query(Item).filter_by(title="Huge").one().cover_file is None


def _others_can_write():
    """Whether another connection could write to the database right now."""
    conn = sqlite3.connect(DB_PATH, timeout=0.5)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.rollback()
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        conn.close()


def test_adding_an_album_doesnt_lock_the_database_while_its_cover_downloads(client, db, monkeypatch):
    from app import covers
    from app.models import Item
    seen = []

    async def download(url):
        seen.append(_others_can_write())
        return None
    monkeypatch.setattr(covers, "download", download)
    client.post("/albums", data={"title": "New", "genres": "brand new genre",
                                 "cover_url": "https://coverartarchive.org/release/x/front"})
    assert seen == [True]
    assert [g.name for g in db.query(Item).filter_by(title="New").one().genres] == ["brand new genre"]


def test_editing_an_album_doesnt_lock_the_database_while_its_cover_uploads(client, db, make_album, monkeypatch):
    from starlette.datastructures import UploadFile
    album = make_album("Old")
    seen = []
    real_read = UploadFile.read

    async def read(self, size=-1):
        seen.append(_others_can_write())
        return await real_read(self, size)
    monkeypatch.setattr(UploadFile, "read", read)
    client.post(f"/albums/{album.id}", data={"title": "Renamed", "genres": "brand new genre"},
                files={"cover": ("c.png", b"\x89PNG", "image/png")})
    assert seen == [True]
    db.refresh(album)
    assert album.title == "Renamed" and album.cover_file


def test_new_account_lands_on_library_with_season_vibes(db):
    r = TestClient(app).post("/register", data={"username": "new", "password": "password1", "password2": "password1"})
    assert r.url.path == "/" and "Your library is empty" in r.text
    assert [v.name for v in db.query(Vibe).order_by(Vibe.id)] == ["winter", "spring", "summer", "fall"]


def test_random_order_holds_for_a_seed(client, make_album):
    titles = [f"Album {i}" for i in range(12)]
    for t in titles:
        make_album(t)

    def order(**params):
        html = client.get("/", params=params, headers={"HX-Request": "true"}).text
        return re.findall(r'class="card-title"[^>]*>([^<]+)<', html)

    first = order(order="random", seed="123")
    assert sorted(first) == sorted(titles)
    assert first == order(order="random", seed="123")  # same seed, same order
    assert first != order(order="random", seed="456")
    assert order(order="random", seed="nonsense")  # a bad seed still lists everything, shuffled
