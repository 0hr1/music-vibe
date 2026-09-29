# Notes for Claude

## Testing

- `.venv/bin/pytest` runs everything. Tests use a throwaway data dir and never touch the network
  (fake Deezer etc. with monkeypatch).
- **Browser tests live in `tests/e2e/`** (pytest-playwright, headless Chromium). The app runs in a
  thread of the test process, so the fixtures in `tests/conftest.py` (`make_album`, `make_vibe`, ...)
  and monkeypatching reach it. Use the `logged_in` fixture for a page logged in as `tester`.
- Any change to JavaScript, htmx or page flow needs an e2e test, or at least a screenshot you look at
  yourself (`page.screenshot(path=...)`, then read the image) before calling it done. Rendering the
  HTML with TestClient doesn't prove a button works.
- In e2e tests, find elements by role (`page.get_by_role("button", name=...)`) or data attribute, not
  `text=`: the taskbar ("+ Add", "Vibes") matches a lot of button text.
- Browser tests are sorted to run last (see `tests/conftest.py`), because Playwright leaves an event
  loop running that breaks later `asyncio.run()` tests.
- The user works over SSH, so Claude in Chrome can't reach their browser; use Playwright.

## Changing the database layout

A deploy upgrades the live database in place, on startup. Any change to a table in `app/models.py`
needs a matching step appended to `STEPS` in `app/migrations.py` (never edit or reorder old steps), then
`.venv/bin/python tests/schemas/save_schema.py` to save the new layout. `tests/test_schema_upgrades.py`
upgrades every saved layout and checks it matches a fresh database; if it fails, fix the step or model
(e.g. a NOT NULL column added by ALTER TABLE needs a `server_default` in the model too), not the test.

## Running

- `./run.sh [port]` for a local dev server; `HOST=tailscale ./run.sh` to reach it from the tailnet.
