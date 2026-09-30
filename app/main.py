import asyncio
import csv
import functools
import hashlib
import io
import random
import re
import secrets
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import httpx
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload
from starlette.middleware.sessions import SessionMiddleware

from . import backup, covers, deezer, importer, invites, migrations, musicbrainz, ratelimit, spotify
from .auth import (LoginRequired, admin_user, admins_named, check_login, current_user, hash_password, is_admin,
                   is_reserved_admin_name, log_in, verify_password)
from .config import ALLOW_SIGNUP, COVERS_DIR, HTTPS_ONLY, ON_FLY, SECRET_KEY, SESSION_MAX_AGE
from .db import engine, get_db
from .models import Genre, InviteCode, Item, User, Vibe, item_genres

HERE = Path(__file__).resolve().parent

migrations.upgrade(engine)

@asynccontextmanager
async def lifespan(_app: FastAPI):
    task = asyncio.create_task(backup.run_forever())
    yield
    task.cancel()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY,
    max_age=SESSION_MAX_AGE,
    same_site="lax",
    https_only=HTTPS_ONLY,
)


MAX_BODY = covers.MAX_BYTES + 2 * 1024 * 1024  # a cover upload plus the rest of the form


class BodyLimit:
    """Refuses request bodies over MAX_BODY. Form parsing saves uploads to temp files before any
    route code (even the login check) runs, so without this anyone could fill the disk."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        length = dict(scope["headers"]).get(b"content-length", b"0")
        if not length.isdigit() or int(length) > MAX_BODY:
            return await Response("Request too large.", 413, media_type="text/plain")(scope, receive, send)
        received = 0

        async def counted():  # for bodies sent without a Content-Length
            nonlocal received
            message = await receive()
            received += len(message.get("body", b""))
            if received > MAX_BODY:
                raise HTTPException(413, "Request too large.")
            return message

        await self.app(scope, counted, send)


app.add_middleware(BodyLimit)


class VersionedStatic(StaticFiles):
    """A file asked for with ?v=<hash of its contents> (see static_url) never changes at that URL, so
    browsers may keep it for a year; a deploy that changes the file changes the URL."""

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        if scope.get("query_string", b"").startswith(b"v=") and response.status_code == 200:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


@functools.cache
def _digest(path: Path, mtime_ns: int) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def static_url(name: str) -> str:
    """URL of a file in app/static that changes whenever the file does."""
    path = HERE / "static" / name
    return f"/static/{name}?v={_digest(path, path.stat().st_mtime_ns)}"


app.mount("/static", VersionedStatic(directory=HERE / "static"), name="static")
app.mount("/covers", StaticFiles(directory=COVERS_DIR), name="covers")

templates = Jinja2Templates(directory=HERE / "templates")

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}
if HTTPS_ONLY:
    SECURITY_HEADERS["Strict-Transport-Security"] = "max-age=31536000"


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    response = await call_next(request)
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    if response.headers.get("content-type", "").startswith("text/html"):
        # Pages show the library as it is now: Back mustn't bring up a stale copy from the browser's
        # cache (e.g. albums just tagged still showing as untagged), nor show them after logging out
        response.headers.setdefault("Cache-Control", "no-store")
    return response


@app.get("/healthz")
def healthz(db: Session = Depends(get_db)):
    """For the host's health checks: the app is up and the database answers."""
    db.execute(text("SELECT 1"))
    return Response("ok", media_type="text/plain")

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
templates.env.globals["static_url"] = static_url


def _local_path(url: str) -> str | None:
    """The path and query of a URL on this site, or None: only these may be sent back to after login.
    Browsers take "//host", "/\\host" and "/<tab>/host" to another site, so those are refused."""
    return url if re.fullmatch(r"/(?![/\\])[^\x00-\x20\x7f\\]*", url) else None


@app.exception_handler(LoginRequired)
async def _login_required(request: Request, _exc):
    """Off to the login page, which comes back here afterwards: to this page, or for a background
    (htmx) request or a form sent after the session ran out, the page it came from."""
    if request.method == "GET" and not request.headers.get("HX-Request"):
        back = request.url
    else:
        back = urlsplit(request.headers.get("HX-Current-URL") or request.headers.get("referer") or "")
        if back.netloc != request.url.netloc:
            back = None
    target = "/login"
    if back and (path := back.path + (f"?{back.query}" if back.query else "")) not in ("/", "/login"):
        target += "?" + urlencode({"next": path})
    if request.headers.get("HX-Request"):
        return Response(status_code=204, headers={"HX-Redirect": target})
    return RedirectResponse(target, status_code=303)


