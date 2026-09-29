# Hosting music vibe on Fly.io: a first-timer's guide

This takes you from "no Fly account" to "the app is live at `https://<something>`, with backups, and
your library moved over". Plan on about an hour the first time. Everything Fly needs is already in the
repo (`fly.toml`, `Dockerfile`, `deploy/`), so you won't be editing code, only running commands.

Prices and trial rules below were checked against Fly's docs on 2026-09-29. They change now and then;
the source of truth is <https://fly.io/docs/about/pricing/>.

---

## 0. What Fly actually is (the two-minute version)

- **Fly.io** runs Docker containers for you on their servers. You don't rent a whole server and log
  into it like a VPS. You hand Fly an image and it runs it on a small VM, which Fly calls a **Machine**.
- **An app** is Fly's name for a project. Ours is `music-vibe`. It gets a free public address,
  `https://music-vibe.fly.dev`, and HTTPS comes with it.
- **A volume** is a disk attached to the machine. The machine itself is thrown away and rebuilt on
  every deploy; the volume survives. Our database, covers and session key live on it, mounted at `/data`,
  which is the same as the `./data` folder you have at home.
- **`flyctl`** (the command is `fly`) is the command-line tool you use for everything: creating the
  app, deploying, reading logs, opening a shell.
- **Secrets** are environment variables Fly stores encrypted and passes to the app, the Fly version of
  your `.env` file.
- **You don't need Docker installed.** `fly deploy` sends the code to a remote builder of Fly's, builds
  the image there, and runs it.

How our setup looks once it's running:

```
 browser ──HTTPS──▶ Fly's edge (handles TLS/certs) ──HTTP──▶ your Machine :8000 ──▶ /data volume
                                                                   │
                                                                   └─ Litestream ──▶ Tigris bucket (DB backup)
```

---

## 1. Sign up and pay

### 1.1 Create the account

1. Go to <https://fly.io/app/sign-up>. Signing in with GitHub or Google works, or use an email address.
2. Confirm your email if asked.
3. **Turn on two-factor authentication** (Account → Settings). This account will hold your card and
   everyone's data.

### 1.2 The free trial, and why you should add a card straight away

New accounts start on a trial with no card needed, but it's small: **2 hours of machine runtime or 7
days, whichever comes first**. Also, **trial machines stop themselves after 5 minutes**. That's fine for
poking around, but it makes the site keep going offline, which is confusing when you're testing whether
it works.

Adding a card ends the trial and switches you to normal pay-as-you-go billing. For a small app that's
a few dollars a month (see below), so just do it:

