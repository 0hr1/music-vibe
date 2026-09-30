"""Seeds a big library (in a scratch data dir) and times each page: server time, SQL statements, size.

    .venv/bin/python tests/perf/measure_pages.py [albums]    (default 5000)

For a look before and after a change that might slow pages down. Timings depend on the machine,
so it prints numbers rather than asserting them; test_query_counts.py guards the part that can be
checked exactly."""
import os
import random
import statistics
import sys
import tempfile
import time
import warnings
from pathlib import Path

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="music-vibe-perf-")
os.environ["SPOTIFY_CLIENT_ID"] = ""
os.environ["SPOTIFY_CLIENT_SECRET"] = ""
os.environ["ALLOW_SIGNUP"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
warnings.filterwarnings("ignore", "Using `httpx`")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import event, insert, select  # noqa: E402

from app import auth  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Genre, Item, User, Vibe, item_genres, item_vibes  # noqa: E402

auth._N = 2**4  # cheap password hashing, as in the tests
N = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
OTHER_USERS, OTHER_N = 4, 1000
rng = random.Random(1)

Base.metadata.create_all(engine)
clients = {}
for name in ["tester"] + [f"other{i}" for i in range(OTHER_USERS)]:
    c = TestClient(app)
    r = c.post("/register", data={"username": name, "password": "password1", "password2": "password1"})
    assert r.status_code == 200, r.text
    clients[name] = c

t0 = time.perf_counter()
with SessionLocal() as db:
    genre_ids = []
    for i in range(300):
        g = Genre(name=f"genre {i}")
        db.add(g)
        db.flush()
        genre_ids.append(g.id)
    for name, count in [("tester", N)] + [(f"other{i}", OTHER_N) for i in range(OTHER_USERS)]:
        uid = db.scalar(select(User.id).where(User.username == name))
        for i in range(8):
            db.add(Vibe(user_id=uid, name=f"vibe {i}"))
        db.flush()
        vibe_ids = db.scalars(select(Vibe.id).where(Vibe.user_id == uid)).all()
        rows = [dict(user_id=uid, kind="album", title=f"Album {i} {rng.random():.6f}", creator=f"Artist {i % 800}",
                     year=rng.choice([None] + list(range(1960, 2026))), genres_checked=True) for i in range(count)]
        db.execute(insert(Item), rows)
        ids = db.scalars(select(Item.id).where(Item.user_id == uid)).all()
        iv, ig = [], []
        for item_id in ids:
            if rng.random() > 0.3:  # 30% untagged
                iv += [dict(item_id=item_id, vibe_id=v) for v in rng.sample(vibe_ids, rng.randint(1, 3))]
            ig += [dict(item_id=item_id, genre_id=g) for g in rng.sample(genre_ids, rng.randint(1, 3))]
        db.execute(insert(item_vibes), iv)
        db.execute(insert(item_genres), ig)
    db.commit()
    mine = db.scalars(select(Item.id).where(Item.user_id == db.scalar(select(User.id).where(User.username == "tester")))).all()
    untagged = db.scalar(select(Item.id).where(Item.id.in_(mine), ~Item.vibes.any()).limit(1))
    vibe = db.scalars(select(Vibe.id).join(User).where(User.username == "tester")).first()
print(f"seeded {N} albums (+{OTHER_USERS}x{OTHER_N}) in {time.perf_counter() - t0:.1f}s")

statements = 0


@event.listens_for(engine, "before_cursor_execute")
def _count(*_):
    global statements
    statements += 1


c = clients["tester"]
pages = [
    ("library", "/", {}),
    ("library htmx", "/", {"HX-Request": "true"}),
    ("library search", "/?q=Artist+12", {"HX-Request": "true"}),
    ("library vibe filter", f"/?vibe={vibe}", {"HX-Request": "true"}),
    ("library genre filter", "/?genre=genre+1&genre=genre+2", {"HX-Request": "true"}),
    ("library random", "/?order=random&seed=5", {"HX-Request": "true"}),
    ("library sort title", "/?sort=title&order=asc", {"HX-Request": "true"}),
    ("triage start", "/triage", {}),
    ("triage card", f"/triage/{untagged}", {}),
    ("album page", f"/albums/{mine[len(mine) // 2]}", {}),
    ("stats", "/stats", {}),
    ("genres", "/genres", {}),
    ("vibes", "/vibes", {}),
    ("export.csv", "/export.csv", {}),
]
print(f"{'page':24} {'median ms':>10} {'max ms':>8} {'sql':>5} {'KB':>8}")
for label, url, headers in pages:
    times = []
    for _ in range(5):
        statements = 0
        t = time.perf_counter()
        r = c.get(url, headers=headers, follow_redirects=False)
        times.append((time.perf_counter() - t) * 1000)
        assert r.status_code in (200, 303, 307), (url, r.status_code)
    print(f"{label:24} {statistics.median(times):10.1f} {max(times):8.1f} {statements:5} {len(r.content) / 1024:8.0f}")
