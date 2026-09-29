from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app import invites, manage
from app.auth import verify_password
from app.main import app
from app.models import InviteCode, Item, User


def _register(c, name, code=None):
    data = {"username": name, "password": "password1", "password2": "password1"}
    if code is not None:
        data["code"] = code
    return c.post("/register", data=data)


def test_first_account_needs_no_code():
    r = _register(TestClient(app), "first")
    assert r.status_code == 200 and "Your library is empty" in r.text


def test_later_accounts_need_a_valid_code(client, db):
    c = TestClient(app)
    assert 'name="code"' in c.get("/register").text
    r = _register(c, "nocode")
    assert r.status_code == 400 and "invite code" in r.text
    r = _register(c, "badcode", "AAAA-BBBB-CCCC")
    assert r.status_code == 400
    invite = invites.create(db, max_uses=1)
    r = _register(c, "friend", invite.code.lower().replace("-", " "))  # typed sloppily still works
    assert r.status_code == 200 and "Your library is empty" in r.text
    r = _register(TestClient(app), "second", invite.code)
    assert r.status_code == 400 and "used up" in r.text
    db.expire_all()
    assert db.get(InviteCode, invite.id).uses == 1
    assert db.query(User).filter_by(username="second").count() == 0


def test_failed_signup_doesnt_use_up_the_code(client, db):
    invite = invites.create(db, max_uses=1)
    r = TestClient(app).post("/register", data={"username": "x", "password": "password1", "password2": "password1",
                                                "code": invite.code})
    assert r.status_code == 400
    db.expire_all()
    assert db.get(InviteCode, invite.id).uses == 0


def test_expired_code_rejected(client, db):
    invite = invites.create(db, max_uses=None)
    invite.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.commit()
    assert _register(TestClient(app), "late", invite.code).status_code == 400


def test_register_link_prefills_code(client, db):
    invite = invites.create(db)
    assert f'value="{invite.code}"' in TestClient(app).get(f"/register?code={invite.code}").text


def test_admin_page_is_only_for_admins(client, db):
    assert client.get("/admin").status_code == 200  # first account is the admin
    assert 'href="/admin"' in client.get("/account").text
    other = TestClient(app)
    _register(other, "friend", invites.create(db).code)
    assert other.get("/admin").status_code == 404
    assert other.post("/admin/invites", data={"uses": "1", "days": "1"}).status_code == 404
    assert 'href="/admin"' not in other.get("/account").text


def test_admin_creates_and_deletes_invites(client, db):
    client.post("/admin/invites", data={"uses": "", "days": "", "note": "band"})
    invite = db.query(InviteCode).one()
    assert invite.max_uses is None and invite.expires_at is None and invite.note == "band"
    assert f"?code={invite.code}" in client.get("/admin").text
    client.post(f"/admin/invites/{invite.id}/delete")
    assert db.query(InviteCode).count() == 0


def test_admin_resets_password_and_deletes_user(client, db):
    _register(TestClient(app), "friend", invites.create(db).code)
    friend_id = db.query(User).filter_by(username="friend").one().id
    db.add(Item(user_id=friend_id, title="Their Album"))
    db.commit()
    r = client.post(f"/admin/users/{friend_id}/reset-password")
    temp = r.text.split('temporary password is <code class="secret">')[1].split("<")[0]
    db.expire_all()
    assert verify_password(temp, db.get(User, friend_id).password_hash)
    assert r.url.path == "/admin"  # so a reload doesn't reset it again...
    assert temp not in client.get("/admin").text  # ...and it's shown once
    client.post(f"/admin/users/{friend_id}/delete")
    db.expire_all()
    assert db.query(User).filter_by(id=friend_id).count() == 0 and db.query(Item).count() == 0


def test_admin_cant_delete_themselves(client, user):
    assert client.post(f"/admin/users/{user.id}/delete").status_code == 404


def test_manage_cli(client, capsys, db):
    assert manage.main(["invite", "--uses", "0", "--days", "0"]) == 0
    code = capsys.readouterr().out.strip()
    assert invites.find_usable(db, code).max_uses is None
    assert manage.main(["reset-password", "TESTER"]) == 0
    temp = capsys.readouterr().out.strip().split(": ")[1]
    db.expire_all()
    assert verify_password(temp, db.query(User).filter_by(username="tester").one().password_hash)
    assert manage.main(["reset-password", "nobody"]) == 1
