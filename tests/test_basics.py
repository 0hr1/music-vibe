from fastapi.testclient import TestClient

from app.main import app


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
