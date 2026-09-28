import asyncio
import time

import httpx
from fastapi.testclient import TestClient

from app import ratelimit
from app.main import app


def _login(c, password, username="tester"):
    return c.post("/login", data={"username": username, "password": password})


def test_login_locks_after_repeated_failures(client):
    c = TestClient(app)
    for _ in range(ratelimit.login_by_user.limit):
        assert _login(c, "wrong-pass").status_code == 401
    r = _login(c, "password1")  # even the right password waits out the lock
    assert r.status_code == 429 and "Too many attempts" in r.text
    ratelimit.login_by_user.clear()
    assert _login(c, "password1").status_code == 200


def test_login_limit_per_ip_covers_many_usernames(client):
    c = TestClient(app)
    for i in range(ratelimit.login_by_ip.limit):
        _login(c, "x", username=f"guess{i}")
    assert _login(c, "password1").status_code == 429


def test_successful_login_clears_user_failures(client):
    c = TestClient(app)
    for _ in range(ratelimit.login_by_user.limit - 1):
        _login(c, "wrong-pass")
    assert _login(c, "password1").status_code == 200
    assert _login(c, "wrong-pass").status_code == 401  # count started over


def test_signup_attempts_limited(client):
    c = TestClient(app)
    for i in range(ratelimit.signup_by_ip.limit - 1):  # the client fixture's sign-up counted too
        c.post("/register", data={"username": f"u{i}", "password": "password1", "password2": "password1",
                                  "code": "NOPE-NOPE-NOPE"})
    r = c.post("/register", data={"username": "late", "password": "password1", "password2": "password1"})
    assert r.status_code == 429


def test_limiter_window_expires(monkeypatch):
    limiter = ratelimit.Limiter(limit=2, window=60)
    now = [1000.0]
    monkeypatch.setattr(ratelimit.time, "monotonic", lambda: now[0])
    limiter.hit("k")
    limiter.hit("k")
    assert limiter.blocked("k") and not limiter.blocked("other")
    now[0] += 61
    assert not limiter.blocked("k")


def test_throttle_spaces_calls_and_refuses_long_queues():
    async def run():
        throttle = ratelimit.Throttle(0.05, max_wait=0.12)
        start = time.monotonic()
        stamps = []

        async def call():
            await throttle.wait()
            stamps.append(time.monotonic() - start)

        await asyncio.gather(*(call() for _ in range(3)))
        assert stamps[1] - stamps[0] >= 0.045 and stamps[2] - stamps[1] >= 0.045
        results = await asyncio.gather(*(call() for _ in range(6)), return_exceptions=True)
        assert any(isinstance(r, httpx.TimeoutException) for r in results)

    asyncio.run(run())
