from app.models import Genre, Item, User


def genres_of(db, item):
    db.expire_all()
    return sorted(g.name for g in db.get(Item, item.id).genres)


def gid(db, name):
    return db.query(Genre).filter_by(name=name).one().id


def test_rename(client, make_album, db):
    a = make_album("A", genres=["alt rock", "indie"])
    client.post("/genres/merge", data={"genre": gid(db, "alt rock"), "name": " Alternative  Rock "})
    assert genres_of(db, a) == ["alternative rock", "indie"]
    assert db.query(Genre).filter_by(name="alt rock").one_or_none() is None  # unused genre cleaned up


def test_rename_into_existing_merges(client, make_album, db):
    a = make_album("A", genres=["hip-hop", "hip hop"])
    b = make_album("B", genres=["hip-hop"])
    client.post("/genres/merge", data={"genre": gid(db, "hip-hop"), "name": "hip hop"})
    assert genres_of(db, a) == ["hip hop"] and genres_of(db, b) == ["hip hop"]


def test_merge_duplicates_group(client, make_album, db):
    a = make_album("A", genres=["hip-hop"])
    b = make_album("B", genres=["hip hop"])
    assert "Possible Duplicates" in client.get("/genres").text
    client.post("/genres/merge", data={"genre": [gid(db, "hip-hop"), gid(db, "hip hop")], "name": "hip hop"})
    assert genres_of(db, a) == ["hip hop"] and genres_of(db, b) == ["hip hop"]
    assert "Possible Duplicates" not in client.get("/genres").text


def test_delete_keeps_album(client, make_album, db):
    a = make_album("A", genres=["rock", "pop"])
    client.post(f"/genres/{gid(db, 'pop')}/delete")
    assert genres_of(db, a) == ["rock"]


def test_only_touches_own_albums(client, make_album, db):
    mine = make_album("Mine", genres=["rock"])
    other = User(username="other", password_hash="x")
    db.add(other)
    db.commit()
    theirs = Item(user_id=other.id, title="Theirs", genres=[db.query(Genre).filter_by(name="rock").one()])
    db.add(theirs)
    db.commit()
    client.post("/genres/merge", data={"genre": gid(db, "rock"), "name": "rock and roll"})
    assert genres_of(db, mine) == ["rock and roll"] and genres_of(db, theirs) == ["rock"]