**Dashboard → Billing → add a payment method.** (If the trial runs out without a card, apps stop and
you can't deploy until you add one. Nothing is deleted.)

### 1.3 What it will cost

Fly bills your card once a month for what you used, prorated by the hour. For this app:

| Thing | Price (approx.) | Notes |
|---|---|---|
| Machine, `shared-cpu-1x` with 512 MB | ~$2.60/month if it ran 24/7 in the US; a bit more in some regions, e.g. Frankfurt | It **sleeps when nobody's using it** (`auto_stop_machines`), and you pay only while it runs, so usually less |
| Volume, 1 GB | $0.15/month | Charged whether the machine runs or not |
| Volume snapshots (daily, 14 days) | First 10 GB/month free | Ours are tiny |
| IP addresses (shared IPv4 + IPv6) | Free | A *dedicated* IPv4 is $2/month and you don't need it |
| HTTPS certificates | First 10 hostnames free | |
| Outbound traffic | ~$0.02/GB in NA/Europe | Negligible for a few users |
| Tigris backup bucket | A few cents at our size | Billed through Fly |
| **Custom domain** (optional, not from Fly) | ~$10–15/**year** | Bought from a registrar; see step 6 |

Expect roughly **$1–4 a month**, plus a domain if you want one. Check the Billing page after the first
week to see what you're actually spending.

---

## 2. Install `flyctl` and log in

On this Linux box (and/or your Mac):

```sh
# Linux / macOS
curl -L https://fly.io/install.sh | sh
# macOS alternative:  brew install flyctl
```

The installer prints two lines to add to your shell config (`~/.bashrc` or `~/.zshrc`). They look like:

```sh
export FLYCTL_INSTALL="$HOME/.fly"
export PATH="$FLYCTL_INSTALL/bin:$PATH"
```

Add them, open a new terminal, then:

```sh
fly version        # check it's installed
fly auth login     # opens the browser; log in and come back
fly auth whoami    # should print your email
```

---

## 3. Create the app (nothing runs yet)

From the repo folder:

```sh
cd ~/Documents/projects/music_vibe
fly launch --no-deploy --copy-config
```

What that does and what it'll ask:

- `--copy-config` means "use our `fly.toml`, don't generate a new one". `--no-deploy` means "just
  create the app, don't start anything yet".
- It shows a summary (name, region, VM size) and asks **"Do you want to tweak these settings?"**
  Answer **no** unless the name is taken.
- **App name**: `music-vibe` is probably taken, since names are global across Fly. If so, pick another
  (e.g. `music-vibe-ori`). That name becomes your URL: `https://<name>.fly.dev`. Fly writes it back
  into `fly.toml`.
- It may offer a Postgres or Redis database. **Say no**, since we use SQLite on the volume.

**Region.** `fly.toml` says `primary_region = "fra"` (Frankfurt). Pick whatever is closest to you and
your users; list them with `fly platform regions`. To change it, edit `primary_region` in `fly.toml`
before going on. If you change the name or region, commit `fly.toml` so the repo matches reality.

---

## 4. The volume and secrets

### 4.1 Create the volume (the disk for `/data`)

Use the **same region** as `primary_region` in `fly.toml`:

```sh
fly volumes create music_vibe_data --size 1 --region fra
```

It warns that one volume means no redundancy and asks whether to continue. Say **yes**. That's fine
here, because SQLite can only live on one machine anyway, and the backups in step 7 are the safety net.
1 GB is lots for a database and covers, and you can grow it later
(`fly volumes extend <id> --size 3`). You can't shrink it.

### 4.2 Set the secrets

Copy the values from your local `.env`:

```sh
fly secrets set \
  MB_CONTACT="you@example.com" \
  SPOTIFY_CLIENT_ID="..." \
  SPOTIFY_CLIENT_SECRET="..." \
  ADMIN_USERNAMES="<the username you'll register with>"
```

- `MB_CONTACT` is what MusicBrainz asks for in the User-Agent (an email or URL).
- The Spotify pair is optional. It uses client credentials, so there's no redirect URL to configure on
  Spotify's side for the new domain.
- `ADMIN_USERNAMES` is a safety net. Normally the **first account to register becomes admin**, and
  on a public URL that could, in theory, be a stranger who finds it before you do. With this set, only
  that username gets admin rights no matter who signs up first.
- **Don't** run `fly secrets import < .env`. Your `.env` has `HTTPS_ONLY=false` and `PORT`, which are
  wrong for Fly. `fly.toml` already sets `DATA_DIR=/data` and `HTTPS_ONLY=true`.
- `SECRET_KEY` isn't needed. The app generates one and keeps it in `/data/secret_key`, so it
  survives deploys.

List what's set (names only, values stay hidden) with `fly secrets list`.

---

## 5. Deploy

```sh
fly deploy
```

The first run takes a few minutes: it builds the image remotely, pushes it, creates the machine,
attaches the volume and gives the app its IP addresses (a shared IPv4 and an IPv6). When it finishes:

```sh
fly status     # should show ONE machine, state "started", checks passing
fly logs       # live server logs; Ctrl+C to leave
fly open       # opens https://<name>.fly.dev in your browser
```

**One machine only.** SQLite on a volume can't be shared, so there must be exactly one machine.
Fly normally creates just one when a volume is involved, but if `fly status` ever lists two, run
`fly scale count 1`.

**Right away:** open the site and **register your account** (with the username you put in
`ADMIN_USERNAMES`). Then hand out invite codes from *your name → Invite codes & users*, same as at home.

> Moving your existing library? Do it **before** you add anything on Fly. See step 8.

---

## 6. HTTPS

### 6.1 On `*.fly.dev`: already done

There's nothing to set up. Fly's edge holds the certificate for `https://<name>.fly.dev` and handles
TLS. Our config then does two things:

- `force_https = true` in `fly.toml`: anyone who types `http://` gets redirected to `https://`.
- `HTTPS_ONLY = "true"` in `fly.toml`: the app marks the login cookie as secure-only and adds security
  headers. Uvicorn runs with `--proxy-headers`, so it trusts Fly's proxy about the request being HTTPS.

You can stop here and use the `fly.dev` address for good.

### 6.2 Your own domain (optional)

If you want `music.yourname.com` or `yourname.com`:

1. **Buy a domain** from a registrar: Cloudflare Registrar, Porkbun and Namecheap are all fine, about
   $10–15/year for a `.com`. The registrar is also where you'll edit **DNS records**, the entries that
   tell the internet which server a name points to.

2. **Tell Fly about the hostname**, which makes Fly get a free Let's Encrypt certificate for it:

   ```sh
   fly certs add music.yourname.com
   ```

   It prints the DNS records to create. You can also get the IPs with `fly ips list`.

3. **Create the DNS records** at your registrar:

   - For a **subdomain** like `music.yourname.com` (the easiest option): one **CNAME** record,
     name `music`, value `<name>.fly.dev`.
   - For the **bare domain** `yourname.com`: an **A** record (name `@`) pointing to the *shared
     IPv4* from `fly ips list`, and an **AAAA** record (name `@`) pointing to the *IPv6*.

   **If your DNS is at Cloudflare:** set these records to **"DNS only" (grey cloud)**, not "Proxied"
   (orange). Otherwise Cloudflare sits in front of Fly and the certificate check fails.

4. **Wait and check.** DNS can take anywhere from a minute to an hour or so.

   ```sh
   fly certs check music.yourname.com    # repeat until it says the certificate is issued
   ```

   After that, `https://music.yourname.com` works and renews by itself. The `fly.dev` address keeps
   working too. Send people the new one in invite links.

---

## 7. Backups

There are three layers. The first two are on already, and the third takes two commands.

1. **Fly volume snapshots**: daily, kept 14 days (`snapshot_retention` in `fly.toml`). These include
   the covers. See them with `fly volumes list` then `fly volumes snapshots list <volume-id>`.
2. **The app's own daily DB snapshots** in `/data/backups`, the same as at home. They're on the same
   disk, though, so they don't protect against losing the volume.
3. **Litestream to Tigris**: streams every database change off the machine within seconds. Turn it on:

   ```sh
   fly storage create
   ```

   That creates a Tigris bucket (Fly's S3-compatible storage), sets the `AWS_*` secrets on the app for
   you, and **prints the bucket name**. Then:

   ```sh
   fly secrets set LITESTREAM_REPLICA_URL="s3://<bucket>/music_vibe.db?endpoint=fly.storage.tigris.dev&region=auto"
   ```

   Setting a secret restarts the app, and from then on it runs under Litestream. `fly logs` should show
   Litestream starting to replicate.

   If the volume is ever lost: `fly volumes create music_vibe_data --size 1 --region fra` and then
   `fly deploy`. On start, the app sees an empty disk and restores the database from Tigris. Covers
   would come back from a volume snapshot instead
   (`fly volumes create music_vibe_data --snapshot-id <id> ...`).

**Order matters if you're migrating:** do step 8 first, then turn on Litestream, so the backup starts
from your real library.

Now and then, it's worth downloading a copy to your own computer (Stats page → CSV export, or
`fly ssh sftp get /data/backups/<newest>.db`).

---

## 8. Moving your existing library from this box (optional)

Do this on a fresh Fly deploy before anyone uses it, because it replaces the database on Fly.

**On this box**, make a clean copy of the database and pack everything up:

```sh
cd ~/Documents/projects/music_vibe
# consistent DB copy (safe while the app runs); prints the path, e.g. /data/backups/music_vibe-2026....db
docker compose exec music-vibe python -c "from app.backup import make_backup; print(make_backup())"
cp data/backups/music_vibe-<that timestamp>.db /tmp/music_vibe.db
tar czf /tmp/covers.tgz -C data covers
```

**Upload to Fly** (the machine must be running, so open the site first if it's asleep):

```sh
fly ssh sftp shell
» put /tmp/music_vibe.db /data/music_vibe.db.new
» put /tmp/covers.tgz /data/covers.tgz
» (Ctrl+D to exit)
```

**Swap it in** and restart:

```sh
fly ssh console
# now you're inside the machine:
cd /data
mv music_vibe.db.new music_vibe.db
rm -f music_vibe.db-wal music_vibe.db-shm
tar xzf covers.tgz && rm covers.tgz
exit

fly apps restart          # the restart also fixes file ownership (see deploy/entrypoint.sh)
```

Log in with your **home** credentials, since the accounts came along. `secret_key` doesn't need
copying; everyone just logs in once more. Then go back and turn on Litestream (step 7.3).

Once you're happy with Fly, you can stop the home copy (`docker compose down`). Keep its `data` folder
around for a while as one more backup.

---

## 9. Everyday use

| Want to… | Run |
|---|---|
| Ship code changes | `fly deploy` (from the repo, after committing) |
| See what's happening | `fly logs` / `fly status` |
| Open the site | `fly open` |
| List users, reset a password, make an invite | `fly ssh console -C "python -m app.manage users"` (also `reset-password <user>`, `invite --uses 1 --days 14`) |
| Get a shell on the machine | `fly ssh console` |
| Restart | `fly apps restart` |
| Roll back a bad deploy | `fly releases` to find the last good image, then `fly deploy --image <that image>` |
| Change a secret | `fly secrets set NAME=value` (restarts the app) |
| Stop the app from sleeping | set `min_machines_running = 1` in `fly.toml`, then `fly deploy` (costs the full ~$3/month) |
| See the bill | fly.io dashboard → Billing |
| Tear everything down | `fly apps destroy <name>` (**deletes the volume too**; download a backup first) |

About the sleeping: after a few idle minutes the machine stops, and the next visitor waits a few
seconds while it boots. Daily snapshots and the in-app backup still happen whenever it's awake.

---

## 10. When something goes wrong

- **"Site can't be reached" / keeps going down after a few minutes**: you're probably still on the
  trial (machines stop after 5 minutes). Add a card (step 1.2).
- **Deploy fails with something about the volume or mounts**: the volume doesn't exist, or it's in a
  different region from `primary_region`. Check with `fly volumes list`.
- **Health check failing / machine keeps restarting**: `fly logs` shows the Python error.
  `/healthz` must answer within 5 s.
- **Login doesn't stick**: make sure you're using `https://`. The cookie is secure-only by design.
- **Custom domain shows a certificate error**: DNS hasn't propagated yet, or Cloudflare proxying is
  on. Run `fly certs check <host>`, which says what it's waiting for.
- **Spotify links stop being found**: re-check `fly secrets list` for both Spotify variables.
- **Locked out of the admin account**: `fly ssh console -C "python -m app.manage reset-password <you>"`.

Fly's docs are at <https://fly.io/docs/>, and there's a community forum at <https://community.fly.io/>.