def render(request: Request, name: str, status_code: int = 200, **ctx):
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


def redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


TOO_MANY = "Too many attempts. Wait a few minutes and try again."


def client_ip(request: Request) -> str:
    """The visitor's address. On Fly it's the Fly-Client-IP header, which Fly's proxy sets itself.
    (X-Forwarded-For won't do there: Fly appends to whatever the client sent, so its first entry is
    made up by the client.)"""
    ip = request.headers.get("fly-client-ip") if ON_FLY else None
    return f"ip:{ip or (request.client.host if request.client else '?')}"


def _login_limits(request: Request, username: str) -> tuple[tuple[ratelimit.Limiter, str], ...]:
    """The (limiter, key) pairs a wrong password counts against."""
    name, ip = username.strip().lower()[:64], client_ip(request)
    return ((ratelimit.login_by_ip, ip), (ratelimit.login_by_user_ip, f"user:{name}|{ip}"),
            (ratelimit.login_by_user, f"user:{name}"))


def _no_users(db: Session) -> bool:
    return db.scalar(select(func.count(User.id))) == 0


def signup_open(db: Session) -> bool:
    """Whether the sign-up page can skip the invite code: ALLOW_SIGNUP is on, or it's the very first
    account and ADMIN_USERNAMES doesn't say who that must be."""
    return ALLOW_SIGNUP or (_no_users(db) and not admins_named())


def needs_invite(db: Session, username: str) -> bool:
    """With ADMIN_USERNAMES set, only a listed name can take the first account without a code, so a
    stranger who finds a fresh public deploy first can't get in."""
    if signup_open(db):
        return False
    return not (_no_users(db) and is_reserved_admin_name(username))


# ---------- auth ----------


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "", db: Session = Depends(get_db)):
    return render(request, "login.html", needs_code=not signup_open(db), next=_local_path(next))


@app.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...), next: str = Form(""),
          db: Session = Depends(get_db)):
    next = _local_path(next)
    limits = _login_limits(request, username)
    if any(limiter.blocked(key) for limiter, key in limits):
        return render(request, "login.html", 429, error=TOO_MANY, username=username, next=next,
                      needs_code=not signup_open(db))
    # Counted before the (slow) password check, so a burst of attempts sent at once can't all get in
    # ahead of the count; forgiven below if it turns out right.
    for limiter, key in limits:
        limiter.hit(key)
    user = db.scalar(select(User).where(func.lower(User.username) == username.strip().lower()))
    if not check_login(user, password):
        return render(request, "login.html", 401, error="Wrong username or password.",
                      username=username, next=next, needs_code=not signup_open(db))
    for limiter, key in limits:
        limiter.undo(key)
    ratelimit.login_by_user_ip.reset(limits[1][1])
    log_in(request, user)
    return redirect(next or "/")


@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request, code: str = "", db: Session = Depends(get_db)):
    return _register_form(request, db, code=code)


def _register_form(request: Request, db: Session, status_code: int = 200, code: str = "", **ctx):
    needs_code = not signup_open(db)
    return render(request, "register.html", status_code, needs_code=needs_code, code=invites.normalize(code),
                  admin_first=needs_code and _no_users(db), **ctx)


