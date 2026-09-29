import asyncio
import time

import httpx
from fastapi.testclient import TestClient

from app import main, ratelimit
from app.main import app


def _login(c, password, username="tester"):
    return c.post("/login", data={"username": username, "password": password})


def test_login_locks_after_repeated_failures(client):
    c = TestClient(app)
    for _ in range(ratelimit.login_by_user_ip.limit):
        assert _login(c, "wrong-pass").status_code == 401
    r = _login(c, "password1")  # even the right password waits out the lock, from this address
    assert r.status_code == 429 and "Too many attempts" in r.text
    ratelimit.login_by_user_ip.clear()
    assert _login(c, "password1").status_code == 200


def test_someone_elses_failures_dont_lock_out_the_owner(client, monkeypatch):
    monkeypatch.setattr(main, "ON_FLY", True)
    attacker = TestClient(app, headers={"Fly-Client-IP": "203.0.113.9"})
    for _ in range(ratelimit.login_by_user_ip.limit):
        _login(attacker, "wrong-pass")
    assert _login(attacker, "password1").status_code == 429
    owner = TestClient(app, headers={"Fly-Client-IP": "198.51.100.7"})
    assert _login(owner, "password1").status_code == 200


def test_many_addresses_still_hit_a_per_account_ceiling(client, monkeypatch):
    monkeypatch.setattr(ratelimit.login_by_user, "limit", 3)
    monkeypatch.setattr(main, "ON_FLY", True)
    for i in range(3):
        _login(TestClient(app, headers={"Fly-Client-IP": f"203.0.113.{i}"}), "wrong-pass")
    assert _login(TestClient(app, headers={"Fly-Client-IP": "203.0.113.99"}), "password1").status_code == 429


def test_login_limit_per_ip_covers_many_usernames(client):
    c = TestClient(app)
    for i in range(ratelimit.login_by_ip.limit):
        _login(c, "x", username=f"guess{i}")
    assert _login(c, "password1").status_code == 429


def test_spoofed_forwarded_for_doesnt_dodge_the_ip_limit(client, monkeypatch):
    monkeypatch.setattr(main, "ON_FLY", True)
    for i in range(ratelimit.login_by_ip.limit):  # as Fly passes it on: client's value first, real IP last
        TestClient(app, headers={"X-Forwarded-For": f"10.0.0.{i}, 203.0.113.9",
                                 "Fly-Client-IP": "203.0.113.9"}).post(
            "/login", data={"username": f"guess{i}", "password": "x"})
    r = TestClient(app, headers={"X-Forwarded-For": "10.9.9.9", "Fly-Client-IP": "203.0.113.9"}).post(
        "/login", data={"username": "tester", "password": "password1"})
    assert r.status_code == 429


def test_successful_login_clears_user_failures(client):
    c = TestClient(app)
    for _ in range(ratelimit.login_by_user_ip.limit - 1):
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


def test_limiter_forgets_oldest_keys_past_its_cap():
    limiter = ratelimit.Limiter(limit=1, window=60, max_keys=3)
    for k in "abcd":
        limiter.hit(k)
    assert not limiter.blocked("a") and limiter.blocked("d")
    assert len(limiter._hits) == 3


def test_login_key_ignores_huge_usernames(client):
    TestClient(app).post("/login", data={"username": "x" * 100_000, "password": "nope"})
    assert all(len(k) < 200 for k in ratelimit.login_by_user._hits)


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
