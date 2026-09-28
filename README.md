# music vibe

A personal album library where you tag albums with your own *vibes* (winter, melancholic, running…)
and browse by vibe, genre and year. Windows 98 in purple.

## Run it

```sh
cp .env.example .env          # optional; see comments inside
docker compose up -d --build
```

Open `http://<this machine>:8420` (port set by `PORT` in `.env`; over Tailscale: `http://<tailscale-hostname>:8420`).
The first account can always be created, and it becomes the admin (or set `ADMIN_USERNAMES`). After that,
people need an **invite code**: click your name in the taskbar, then *Invite codes & users*, and send them the
sign-up link. Each code can be limited to a number of accounts and days. `ALLOW_SIGNUP=true` drops the need for codes.
The same page resets a forgotten password or deletes an account.

Locked out? The same admin tasks work from a shell in the container:

```sh
docker compose exec music-vibe python -m app.manage users
docker compose exec music-vibe python -m app.manage reset-password <username>
docker compose exec music-vibe python -m app.manage invite --uses 1 --days 14
```

Everything lives in `./data` (SQLite database, cover images, session key). Back up or move that folder
to move the app to another server.

The app snapshots the database into `data/backups` once a day and keeps the newest 14 (`BACKUP_KEEP`).
To restore, stop the app and copy a snapshot over `data/music_vibe.db` (delete the `-wal`/`-shm` files next
to it). Covers aren't in the snapshots; they're the plain files in `data/covers`. Each user can also download
their library as CSV from the Stats page.

### Dev mode

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
.venv/bin/pytest            # tests use a throwaway data dir and never touch the network
```

## How adding albums works

- Search hits **Deezer** first (fast, no key), and **MusicBrainz** results load underneath for anything Deezer lacks.
- Picking a Deezer result fills the form instantly, then asks MusicBrainz in the background for detailed
  genres and the *original* release year (Deezer often reports reissue dates). MusicBrainz can be slow;
  if it doesn't answer, Deezer's data stays and you can edit anything.
- Covers are downloaded and stored locally.
- With `SPOTIFY_CLIENT_ID`/`SPOTIFY_CLIENT_SECRET` in `.env` (an app from developer.spotify.com/dashboard;
  the owner needs Premium), the Spotify link is looked up automatically and a **Find** button re-runs it.

## Later

- Other item kinds (movies, wallpapers): the `items` table already has a `kind` column
- HTTPS: set `HTTPS_ONLY=true` once served behind TLS (e.g. `tailscale serve` or a reverse proxy)