@app.post("/register")
def register(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    password2: str = Form(...),
    code: str = Form(""),
    db: Session = Depends(get_db),
):
    username = username.strip()
    if ratelimit.signup_by_ip.blocked(client_ip(request)):
        return _register_form(request, db, 429, code=code, error=TOO_MANY, username=username)
    ratelimit.signup_by_ip.hit(client_ip(request))
    code_required = needs_invite(db, username)
    # The code is checked before the name, so without one you can't find out which names exist.
    invite = invites.find_usable(db, code) if code_required else None
    if not re.fullmatch(r"[A-Za-z0-9_.-]{2,32}", username):
        error = "Username: 2-32 letters, numbers, _ . or -"
    elif code_required and not invite:
        error = "That invite code doesn't work. It may have expired or been used up."
    elif (code_required and is_reserved_admin_name(username)) or db.scalar(
            select(User).where(func.lower(User.username) == username.lower())):
        error = "That username is taken."
    else:
        error = _password_problem(password, password2)
    if not error and code_required and not invites.redeem(db, invite):
        error = "That invite code doesn't work. It may have expired or been used up."
    if error:
        db.rollback()
        return _register_form(request, db, 400, code=code, error=error, username=username)
    user = User(username=username, password_hash=hash_password(password))
    db.add(user)
    try:
        db.flush()
        db.add_all(Vibe(user_id=user.id, name=name, color=color) for name, color in STARTER_VIBES)
        db.commit()
    except IntegrityError:  # someone took the name (in any capitalization) a moment ago
        db.rollback()
        return _register_form(request, db, 400, code=code, error="That username is taken.", username=username)
    log_in(request, user)
    return redirect("/")


def _password_problem(password: str, password2: str) -> str | None:
    if len(password) < 8:
        return "Password must be at least 8 characters."
    if password != password2:
        return "Passwords don't match."
    return None


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return redirect("/login")


# ---------- account ----------


@app.get("/account", response_class=HTMLResponse)
def account_page(request: Request, saved: bool = False, user: User = Depends(current_user),
                 db: Session = Depends(get_db)):
    return render(request, "account.html", user=user, saved=saved, admin=is_admin(db, user))


