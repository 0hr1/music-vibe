# music vibe

A personal album library where you tag albums with your own *vibes* (winter, melancholic, running…)
and browse by vibe, genre and year. Windows 98 in purple.

**Live at [music-vibe.fly.dev](https://music-vibe.fly.dev)** (sign-up is by invite).

## Develop

```sh
./run.sh                    # dev server on localhost:8000; makes the venv and loads .env
.venv/bin/playwright install chromium-headless-shell   # once, for the browser tests
.venv/bin/pytest            # all tests, in parallel; never touches the network
```

## Docs

- [Self-hosting](docs/self-hosting.md): Docker, invite codes, admin commands, backups
- [Hosting on Fly.io](docs/hosting-on-fly.md): step-by-step deploy guide
