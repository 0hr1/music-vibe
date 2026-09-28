import asyncio
import re
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload
from starlette.middleware.sessions import SessionMiddleware

from . import covers, deezer, importer, musicbrainz, spotify
from .auth import LoginRequired, current_user, hash_password, verify_password
from .config import ALLOW_SIGNUP, COVERS_DIR, HTTPS_ONLY, SECRET_KEY, SESSION_MAX_AGE
from .db import Base, engine, get_db
from .models import Genre, Item, User, Vibe, item_genres

HERE = Path(__file__).resolve().parent

Base.metadata.create_all(engine)

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY,
    max_age=SESSION_MAX_AGE,
    same_site="lax",
    https_only=HTTPS_ONLY,
)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
app.mount("/covers", StaticFiles(directory=COVERS_DIR), name="covers")

templates = Jinja2Templates(directory=HERE / "templates")

SORTS = {
    "added": Item.created_at,
    "year": Item.year,
    "title": func.lower(Item.title),
    "artist": func.lower(Item.creator),
}
HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def text_on(color: str) -> str:
    """Black or white, whichever reads better on the given background."""
    r, g, b = (int(color[i : i + 2], 16) for i in (1, 3, 5))
    return "#000" if (0.299 * r + 0.587 * g + 0.114 * b) > 150 else "#fff"


templates.env.filters["text_on"] = text_on
templates.env.globals["spotify_enabled"] = spotify.enabled


@app.exception_handler(LoginRequired)
async def _login_required(request: Request, _exc):
    if request.headers.get("HX-Request"):
        return Response(status_code=204, headers={"HX-Redirect": "/login"})
    return RedirectResponse("/login", status_code=303)


def render(request: Request, name: str, status_code: int = 200, **ctx):
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


def redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def signup_open(db: Session) -> bool:
    return ALLOW_SIGNUP or db.scalar(select(func.count(User.id))) == 0


# ---------- auth ----------


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, db: Session = Depends(get_db)):
    return render(request, "login.html", signup_open=signup_open(db))


@app.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(func.lower(User.username) == username.strip().lower()))
    if not user or not verify_password(password, user.password_hash):
        return render(request, "login.html", 401, error="Wrong username or password.",
                      username=username, signup_open=signup_open(db))
    request.session.clear()
    request.session["user_id"] = user.id
    return redirect("/")


@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request, db: Session = Depends(get_db)):
    if not signup_open(db):
        return redirect("/login")
    return render(request, "register.html")


@app.post("/register")
def register(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    password2: str = Form(...),
    db: Session = Depends(get_db),
):
    if not signup_open(db):
        return redirect("/login")
    username = username.strip()
    error = None
    if not re.fullmatch(r"[A-Za-z0-9_.-]{2,32}", username):
        error = "Username: 2-32 letters, numbers, _ . or -"
    elif len(password) < 8:
        error = "Password must be at least 8 characters."
    elif password != password2:
        error = "Passwords don't match."
    elif db.scalar(select(User).where(func.lower(User.username) == username.lower())):
        error = "That username is taken."
    if error:
        return render(request, "register.html", 400, error=error, username=username)
    user = User(username=username, password_hash=hash_password(password))
    db.add(user)
    db.commit()
    request.session.clear()
    request.session["user_id"] = user.id
    return redirect("/vibes?welcome=1")


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return redirect("/login")


# ---------- library ----------


def _int_or_none(value: str | None) -> int | None:
    return int(value) if value and value.strip().lstrip("-").isdigit() else None


