import base64
import json

from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from app import auth, invites
from app.config import SECRET_KEY
from app.main import app
from app.models import User


def _register(c, name, code=None, password="password1"):
    data = {"username": name, "password": password, "password2": password}
    if code is not None:
        data["code"] = code
    return c.post("/register", data=data)


def _logged_in(c):
    return c.get("/account", follow_redirects=False).status_code == 200


def test_deleted_users_cookie_doesnt_open_the_next_account(client, db):
    bob = TestClient(app)
    _register(bob, "bob", invites.create(db).code)
    bob_id = db.query(User).filter_by(username="bob").one().id
    client.post(f"/admin/users/{bob_id}/delete")
    carol = TestClient(app)
    _register(carol, "carol", invites.create(db).code)
    assert db.query(User).filter_by(username="carol").one().id == bob_id  # SQLite reused the id
    assert not _logged_in(bob)
    assert _logged_in(carol)


def test_password_reset_signs_out_old_sessions(client, db):
    friend = TestClient(app)
    _register(friend, "friend", invites.create(db).code)
    friend_id = db.query(User).filter_by(username="friend").one().id
    client.post(f"/admin/users/{friend_id}/reset-password")
    assert not _logged_in(friend)


def test_password_change_keeps_this_session_and_ends_others(client):
    other = TestClient(app)
    other.post("/login", data={"username": "tester", "password": "password1"})
    assert _logged_in(other)
    r = client.post("/account/password", data={"current": "password1", "password": "newpass12", "password2": "newpass12"})
    assert "Password changed" in r.text
    assert _logged_in(client) and not _logged_in(other)


def _session_cookie(data):
    return TimestampSigner(SECRET_KEY).sign(base64.b64encode(json.dumps(data).encode())).decode()


def test_cookie_with_only_a_user_id_is_rejected(client, user):
    """Sessions from before the check existed held just the id; they have to log in again."""
    for data, ok in (({"user_id": user.id}, False),
                     ({"user_id": user.id, "check": auth._session_check(user)}, True)):
        c = TestClient(app)
        c.cookies.set("session", _session_cookie(data))
        assert _logged_in(c) == ok


def test_with_admin_usernames_only_that_name_skips_the_code(monkeypatch):
    monkeypatch.setattr(auth, "ADMIN_USERNAMES", {"ori"})
    c = TestClient(app)
    page = c.get("/register").text
    assert 'name="code"' in page and "needs no code" in page
    r = _register(c, "stranger")
    assert r.status_code == 400 and "invite code" in r.text
    r = _register(c, "Ori")
    assert r.status_code == 200 and "Your library is empty" in r.text
    assert c.get("/admin").status_code == 200


def test_invite_cant_claim_a_listed_admin_name(client, db, monkeypatch):
    monkeypatch.setattr(auth, "ADMIN_USERNAMES", {"tester", "second-admin"})
    r = _register(TestClient(app), "second-admin", invites.create(db).code)
    assert r.status_code == 400 and "taken" in r.text


def test_register_doesnt_reveal_names_without_a_valid_code(client):
    r = _register(TestClient(app), "tester", "AAAA-BBBB-CCCC")
    assert r.status_code == 400 and "taken" not in r.text and "invite code" in r.text


def test_login_for_unknown_user_still_hashes(client, monkeypatch):
    calls = []
    real = auth.verify_password
    monkeypatch.setattr(auth, "verify_password", lambda p, h: calls.append(h) or real(p, h))
    TestClient(app).post("/login", data={"username": "nobody", "password": "whatever1"})
    assert calls


def _log_in(c, **extra):
    return c.post("/login", data={"username": "tester", "password": "password1", **extra}, follow_redirects=False)


def test_login_goes_back_to_the_page_asked_for(user, make_album):
    album = make_album("A")
    c = TestClient(app)
    r = c.get(f"/albums/{album.id}?x=1", follow_redirects=False)
    assert r.headers["location"] == f"/login?next=%2Falbums%2F{album.id}%3Fx%3D1"
    assert f'name="next" value="/albums/{album.id}?x=1"' in c.get(r.headers["location"]).text
    assert _log_in(c, password="wrong", next="/stats").status_code == 401
    assert 'name="next" value="/stats"' in _log_in(c, password="wrong", next="/stats").text  # kept on retry
    assert _log_in(c, next=f"/albums/{album.id}?x=1").headers["location"] == f"/albums/{album.id}?x=1"


def test_login_page_itself_needs_no_next(user):
    c = TestClient(app)
    assert c.get("/", follow_redirects=False).headers["location"] == "/login"
    assert _log_in(c).headers["location"] == "/"


def test_background_request_goes_back_to_the_page_it_came_from(user, make_album):
    album = make_album("A")
    c = TestClient(app)
    r = c.post(f"/triage/{album.id}/vibes", headers={"HX-Request": "true",
                                                      "HX-Current-URL": f"http://testserver/triage/{album.id}"})
    assert r.headers["HX-Redirect"] == f"/login?next=%2Ftriage%2F{album.id}"
    r = c.post(f"/triage/{album.id}/vibes", headers={"HX-Request": "true",
                                                      "HX-Current-URL": f"http://elsewhere.example/triage/{album.id}"})
    assert r.headers["HX-Redirect"] == "/login"


def test_login_never_sends_you_off_site(user):
    for next in ("//evil.example", "/\\evil.example", "/\t/evil.example", "https://evil.example", "evil"):
        c = TestClient(app)
        assert 'name="next"' not in c.get("/login", params={"next": next}).text
        assert _log_in(c, next=next).headers["location"] == "/", next
