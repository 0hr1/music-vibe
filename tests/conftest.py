import os
import tempfile

# The app reads its settings at import time, so point it at a scratch data dir first.
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="music-vibe-test-")
os.environ["SPOTIFY_CLIENT_ID"] = ""
os.environ["SPOTIFY_CLIENT_SECRET"] = ""

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import ratelimit  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Genre, Item, User, Vibe  # noqa: E402

try:
    import playwright  # noqa: F401
except ImportError:  # browser tests need requirements-dev.txt; the rest run without them
    collect_ignore = ["e2e"]


def pytest_collection_modifyitems(items):
    """Browser tests go last: Playwright leaves an event loop running in the main thread, which
    breaks later tests that call asyncio.run()."""
    items.sort(key=lambda item: "/e2e/" in item.nodeid)


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    ratelimit.clear_all()
    yield


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


@pytest.fixture
def client():
    """A client logged in as a freshly registered user."""
    c = TestClient(app)
    r = c.post("/register", data={"username": "tester", "password": "password1", "password2": "password1"})
    assert r.status_code == 200
    return c


@pytest.fixture
def user(client, db):
    return db.query(User).filter_by(username="tester").one()


@pytest.fixture
def make_album(db, user):
    def make(title, creator="", year=None, vibes=(), genres=(), **kw):
        item = Item(user_id=user.id, title=title, creator=creator, year=year, **kw)
        item.vibes = list(vibes)
        item.genres = [db.query(Genre).filter_by(name=g).one_or_none() or Genre(name=g) for g in genres]
        db.add(item)
        db.commit()
        return item
    return make


@pytest.fixture
def make_vibe(db, user):
    def make(name, color="#8a5cd6"):
        # New accounts already have the season vibes, so reuse one by that name.
        vibe = db.query(Vibe).filter_by(user_id=user.id, name=name).one_or_none()
        if not vibe:
            vibe = Vibe(user_id=user.id, name=name, color=color)
            db.add(vibe)
            db.commit()
        return vibe
    return make
