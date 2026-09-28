from app.auth import verify_password
from app.models import User


def test_account_page_linked_from_taskbar(client):
    assert 'href="/account"' in client.get("/").text
    assert "Change password" in client.get("/account").text


def test_change_password(client, user, db):
    r = client.post("/account/password", data={"current": "wrong-one", "password": "newpass12", "password2": "newpass12"})
    assert r.status_code == 400 and "Current password is wrong" in r.text
    r = client.post("/account/password", data={"current": "password1", "password": "short", "password2": "short"})
    assert r.status_code == 400 and "at least 8" in r.text
    r = client.post("/account/password", data={"current": "password1", "password": "newpass12", "password2": "newpass12"})
    assert r.status_code == 200 and "Password changed" in r.text
    db.expire_all()
    assert verify_password("newpass12", db.get(User, user.id).password_hash)