@app.get("/", response_class=HTMLResponse)
def library(
    request: Request,
    q: str = "",
    vibe: list[int] = Query(default=[]),
    genre: list[str] = Query(default=[]),
    year_min: str | None = None,
    year_max: str | None = None,
    sort: str = "added",
    order: str = "desc",
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    ymin, ymax = _int_or_none(year_min), _int_or_none(year_max)
    albums = _filtered_albums(db, user, q, vibe, genre, ymin, ymax, sort, order)

    ctx = dict(albums=albums, total=_album_count(db, user))
    # htmx filter updates only need the grid (but a history restore needs the full page)
    if request.headers.get("HX-Request") and not request.headers.get("HX-History-Restore-Request"):
        return render(request, "partials/grid.html", **ctx)

    vibes = db.scalars(select(Vibe).where(Vibe.user_id == user.id).order_by(Vibe.name)).all()
    genres = db.scalars(
        select(Genre.name)
        .join(Item.genres)
        .where(Item.user_id == user.id)
        .group_by(Genre.name)
        .order_by(func.count().desc(), Genre.name)
    ).all()
    years = db.execute(select(func.min(Item.year), func.max(Item.year)).where(Item.user_id == user.id)).one()
    return render(
        request, "library.html", user=user, vibes=vibes, genres=genres, years=years,
        f=dict(q=q, vibe=vibe, genre=genre, year_min=ymin, year_max=ymax, sort=sort, order=order),
        **ctx,
    )


def _filtered_albums(db: Session, user: User, q: str, vibe: list[int], genre: list[str],
                     ymin: int | None, ymax: int | None, sort: str, order: str) -> list[Item]:
    stmt = (
        select(Item)
        .where(Item.user_id == user.id, Item.kind == "album")
        .options(selectinload(Item.vibes), selectinload(Item.genres))
    )
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(Item.title.ilike(like) | Item.creator.ilike(like))
    for vid in vibe:  # must match ALL selected vibes
        stmt = stmt.where(Item.vibes.any(Vibe.id == vid))
    if genre:  # match ANY selected genre
        stmt = stmt.where(Item.genres.any(Genre.name.in_(genre)))
    if ymin is not None:
        stmt = stmt.where(Item.year >= ymin)
    if ymax is not None:
        stmt = stmt.where(Item.year <= ymax)
    col = SORTS.get(sort, Item.created_at)
    col = col.asc() if order == "asc" else col.desc()
    stmt = stmt.order_by(col.nulls_last(), Item.id.desc())
    return db.scalars(stmt).all()


@app.post("/albums/bulk", response_class=HTMLResponse)
async def bulk_edit(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Add/remove a vibe on, or delete, the selected albums; returns the grid re-rendered with the
    library's current filters (sent along from the filter form)."""
    form = await request.form()
    ids = {int(i) for i in form.getlist("sel") if i.isdigit()}
    items = db.scalars(select(Item).where(Item.user_id == user.id, Item.id.in_(ids))
                       .options(selectinload(Item.vibes))).all()
    action, message = form.get("action"), None
    if action == "delete":
        files = [item.cover_file for item in items]
        for item in items:
            db.delete(item)
        db.commit()
        for name in files:
            covers.delete(name)
        ids, message = set(), f"Deleted {len(items)} album{'' if len(items) == 1 else 's'}."
    elif action in ("add_vibe", "remove_vibe"):
        vibe = db.get(Vibe, _int_or_none(form.get("bulk_vibe")) or 0)
        if not vibe or vibe.user_id != user.id:
            raise HTTPException(400)
        changed = 0
        for item in items:
            if action == "add_vibe" and vibe not in item.vibes:
                item.vibes.append(vibe)
                changed += 1
            elif action == "remove_vibe" and vibe in item.vibes:
                item.vibes.remove(vibe)
                changed += 1
        db.commit()
        verb = "Added “{}” to" if action == "add_vibe" else "Removed “{}” from"
        message = f"{verb.format(vibe.name)} {changed} album{'' if changed == 1 else 's'}."
    else:
        raise HTTPException(400)
    albums = _filtered_albums(
        db, user, form.get("q", ""), [int(v) for v in form.getlist("vibe") if v.isdigit()], form.getlist("genre"),
        _int_or_none(form.get("year_min")), _int_or_none(form.get("year_max")),
        form.get("sort", "added"), form.get("order", "desc"),
    )
    return render(request, "partials/grid.html", albums=albums, total=_album_count(db, user),
                  selected=ids, message=message)


def _album_count(db: Session, user: User) -> int:
    return db.scalar(select(func.count(Item.id)).where(Item.user_id == user.id, Item.kind == "album"))


# ---------- albums ----------


def _user_vibes(db: Session, user: User) -> list[Vibe]:
    return db.scalars(select(Vibe).where(Vibe.user_id == user.id).order_by(Vibe.name)).all()


def _get_album(db: Session, user: User, album_id: int) -> Item:
    item = db.get(Item, album_id)
    if not item or item.user_id != user.id:
        raise HTTPException(404)
    return item


def _genre_name(raw: str) -> str:
    return " ".join(raw.strip().lower().split())[:100]


def _get_or_create_genre(db: Session, name: str) -> Genre:
    g = db.scalar(select(Genre).where(Genre.name == name))
    if not g:
        g = Genre(name=name)
        db.add(g)
    return g


def _parse_genres(db: Session, raw: str) -> list[Genre]:
    names = []
    for part in raw.split(","):
        name = _genre_name(part)
        if name and name not in names:
            names.append(name)
    return [_get_or_create_genre(db, name) for name in names]


def _clean_url(url: str) -> str | None:
    url = url.strip()
    return url if url.startswith(("https://", "http://")) else None


def _album_form(request, user, db, album=None, **ctx):
    return render(request, "album_form.html", user=user, album=album, vibes=_user_vibes(db, user), **ctx)


@app.get("/albums/new", response_class=HTMLResponse)
def new_album(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return render(request, "album_new.html", user=user, vibes=_user_vibes(db, user), prefill={})


def _owned_ids(db: Session, user: User) -> set[str]:
    return set(db.scalars(select(Item.external_id).where(Item.user_id == user.id, Item.external_id.is_not(None))))


@app.get("/albums/search", response_class=HTMLResponse)
async def search_albums(request: Request, q: str = "", user: User = Depends(current_user),
                        db: Session = Depends(get_db)):
    """Fast Deezer results; MusicBrainz results are loaded separately by the page."""
    q = q.strip()
    if len(q) < 2:
        return HTMLResponse("")
    try:
        results, error = await deezer.search_albums(q), False
    except httpx.HTTPError:
        results, error = [], True
    return render(request, "partials/search_results.html", results=results, error=error, q=q,
                  owned=_owned_ids(db, user), more=True)


@app.get("/albums/search/mb", response_class=HTMLResponse)
async def search_albums_mb(request: Request, q: str = "", user: User = Depends(current_user),
                           db: Session = Depends(get_db)):
    try:
        results, error = await musicbrainz.search_albums(q), False
    except httpx.HTTPError:
        results, error = [], True
    return render(request, "partials/search_results.html", results=results, error=error, q=q,
                  owned=_owned_ids(db, user), source_label="MusicBrainz")


@app.get("/albums/prefill", response_class=HTMLResponse)
async def prefill_album(request: Request, source: str, id: str, user: User = Depends(current_user),
                        db: Session = Depends(get_db)):
    try:
        if source == "deezer" and id.isdigit():
            prefill = await deezer.get_album(id)
        elif source == "mb" and re.fullmatch(r"[0-9a-f-]{36}", id):
            prefill = await musicbrainz.get_album(id)
        else:
            raise HTTPException(400)
    except httpx.HTTPError:
        prefill = {}
    return render(request, "partials/album_fields.html", vibes=_user_vibes(db, user), prefill=prefill,
                  album=None, lookup_failed=not prefill, refine=source == "deezer" and bool(prefill))


@app.get("/albums/refine", response_class=HTMLResponse)
async def refine_album(request: Request, title: str, artist: str = "", year: str = "", genres: str = "",
                       user: User = Depends(current_user)):
    """Swap in MusicBrainz's detailed genres and original release year for a Deezer-sourced album."""
    try:
        found = await musicbrainz.find_details(title, artist)
    except httpx.HTTPError:
        found = {}
    v_year = _int_or_none(year)
    if found.get("year") and (v_year is None or found["year"] < v_year):
        v_year = found["year"]
    v_genres = ", ".join(found["genres"]) if found.get("genres") else genres
    return render(request, "partials/year_genres.html", v_year=v_year, v_genres=v_genres)


@app.get("/albums/spotify", response_class=HTMLResponse)
async def spotify_link(request: Request, title: str = "", creator: str = "", spotify_url: str = "",
                       user: User = Depends(current_user)):
    """Look up the album's Spotify link; if nothing is found, keep whatever was in the field."""
    try:
        url = await spotify.find_album_url(title.strip(), creator.strip())
        note = None if url else "No match on Spotify. Paste the link by hand."
    except httpx.HTTPError:
        url, note = None, "Spotify didn't answer. Try Find again, or paste the link by hand."
    return render(request, "partials/spotify_field.html", v_spotify=url or spotify_url, spotify_note=note)


# ---------- bulk import ----------


def _mark_owned(db: Session, user: User, rows: list[dict]) -> list[dict]:
    """Flag results already in the library, by id or by same title + artist."""
    ids = _owned_ids(db, user)
    names = {(importer.norm(t), importer.norm(c)) for t, c in
             db.execute(select(Item.title, Item.creator).where(Item.user_id == user.id, Item.kind == "album"))}
    for row in rows:
        for r in row["results"]:
            r["owned"] = f"deezer:{r['id']}" in ids or (importer.norm(r["title"]), importer.norm(r["artist"])) in names
    return rows


@app.get("/albums/import", response_class=HTMLResponse)
def import_page(request: Request, user: User = Depends(current_user)):
    return render(request, "import.html", user=user, max_lines=importer.MAX_LINES)


@app.post("/albums/import", response_class=HTMLResponse)
async def import_match(request: Request, text: str = Form(""), user: User = Depends(current_user),
                       db: Session = Depends(get_db)):
    lines = importer.parse_lines(text)
    if not lines:
        return render(request, "import.html", 400, user=user, max_lines=importer.MAX_LINES, text=text,
                      error="Paste at least one album, one per line.")
    rows = _mark_owned(db, user, await importer.match_all(lines))
    return render(request, "import_review.html", user=user, rows=rows)


@app.get("/albums/import/row", response_class=HTMLResponse)
async def import_row(request: Request, i: int, q: str = "", user: User = Depends(current_user),
                     db: Session = Depends(get_db)):
    """Re-search one line of the review list."""
    row = await importer.match_line(" ".join(q.split())[:200], asyncio.Semaphore(1))
    return render(request, "partials/import_row.html", i=i, row=_mark_owned(db, user, [row])[0])


@app.post("/albums/import/add", response_class=HTMLResponse)
async def import_add(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    form = await request.form()
    picks = [form.get(f"pick-{i}") for i in form.getlist("row")]
    ids = _owned_ids(db, user)
    picks = list(dict.fromkeys(p for p in picks if p and p.isdigit() and f"deezer:{p}" not in ids))
    added, failed = [], 0
    for info in await importer.fetch_albums(picks):
        if info is None:
            failed += 1
            continue
        item = Item(user_id=user.id, kind="album", title=info["title"][:500] or "Untitled",
                    creator=info["artist"][:500], year=info["year"], external_id=info["external_id"],
                    cover_file=info["cover_file"], spotify_url=info["spotify_url"])
        item.genres = _parse_genres(db, ", ".join(info["genres"]))
        db.add(item)
        added.append(item)
    db.commit()
    return render(request, "import_done.html", user=user, added=added, failed=failed)


@app.post("/albums")
async def create_album(
    request: Request,
    title: str = Form(...),
    creator: str = Form(""),
    year: str = Form(""),
    genres: str = Form(""),
    vibes: list[int] = Form(default=[]),
    spotify_url: str = Form(""),
    notes: str = Form(""),
    external_id: str = Form(""),
    cover_url: str = Form(""),
    cover: UploadFile | None = File(None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    item = Item(
        user_id=user.id,
        kind="album",
        title=title.strip()[:500] or "Untitled",
        creator=creator.strip()[:500],
        year=_int_or_none(year),
        spotify_url=_clean_url(spotify_url),
        notes=notes.strip() or None,
        external_id=external_id.strip() or None,
    )
    item.genres = _parse_genres(db, genres)
    item.vibes = db.scalars(select(Vibe).where(Vibe.user_id == user.id, Vibe.id.in_(vibes))).all()
    if cover and cover.filename:
        item.cover_file = covers.save_upload(await cover.read(), cover.content_type or "")
    elif covers.is_trusted_url(cover_url):
        item.cover_file = await covers.download(cover_url)
    db.add(item)
    db.commit()
    return redirect("/")


@app.get("/albums/{album_id}", response_class=HTMLResponse)
def edit_album_page(request: Request, album_id: int, user: User = Depends(current_user),
                    db: Session = Depends(get_db)):
    return _album_form(request, user, db, album=_get_album(db, user, album_id))


@app.post("/albums/{album_id}")
async def update_album(
    request: Request,
    album_id: int,
    title: str = Form(...),
    creator: str = Form(""),
    year: str = Form(""),
    genres: str = Form(""),
    vibes: list[int] = Form(default=[]),
    spotify_url: str = Form(""),
    notes: str = Form(""),
    remove_cover: bool = Form(False),
    cover: UploadFile | None = File(None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    item = _get_album(db, user, album_id)
    item.title = title.strip()[:500] or "Untitled"
    item.creator = creator.strip()[:500]
    item.year = _int_or_none(year)
    item.spotify_url = _clean_url(spotify_url)
    item.notes = notes.strip() or None
    item.genres = _parse_genres(db, genres)
    item.vibes = db.scalars(select(Vibe).where(Vibe.user_id == user.id, Vibe.id.in_(vibes))).all()
    if cover and cover.filename:
        if new := covers.save_upload(await cover.read(), cover.content_type or ""):
            covers.delete(item.cover_file)
            item.cover_file = new
    elif remove_cover:
        covers.delete(item.cover_file)
        item.cover_file = None
    db.commit()
    return redirect("/")


@app.post("/albums/{album_id}/delete")
def delete_album(album_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    item = _get_album(db, user, album_id)
    covers.delete(item.cover_file)
    db.delete(item)
    db.commit()
    return redirect("/")


# ---------- genres ----------
# Genre names are shared between users, so renaming/merging/deleting only re-tags this user's albums.


@app.get("/genres", response_class=HTMLResponse)
def genres_page(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.execute(
        select(Genre, func.count(Item.id)).join(Item.genres).where(Item.user_id == user.id)
        .group_by(Genre.id).order_by(Genre.name)
    ).all()
    groups: dict[str, list] = {}
    for genre, count in rows:  # "hip hop" / "hip-hop" / "Hip Hop" look like the same genre
        groups.setdefault(importer.norm(genre.name), []).append((genre, count))
    duplicates = [g for g in groups.values() if len(g) > 1]
    return render(request, "genres.html", user=user, rows=rows, duplicates=duplicates)


def _retag(db: Session, user: User, source_ids: set[int], target: Genre | None) -> None:
    """Replace the source genres with `target` (or just remove them) on this user's albums."""
    source_ids.discard(target.id if target and target.id else -1)
    items = db.scalars(
        select(Item).where(Item.user_id == user.id, Item.genres.any(Genre.id.in_(source_ids)))
        .options(selectinload(Item.genres))
    ).all()
    for item in items:
        item.genres = [g for g in item.genres if g.id not in source_ids]
        if target and target not in item.genres:
            item.genres.append(target)
    db.flush()
    db.execute(delete(Genre).where(Genre.id.not_in(select(item_genres.c.genre_id))))  # unused by anyone
    db.commit()


def _user_genre_ids(db: Session, user: User, ids: list[int]) -> set[int]:
    return set(db.scalars(select(Genre.id).join(Item.genres).where(Item.user_id == user.id, Genre.id.in_(ids))))


@app.post("/genres/merge")
def merge_genres(genre: list[int] = Form(default=[]), name: str = Form(...), user: User = Depends(current_user),
                 db: Session = Depends(get_db)):
    """Rename one genre, or merge several: all become `name` (which may be an existing genre)."""
    name = _genre_name(name)
    if name:
        _retag(db, user, _user_genre_ids(db, user, genre), _get_or_create_genre(db, name))
    return redirect("/genres")


@app.post("/genres/{genre_id}/delete")
def delete_genre(genre_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _retag(db, user, _user_genre_ids(db, user, [genre_id]), None)
    return redirect("/genres")


# ---------- vibes ----------


@app.get("/vibes", response_class=HTMLResponse)
def vibes_page(request: Request, welcome: bool = False, user: User = Depends(current_user),
               db: Session = Depends(get_db)):
    counts = dict(
        db.execute(
            select(Vibe.id, func.count(Item.id)).join(Item.vibes).where(Vibe.user_id == user.id).group_by(Vibe.id)
        ).all()
    )
    return render(request, "vibes.html", user=user, vibes=_user_vibes(db, user), counts=counts, welcome=welcome)


def _vibe_values(name: str, color: str) -> tuple[str, str]:
    name = " ".join(name.split())[:64]
    color = color if HEX_COLOR.match(color) else "#8a5cd6"
    return name, color.lower()


@app.post("/vibes")
def create_vibe(name: str = Form(...), color: str = Form("#8a5cd6"), user: User = Depends(current_user),
                db: Session = Depends(get_db)):
    name, color = _vibe_values(name, color)
    if name:
        db.add(Vibe(user_id=user.id, name=name, color=color))
        try:
            db.commit()
        except IntegrityError:  # duplicate name
            db.rollback()
    return redirect("/vibes")


@app.post("/vibes/{vibe_id}")
def update_vibe(vibe_id: int, name: str = Form(...), color: str = Form(...), user: User = Depends(current_user),
                db: Session = Depends(get_db)):
    vibe = db.get(Vibe, vibe_id)
    if not vibe or vibe.user_id != user.id:
        raise HTTPException(404)
    name, color = _vibe_values(name, color)
    if name:
        vibe.name, vibe.color = name, color
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
    return redirect("/vibes")


@app.post("/vibes/{vibe_id}/delete")
def delete_vibe(vibe_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    vibe = db.get(Vibe, vibe_id)
    if not vibe or vibe.user_id != user.id:
        raise HTTPException(404)
    db.delete(vibe)
    db.commit()
    return redirect("/vibes")
