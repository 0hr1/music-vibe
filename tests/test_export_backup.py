import csv
import io
import os
import sqlite3
import time

from app import backup


def test_csv_export(client, make_album, make_vibe):
    winter = make_vibe("winter")
    make_album("ロングシーズン", "Fishmans", 1996, vibes=[winter], genres=["dream pop", "dub"],
               spotify_url="https://open.spotify.com/album/x", notes='quote " and, comma')
    make_album("Kid A", "Radiohead", 2000)
    r = client.get("/export.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    assert r.text.startswith("﻿")
    rows = list(csv.DictReader(io.StringIO(r.text.lstrip("﻿"))))
    assert [row["title"] for row in rows] == ["ロングシーズン", "Kid A"]  # sorted by artist
    assert rows[0]["vibes"] == "winter" and rows[0]["genres"] == "dream pop, dub"
    assert rows[0]["notes"] == 'quote " and, comma' and rows[1]["year"] == "2000"


def test_csv_export_only_own_albums(client, db):
    from app.models import Item, User
    other = User(username="other", password_hash="x")
    db.add(other)
    db.commit()
    db.add(Item(user_id=other.id, title="Theirs"))
    db.commit()
    assert "Theirs" not in client.get("/export.csv").text


def test_backup_copies_db_and_prunes(client, make_album, monkeypatch):
    for old in backup.backups():
        old.unlink()
    make_album("Kid A", "Radiohead", 2000)
    assert backup.backup_due()
    path = backup.make_backup()
    with sqlite3.connect(path) as conn:
        assert conn.execute("select title from items").fetchall() == [("Kid A",)]
    assert not backup.backup_due()

    monkeypatch.setattr(backup, "BACKUP_KEEP", 3)
    for i in range(5):  # fake older snapshots
        fake = backup.BACKUPS_DIR / f"music_vibe-2020010{i}-000000.db"
        fake.write_bytes(b"")
        os.utime(fake, (time.time() - 10 * 86400, time.time() - 10 * 86400))
    newest = backup.make_backup()
    names = [p.name for p in backup.backups()]
    assert names == ["music_vibe-20200103-000000.db", "music_vibe-20200104-000000.db", newest.name]