@app.post("/account/password")
def change_password(
    request: Request,
    current: str = Form(...),
    password: str = Form(...),
    password2: str = Form(...),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    limits = _login_limits(request, user.username)
    if any(limiter.blocked(key) for limiter, key in limits):
        error = TOO_MANY
    else:
        for limiter, key in limits:  # counted first, as on the login page
            limiter.hit(key)
        if not verify_password(current, user.password_hash):
            error = "Current password is wrong."
        else:
            for limiter, key in limits:
                limiter.undo(key)
            error = _password_problem(password, password2)
    if error:
        return render(request, "account.html", 400, user=user, error=error, admin=is_admin(db, user))
    user.password_hash = hash_password(password)
    db.commit()
    log_in(request, user)  # the new password signs out every other session; keep this one
    return redirect("/account?saved=1")


# ---------- admin: invite codes and users ----------


def _admin_page(request: Request, db: Session, admin: User, status_code: int = 200, **ctx):
    codes = db.scalars(select(InviteCode).order_by(InviteCode.created_at.desc())).all()
    users = db.execute(
        select(User, func.count(Item.id)).outerjoin(Item, Item.user_id == User.id).group_by(User.id).order_by(User.id)
    ).all()
    return render(request, "admin.html", status_code, user=admin, codes=codes, users=users,
                  signup_url=str(request.url_for("register_page")), **ctx)


@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    return _admin_page(request, db, admin, reset=request.session.pop("reset", None))


@app.post("/admin/invites")
def create_invite(uses: str = Form(""), days: str = Form(""), note: str = Form(""),
                  admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    max_uses, valid_days = _int_or_none(uses), _int_or_none(days)
    invites.create(db, max_uses=max_uses if max_uses and max_uses > 0 else None,
                   days=valid_days if valid_days and valid_days > 0 else None, note=note.strip(), created_by=admin.id)
    return redirect("/admin")


@app.post("/admin/invites/{invite_id}/delete")
def delete_invite(invite_id: int, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    if invite := db.get(InviteCode, invite_id):
        db.delete(invite)
        db.commit()
    return redirect("/admin")


def _other_user(db: Session, admin: User, user_id: int) -> User:
    """Admins manage other accounts here; their own goes through the account page."""
    target = db.get(User, user_id)
    if not target or target.id == admin.id:
        raise HTTPException(404)
    return target


@app.post("/admin/users/{user_id}/reset-password")
def reset_password(request: Request, user_id: int, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    target = _other_user(db, admin, user_id)
    temp = secrets.token_urlsafe(9)
    target.password_hash = hash_password(temp)
    db.commit()
    # Shown once on the admin page. Not straight from this POST: reloading that would reset it again,
    # and the password already sent on would stop working.
    request.session["reset"] = (target.username, temp)
    return redirect("/admin")


@app.post("/admin/users/{user_id}/delete")
def delete_user(user_id: int, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    target = _other_user(db, admin, user_id)
    files = db.scalars(select(Item.cover_file).where(Item.user_id == target.id, Item.cover_file.is_not(None))).all()
    db.delete(target)  # albums and vibes go with it (ON DELETE CASCADE)
    db.commit()
    for name in files:
        covers.delete(name)
    return redirect("/admin")


# ---------- library ----------


def _int_or_none(value: str | None) -> int | None:
    """Up to 9 digits; anything bigger is nonsense here and overflows SQLite's integers."""
    value = (value or "").strip()
    return int(value) if value.lstrip("-").isdigit() and len(value.lstrip("-")) <= 9 else None


YEARS = range(1000, 2101)


def _year(value: str | int | None) -> int | None:
    """A plausible release year, or None. Stats draws one bar per decade in between, so the range
    must stay small."""
    year = _int_or_none(value) if isinstance(value, str) else value
    return year if year in YEARS else None


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
    seed: str | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    ymin, ymax = _int_or_none(year_min), _int_or_none(year_max)
    albums = _filtered_albums(db, user, q, vibe, genre, ymin, ymax, sort, order, _int_or_none(seed))

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
        untagged=_untagged_count(db, user),
        f=dict(q=q, vibe=vibe, genre=genre, year_min=ymin, year_max=ymax, sort=sort, order=order, seed=seed or ""),
        **ctx,
    )


def _filtered_albums(db: Session, user: User, q: str, vibe: list[int], genre: list[str],
                     ymin: int | None, ymax: int | None, sort: str, order: str,
                     seed: int | None = None) -> list[Item]:
    stmt = (
        select(Item)
        .where(Item.user_id == user.id, Item.kind == "album")
        .options(selectinload(Item.vibes))  # the grid shows vibes, not genres
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
    if order == "random":
        # Shuffled by a seed kept in the URL, so re-rendering the grid (after a bulk edit, a refresh
        # or going back) keeps the same order until the user shuffles again
        albums = list(db.scalars(stmt.order_by(Item.id)).all())
        random.Random(seed).shuffle(albums)
        return albums
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
        form.get("sort", "added"), form.get("order", "desc"), _int_or_none(form.get("seed")),
    )
    return render(request, "partials/grid.html", albums=albums, total=_album_count(db, user),
                  selected=ids, message=message, oob_untagged=_untagged_count(db, user))


def _album_count(db: Session, user: User) -> int:
    return db.scalar(select(func.count(Item.id)).where(Item.user_id == user.id, Item.kind == "album"))


def _untagged_count(db: Session, user: User) -> int:
    return db.scalar(select(func.count(Item.id)).where(Item.user_id == user.id, Item.kind == "album",
                                                       ~Item.vibes.any()))


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
async def prefill_album(request: Request, source: str, id: str, vibes: list[str] = Query(default=[]),
                        notes: str = "", user: User = Depends(current_user), db: Session = Depends(get_db)):
    """The add form's fields for a search result. Vibes ticked and notes typed for a result picked
    earlier are kept: they're the user's, not the album's."""
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
                  album=None, lookup_failed=not prefill, refine=source == "deezer" and bool(prefill),
                  keep_vibes=[int(n) for n in vibes if re.fullmatch(r"[0-9]{1,9}", n)], keep_notes=notes,
                  owned=_owned_copy(db, user, prefill) if prefill else None)


def _owned_copy(db: Session, user: User, info: dict) -> Item | None:
    """The user's album that this search result is, by source id or by same title and artist."""
    mine = select(Item).where(Item.user_id == user.id, Item.kind == "album")
    if info.get("external_id") and (item := db.scalars(mine.where(Item.external_id == info["external_id"])).first()):
        return item
    wanted = (importer.norm(info.get("title") or ""), importer.norm(info.get("artist") or ""))
    return next((i for i in db.scalars(mine) if (importer.norm(i.title), importer.norm(i.creator)) == wanted), None)


@app.get("/albums/refine", response_class=HTMLResponse)
async def refine_album(request: Request, title: str, artist: str = "", year: str = "", genres: str = "",
                       user: User = Depends(current_user)):
    """Swap in MusicBrainz's detailed genres and original release year for a Deezer-sourced album."""
    try:
        found = await musicbrainz.find_details(title, artist)
    except httpx.HTTPError:
        found = {}
    v_year, found_year = _year(year), _year(found.get("year"))
    if found_year and (v_year is None or found_year < v_year):
        v_year = found_year
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
                    creator=info["artist"][:500], year=_year(info["year"]), external_id=info["external_id"],
                    cover_file=info["cover_file"], spotify_url=info["spotify_url"])
        item.genres = _parse_genres(db, ", ".join(info["genres"]))
        db.add(item)
        added.append(item)
    db.commit()
    # A page of its own, so reloading it doesn't send the form again (and add nothing)
    batch = f"{min(a.id for a in added)}-{max(a.id for a in added)}" if added else ""
    return redirect("/albums/import/done?" + urlencode({"batch": batch, "failed": failed}))


@app.get("/albums/import/done", response_class=HTMLResponse)
def import_done(request: Request, batch: str = "", failed: str = "", user: User = Depends(current_user),
                db: Session = Depends(get_db)):
    span = _span(batch)
    added = db.scalars(select(Item).where(*_in_triage(user, span)).order_by(Item.id)).all() if span else []
    return render(request, "import_done.html", user=user, added=added, failed=max(0, _int_or_none(failed) or 0),
                  batch=batch if span else "")


# ---------- triage: tagging albums one card at a time ----------
# Which albums a triage goes through is a rule, not a stored list: every album without vibes, or, with
# `batch=<first id>-<last id>`, the albums one import added. Cards go in the order albums were added,
# and the URL names the album on screen, so a triage can be left and picked up again any time.

_lookups: dict[int, asyncio.Task] = {}  # MusicBrainz lookups under way, by album id
Span = tuple[int, int] | None  # an import's first and last album id; None means "albums without vibes"


def _span(batch: str) -> Span:
    m = re.fullmatch(r"(\d{1,9})-(\d{1,9})", batch)
    return (int(m[1]), int(m[2])) if m else None


def _in_triage(user: User, span: Span) -> list:
    where = [Item.user_id == user.id, Item.kind == "album"]
    return where + [Item.id.between(*span) if span else ~Item.vibes.any()]


def _count(db: Session, *where) -> int:
    return db.scalar(select(func.count(Item.id)).where(*where))


def _neighbour(db: Session, user: User, span: Span, album_id: int, after: bool) -> Item | None:
    side = Item.id > album_id if after else Item.id < album_id
    return db.scalars(select(Item).where(*_in_triage(user, span), side)
                      .order_by(Item.id if after else Item.id.desc()).limit(1)).first()


def _triage_url(album_id: int | None, span: Span) -> str:
    query = f"?batch={span[0]}-{span[1]}" if span else ""
    return f"/triage/{album_id}{query}" if album_id else f"/triage/done{query}"


@app.get("/triage")
def triage_start(batch: str = "", ids: str = "", i: int = 0, user: User = Depends(current_user),
                 db: Session = Depends(get_db)):
    """Pick up where you left off: the first album still without vibes (in the batch, if one is given).
    Also turns the long `?ids=1,2,3&i=4` links triage used to have into the short kind."""
    if ids:
        wanted = [int(n) for n in ids.split(",") if n.strip().isdigit()]
        mine = set(db.scalars(select(Item.id).where(Item.user_id == user.id, Item.id.in_(wanted))))
        wanted = [n for n in wanted if n in mine]
        if not wanted:
            return redirect(_triage_url(None, None))
        return redirect(_triage_url(wanted[max(0, min(i, len(wanted) - 1))], (min(wanted), max(wanted))))
    span = _span(batch)
    first = db.scalar(select(func.min(Item.id)).where(*_in_triage(user, span), ~Item.vibes.any()))
    return redirect(_triage_url(first, span))


def _known_genres(db: Session, user: User) -> list[str]:
    return db.scalars(select(Genre.name).join(Item.genres).where(Item.user_id == user.id)
                      .group_by(Genre.name).order_by(Genre.name)).all()


@app.get("/triage/done", response_class=HTMLResponse)
def triage_done(request: Request, batch: str = "", user: User = Depends(current_user),
                db: Session = Depends(get_db)):
    span = _span(batch)
    mine = _in_triage(user, span)
    untagged = _count(db, Item.user_id == user.id, Item.kind == "album", ~Item.vibes.any())
    last = _neighbour(db, user, span, 2**31, after=False) if span else None
    return render(request, "triage.html", user=user, album=None, span=span, untagged=untagged,
                  total=_count(db, *mine), tagged=_count(db, *mine, Item.vibes.any()),
                  prev_url=_triage_url(last.id, span) if last else None)


@app.get("/triage/{album_id}", response_class=HTMLResponse)
def triage_card(request: Request, album_id: int, batch: str = "", user: User = Depends(current_user),
                db: Session = Depends(get_db)):
    span = _span(batch)
    album = db.get(Item, album_id)
    if not album or album.user_id != user.id:  # e.g. removed, then reached with the browser's back button
        return redirect(_triage_url(getattr(_neighbour(db, user, span, album_id, after=True), "id", None), span))
    upcoming = _neighbour(db, user, span, album.id, after=True)
    earlier = _neighbour(db, user, span, album.id, after=False) if span else None  # else app.js knows
    mine, others = _in_triage(user, span), Item.id != album.id
    if span:  # the card itself is counted live, as its vibes are ticked
        progress = dict(position=_count(db, *mine, Item.id <= album.id), total=_count(db, *mine),
                        tagged_others=_count(db, *mine, others, Item.vibes.any()))
    else:
        progress = dict(left_others=_count(db, *mine, others))
    return render(
        request, "triage.html", user=user, album=album, span=span, batch=batch if span else "",
        vibes=(vibes := _user_vibes(db, user)), suggested_color=_unused_color(vibes),
        prev_url=_triage_url(earlier.id, span) if earlier else None,
        next_url=_triage_url(getattr(upcoming, "id", None), span),
        last=upcoming is None, prefetch=upcoming.id if upcoming and not upcoming.genres_checked else None,
        known_genres=_known_genres(db, user), **progress,
    )


# Colours a vibe made on the fly gets, in order, skipping ones already in use
VIBE_PALETTE = ["#e0563b", "#3bb58a", "#6b7fa3", "#d6a23b", "#b05cd6", "#3b8fe0", "#e05c9a", "#7cb342",
                "#8d6e63", "#26a69a", "#5c6bc0", "#f4a261"]


def _unused_color(vibes: list[Vibe]) -> str:
    used = {v.color.lower() for v in vibes}
    return next((c for c in VIBE_PALETTE if c not in used), VIBE_PALETTE[len(vibes) % len(VIBE_PALETTE)])


@app.post("/triage/{album_id}/new-vibe", response_class=HTMLResponse)
def triage_new_vibe(request: Request, album_id: int, name: str = Form(""), color: str = Form(""),
                    vibes: list[int] = Form(default=[]), user: User = Depends(current_user),
                    db: Session = Depends(get_db)):
    """Make a vibe (or find the one by that name) and tick it on this album, along with the vibes
    already ticked on the card."""
    item = _get_album(db, user, album_id)
    name, color = _vibe_values(name, color)
    ticked = db.scalars(select(Vibe).where(Vibe.user_id == user.id, Vibe.id.in_(vibes))).all()
    if name:
        vibe = db.scalar(select(Vibe).where(Vibe.user_id == user.id, func.lower(Vibe.name) == name.lower()))
        if not vibe:
            vibe = Vibe(user_id=user.id, name=name, color=color)
            db.add(vibe)
        ticked = [*ticked, vibe] if vibe not in ticked else ticked
    item.vibes = ticked
    db.commit()
    all_vibes = _user_vibes(db, user)
    return render(request, "partials/triage_vibes.html", album=item, vibes=all_vibes,
                  suggested_color=_unused_color(all_vibes))


def _genre_chips(request: Request, item: Item, **ctx):
    return render(request, "partials/triage_genres.html", album=item, **ctx)


@app.post("/triage/{album_id}/vibes")
def triage_vibes(album_id: int, vibes: list[int] = Form(default=[]), user: User = Depends(current_user),
                 db: Session = Depends(get_db)):
    item = _get_album(db, user, album_id)
    item.vibes = db.scalars(select(Vibe).where(Vibe.user_id == user.id, Vibe.id.in_(vibes))).all()
    db.commit()
    return Response(status_code=204)


@app.post("/triage/{album_id}/genres", response_class=HTMLResponse)
def triage_genres(request: Request, album_id: int, add: str = Form(""), remove: str = Form(""),
                  user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Add genres (comma separated) to, or remove one from, a single album."""
    item = _get_album(db, user, album_id)
    names = [g.name for g in item.genres if g.name != _genre_name(remove)]
    item.genres = _parse_genres(db, ", ".join(names + [add]))
    item.genres_checked = True
    db.commit()
    return _genre_chips(request, item)


async def _look_up(item_id: int, title: str, artist: str) -> dict:
    """One MusicBrainz lookup per album at a time: the card and the prefetch for it can share it."""
    if item_id not in _lookups:
        _lookups[item_id] = asyncio.create_task(musicbrainz.find_details(title, artist))
        _lookups[item_id].add_done_callback(lambda _t: _lookups.pop(item_id, None))
    return await asyncio.shield(_lookups[item_id])


@app.post("/triage/{album_id}/refine", response_class=HTMLResponse)
async def triage_refine(request: Request, album_id: int, user: User = Depends(current_user),
                        db: Session = Depends(get_db)):
    """Swap in MusicBrainz's genres and original year, unless the genres were already checked or got
    edited while MusicBrainz was answering."""
    item = _get_album(db, user, album_id)
    if item.genres_checked:
        return _genre_chips(request, item)
    try:
        found = await _look_up(item.id, item.title, item.creator)
    except httpx.HTTPError:
        return _genre_chips(request, item, note="MusicBrainz didn't answer, so these are Deezer's genres.")
    db.expire_all()
    item = db.get(Item, album_id)
    if item is None:  # removed from the library meanwhile
        return HTMLResponse("")
    if item.genres_checked:
        return _genre_chips(request, item)
    if found.get("genres"):
        item.genres = _parse_genres(db, ", ".join(found["genres"]))
    year = _year(found.get("year"))
    if year and (item.year is None or year < item.year):
        item.year = year
    item.genres_checked = True
    db.commit()
    return _genre_chips(request, item, year_changed=True,
                        note="Genres from MusicBrainz." if found.get("genres") else None)


@app.post("/triage/{album_id}/delete")
def triage_delete(album_id: int, batch: str = Form(""), user: User = Depends(current_user),
                  db: Session = Depends(get_db)):
    """Remove an album from the library and carry on with the next one."""
    item, span = _get_album(db, user, album_id), _span(batch)
    upcoming = _neighbour(db, user, span, album_id, after=True)
    covers.delete(item.cover_file)
    db.delete(item)
    db.commit()
    return redirect(_triage_url(getattr(upcoming, "id", None), span))


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
    # Fetch the cover before touching the database: once a new genre is written, the database stays
    # locked for everyone until the commit, and a download can take a while.
    cover_file = None
    if cover and cover.filename:
        cover_file = covers.save_upload(await cover.read(covers.MAX_BYTES + 1), cover.content_type or "")
    elif covers.is_trusted_url(cover_url):
        cover_file = await covers.download(cover_url)
    item = Item(
        user_id=user.id,
        kind="album",
        title=title.strip()[:500] or "Untitled",
        creator=creator.strip()[:500],
        year=_year(year),
        spotify_url=_clean_url(spotify_url),
        notes=notes.strip() or None,
        external_id=external_id.strip() or None,
    )
    item.genres = _parse_genres(db, genres)
    item.genres_checked = True
    item.vibes = db.scalars(select(Vibe).where(Vibe.user_id == user.id, Vibe.id.in_(vibes))).all()
    item.cover_file = cover_file
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
    upload = await cover.read(covers.MAX_BYTES + 1) if cover and cover.filename else None  # first, as above
    item = _get_album(db, user, album_id)
    item.title = title.strip()[:500] or "Untitled"
    item.creator = creator.strip()[:500]
    item.year = _year(year)
    item.spotify_url = _clean_url(spotify_url)
    item.notes = notes.strip() or None
    item.genres = _parse_genres(db, genres)
    item.genres_checked = True
    item.vibes = db.scalars(select(Vibe).where(Vibe.user_id == user.id, Vibe.id.in_(vibes))).all()
    if upload is not None:
        if new := covers.save_upload(upload, cover.content_type or ""):
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


# ---------- stats ----------


@app.get("/stats", response_class=HTMLResponse)
def stats_page(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    mine = (Item.user_id == user.id) & (Item.kind == "album")
    total = db.scalar(select(func.count(Item.id)).where(mine))
    artists = db.scalar(select(func.count(func.distinct(func.lower(Item.creator)))).where(mine, Item.creator != ""))
    tagged = db.scalar(select(func.count(Item.id)).where(mine, Item.vibes.any()))
    ymin, ymax = db.execute(select(func.min(Item.year), func.max(Item.year)).where(mine)).one()

    counts = dict(db.execute(
        select(Vibe.id, func.count(Item.id)).join(Item.vibes).where(mine).group_by(Vibe.id)
    ).all())
    vibe_counts = sorted(((v, counts.get(v.id, 0)) for v in _user_vibes(db, user)), key=lambda vc: -vc[1])
    genre_counts = db.execute(
        select(Genre.name, func.count(Item.id)).join(Item.genres).where(mine)
        .group_by(Genre.name).order_by(func.count(Item.id).desc(), Genre.name).limit(10)
    ).all()
    artist_counts = db.execute(
        select(func.min(Item.creator), func.count(Item.id)).where(mine, Item.creator != "")
        .group_by(func.lower(Item.creator)).order_by(func.count(Item.id).desc(), func.min(Item.creator)).limit(10)
    ).all()
    by_decade = dict(db.execute(
        select((Item.year // 10) * 10, func.count(Item.id)).where(mine, Item.year.is_not(None)).group_by(Item.year // 10)
    ).all())
    decades = ([(d, by_decade.get(d, 0)) for d in range(ymin // 10 * 10, ymax + 1, 10)]
               if ymin is not None and ymax - ymin <= len(YEARS) else [])
    return render(
        request, "stats.html", user=user, total=total, artists=artists, tagged=tagged, years=(ymin, ymax),
        genre_total=db.scalar(select(func.count(func.distinct(item_genres.c.genre_id))).join(Item).where(mine)),
        vibe_counts=vibe_counts, genre_counts=genre_counts, artist_counts=artist_counts, decades=decades,
    )


@app.get("/export.csv")
def export_csv(user: User = Depends(current_user), db: Session = Depends(get_db)):
    albums = db.scalars(
        select(Item).where(Item.user_id == user.id, Item.kind == "album")
        .options(selectinload(Item.vibes), selectinload(Item.genres))
        .order_by(func.lower(Item.creator), Item.year, func.lower(Item.title))
    ).all()
    out = io.StringIO()
    out.write("\ufeff")  # BOM so Excel reads UTF-8 (accents, Japanese titles...) correctly
    w = csv.writer(out)
    w.writerow(["title", "artist", "year", "vibes", "genres", "spotify_url", "notes", "added"])
    for a in albums:
        w.writerow([a.title, a.creator, a.year or "", ", ".join(v.name for v in a.vibes),
                    ", ".join(g.name for g in a.genres), a.spotify_url or "", a.notes or "",
                    a.created_at.date().isoformat() if a.created_at else ""])
    filename = f"music-vibe-{date.today().isoformat()}.csv"
    return Response(out.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


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

# Every new account starts with these.
STARTER_VIBES = [("winter", "#9ec9ff"), ("spring", "#9be39b"), ("summer", "#ffd24a"), ("fall", "#d9822b")]


@app.get("/vibes", response_class=HTMLResponse)
def vibes_page(request: Request, user: User = Depends(current_user),
               db: Session = Depends(get_db)):
    counts = dict(
        db.execute(
            select(Vibe.id, func.count(Item.id)).join(Item.vibes).where(Vibe.user_id == user.id).group_by(Vibe.id)
        ).all()
    )
    return render(request, "vibes.html", user=user, vibes=_user_vibes(db, user), counts=counts)


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
