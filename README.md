# music vibe

A personal album library where you tag albums with your own *vibes* (winter, melancholic, running…)
and browse by vibe, genre and year. Windows 98 in purple.

## Run it

```sh
cp .env.example .env          # optional; see comments inside
docker compose up -d --build
```

Open `http://<this machine>:8420` (port set by `PORT` in `.env`; over Tailscale: `http://<tailscale-hostname>:8420`).
The first account can be created without a code and becomes the admin. If `ADMIN_USERNAMES` is set, only a name
listed there can take that first seat (do this on anything public), and invitees can't take those names. After that,
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

## Hosting on Fly.io

New to Fly? [docs/hosting-on-fly.md](docs/hosting-on-fly.md) walks through everything step by step (sign-up,
billing, custom domain, moving your library over). The short version: `fly.toml` runs the same Docker
image on one small machine with a volume for `/data`. HTTPS comes
built in (`https://<app>.fly.dev`). Install `flyctl` and run `fly auth login`, then from this folder:

```sh
fly launch --no-deploy --copy-config        # creates the app; rename it if "music-vibe" is taken
fly volumes create music_vibe_data --size 1 --region <same region as fly.toml>
fly secrets set ADMIN_USERNAMES=<your username> MB_CONTACT=you@example.com SPOTIFY_CLIENT_ID=... SPOTIFY_CLIENT_SECRET=...
fly deploy
```

Open the site, register as the name in `ADMIN_USERNAMES` (no code needed for that one), then hand out invite codes.
Without `ADMIN_USERNAMES`, whoever registers first on the public URL becomes admin.

**Backups.** Fly snapshots the volume daily (kept 14 days, set in `fly.toml`), which covers everything
including covers. For the database, also stream every change off the machine with Litestream to Tigris,
Fly's S3 storage:

```sh
fly storage create                          # sets the AWS_* secrets and prints the bucket name
fly secrets set LITESTREAM_REPLICA_URL="s3://<bucket>/music_vibe.db?endpoint=fly.storage.tigris.dev&region=auto"
```

If the volume is ever lost, create a new one and deploy: on start the app restores the database from
the replica. Admin commands: `fly ssh console -C "python -m app.manage users"`.

The machine sleeps when idle (`auto_stop_machines`) so it costs less; the first visit after that takes a
few seconds. Set `min_machines_running = 1` in `fly.toml` if that bothers you.

### Dev mode

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
./run.sh [port]             # same, on localhost: makes the venv if missing, loads .env, allows plain http
HOST=tailscale ./run.sh     # reachable from your tailnet (only) instead
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
- HTTPS elsewhere: set `HTTPS_ONLY=true` once served behind TLS (e.g. `tailscale serve` or a reverse proxy).
  Set `FORWARDED_ALLOW_IPS` to the proxy's address so uvicorn believes its `X-Forwarded-Proto`. The rate limits
  take the client IP from uvicorn, so behind a proxy that appends to `X-Forwarded-For` (as Fly does) use a header
  the proxy sets itself instead, like `client_ip()` does with Fly-Client-IP.
