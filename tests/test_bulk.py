from app.models import Item


def test_add_and_remove_vibe(client, make_album, make_vibe, db):
    winter, happy = make_vibe("winter"), make_vibe("happy")
    a = make_album("A", vibes=[winter])
    b = make_album("B")
    r = client.post("/albums/bulk", data={"action": "add_vibe", "bulk_vibe": winter.id, "sel": [a.id, b.id]})
    assert r.status_code == 200 and "Added “winter” to 1 album." in r.text
    assert r.text.count("checked") == 2  # selection is kept for the next action
    db.expire_all()
    assert [v.name for v in db.get(Item, b.id).vibes] == ["winter"]
    r = client.post("/albums/bulk", data={"action": "remove_vibe", "bulk_vibe": winter.id, "sel": [a.id, b.id]})
    assert "Removed “winter” from 2 albums." in r.text
    db.expire_all()
    assert db.get(Item, a.id).vibes == [] and happy.id


def test_bulk_keeps_library_filters(client, make_album, make_vibe):
    winter = make_vibe("winter")
    a = make_album("Alpha", year=1990)
    make_album("Beta", year=2010)
    r = client.post("/albums/bulk", data={"action": "add_vibe", "bulk_vibe": winter.id, "sel": [a.id],
                                          "year_max": "2000"})
    assert "Alpha" in r.text and "Beta" not in r.text


def test_delete_selected(client, make_album, db):
    a, b, c = make_album("A"), make_album("B"), make_album("C")
    r = client.post("/albums/bulk", data={"action": "delete", "sel": [a.id, b.id]})
    assert "Deleted 2 albums." in r.text
    assert [i.title for i in db.query(Item).all()] == ["C"]


def test_cannot_touch_other_users_albums_or_vibes(client, db, make_vibe):
    from app.models import User, Vibe
    other = User(username="other", password_hash="x")
    db.add(other)
    db.commit()
    theirs = Item(user_id=other.id, title="Theirs")
    their_vibe = Vibe(user_id=other.id, name="theirs")
    db.add_all([theirs, their_vibe])
    db.commit()
    client.post("/albums/bulk", data={"action": "delete", "sel": [theirs.id]})
    assert db.get(Item, theirs.id) is not None
    mine = make_vibe("mine")
    r = client.post("/albums/bulk", data={"action": "add_vibe", "bulk_vibe": their_vibe.id, "sel": [theirs.id]})
    assert r.status_code == 400
    assert mine.id
