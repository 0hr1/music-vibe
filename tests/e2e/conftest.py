"""Browser tests: a real (headless) Chromium drives the app, so the JavaScript and htmx get tested too.

The app runs in a background thread of the test process, so it shares the scratch database with
the fixtures in tests/conftest.py and monkeypatching (e.g. faking Deezer) reaches it."""

import socket
import threading
import time

import pytest
import uvicorn

from app.main import app


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def base_url():
    """Overrides pytest-base-url's fixture, so `page.goto("/")` hits this server."""
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("test server didn't start")
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture
def logged_in(page, user):
    """The page, logged in as the `tester` account the `client` fixture registered."""
    page.set_default_timeout(5000)  # fail fast; nothing here should take long
    page.goto("/login")
    page.fill("input[name=username]", "tester")
    page.fill("input[name=password]", "password1")
    page.click("button[type=submit]")
    page.wait_for_url("**/")
    return page
