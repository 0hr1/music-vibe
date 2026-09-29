import sqlite3

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from app import covers, main, migrations
from app.main import app
from app.models import Item


def test_out_of_range_years_are_dropped(client, db):
    for year in ("-1000000000000000", "10000000000000000000000000", "3000", "999"):
        assert client.post("/albums", data={"title": f"Y{year[:5]}", "year": year}).status_code == 200
    assert {i.year for i in db.query(Item)} == {None}
    client.post("/albums", data={"title": "Loveless", "year": " 1991 "})
    assert db.query(Item).filter_by(title="Loveless").one().year == 1991


def test_edit_drops_out_of_range_year(client, db, make_album):
    album = make_album("Loveless", year=1991)
    client.post(f"/albums/{album.id}", data={"title": "Loveless", "year": "-5"})
    db.expire_all()
    assert db.get(Item, album.id).year is None


def test_huge_filter_numbers_dont_500(client):
    assert client.get("/?year_min=" + "9" * 25).status_code == 200
    assert client.get("/?year_max=-" + "9" * 25).status_code == 200


def test_stats_stay_bounded_with_bad_old_data(client, make_album):
    make_album("Old", year=-10**15)  # written before years were checked
    make_album("New", year=10**15)
    assert client.get("/stats").status_code == 200


def test_oversized_body_refused_before_login():
    too_big = b"x" * (main.MAX_BODY + 1)
    r = TestClient(app).post("/albums", content=too_big, headers={"Content-Type": "application/octet-stream"})
    assert r.status_code == 413


def test_oversized_body_without_length_refused(client):
    def chunks():
        for _ in range(main.MAX_BODY // (1024 * 1024) + 2):
            yield b"x" * (1024 * 1024)
    r = client.post("/albums/bulk", content=chunks(), headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code == 413


def test_normal_cover_upload_still_fits(client, db):
    png = b"\x89PNG\r\n\x1a\n" + b"\0" * (covers.MAX_BYTES - 100)
    r = client.post("/albums", data={"title": "Big cover"}, files={"cover": ("c.png", png, "image/png")})
    assert r.status_code == 200


def test_upgrade_brings_an_old_database_up_to_date(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:  # the users table as it was before any migrations
        conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username VARCHAR(64) UNIQUE, "
                     "password_hash VARCHAR(255), created_at DATETIME)")
    engine = create_engine(f"sqlite:///{path}")
    migrations.upgrade(engine)
    with engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA user_version").scalar() == len(migrations.STEPS)
        conn.execute(text("INSERT INTO users (username, password_hash) VALUES ('Bob', 'x')"))
        with pytest.raises(IntegrityError):
            conn.execute(text("INSERT INTO users (username, password_hash) VALUES ('bob', 'x')"))
    migrations.upgrade(engine)  # running again is a no-op


def test_new_database_starts_at_the_latest_version(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'new.db'}")
    migrations.upgrade(engine)
    with engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA user_version").scalar() == len(migrations.STEPS)


def test_prefill_ignores_junk_vibe_ids(client, monkeypatch):
    from app import deezer

    async def get_album(album_id):
        return {"external_id": "deezer:1", "title": "T", "artist": "A", "year": None, "genres": [], "cover_url": ""}

    monkeypatch.setattr(deezer, "get_album", get_album)
    r = client.get("/albums/prefill", params={"source": "deezer", "id": "1", "vibes": ["²", "9" * 30, "x"]})
    assert r.status_code == 200
