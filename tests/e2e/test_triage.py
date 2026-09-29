import pytest
from playwright.sync_api import expect

from app import deezer, musicbrainz
from app.models import Item

ALBUMS = {
    "1": {"title": "Loveless", "artist": "My Bloody Valentine", "year": 2012},
    "2": {"title": "Kid A", "artist": "Radiohead", "year": 2000},
}


@pytest.fixture(autouse=True)
def fake_services(monkeypatch):
    """Deezer knows the albums above; MusicBrainz has detailed genres for Loveless only."""
    async def search_albums(query, limit=12):
        return [{"source": "deezer", "id": i, "title": a["title"], "artist": a["artist"], "year": None,
                 "type": "Album", "thumb": ""}
                for i, a in ALBUMS.items() if a["artist"].casefold() in query.casefold()]

    async def get_album(album_id):
        a = ALBUMS[album_id]
        return {"external_id": f"deezer:{album_id}", "title": a["title"], "artist": a["artist"],
                "year": a["year"], "genres": ["rock"], "cover_url": ""}

    async def find_details(title, artist):
        return {"genres": ["shoegaze", "dream pop"], "year": 1991} if title == "Loveless" else {}

    monkeypatch.setattr(deezer, "search_albums", search_albums)
    monkeypatch.setattr(deezer, "get_album", get_album)
    monkeypatch.setattr(musicbrainz, "find_details", find_details)


def _vibe(page, name):
    return page.get_by_role("checkbox", name=name)


def test_import_then_tag_with_keys(logged_in, db):
    page = logged_in
    page.goto("/albums/import")
    page.fill("textarea[name=text]", "My Bloody Valentine - Loveless\nRadiohead - Kid A")
    page.get_by_role("button", name="Find matches").click()
    page.get_by_role("button", name="Add selected").click()
    page.get_by_role("link", name="Tag these 2 →").click()

    expect(page.get_by_role("heading", name="Loveless")).to_be_visible()
    progress = page.locator(".triage-progress")
    expect(progress).to_contain_text("Album 1 of 2")
    expect(progress).to_contain_text("0 tagged · 2 left")
    # MusicBrainz replaces Deezer's "rock" and fixes the reissue year
    expect(page.locator(".genre-chip")).to_have_text(["dream pop×", "shoegaze×"])
    expect(page.locator("#triage-year")).to_have_text(" · 1991")

    # new accounts have fall, spring, summer, winter: keys 1-4
    page.keyboard.press("1")
    page.keyboard.press("4")
    expect(_vibe(page, "fall")).to_be_checked()
    expect(_vibe(page, "winter")).to_be_checked()
    expect(progress).to_contain_text("1 tagged · 1 left")  # counts albums, not vibes
    page.keyboard.press("4")  # toggles back off
    expect(_vibe(page, "winter")).not_to_be_checked()
    page.keyboard.press("1")
    expect(progress).to_contain_text("0 tagged · 2 left")
    page.keyboard.press("1")
    page.keyboard.press("ArrowRight")

    expect(page.get_by_role("heading", name="Kid A")).to_be_visible()
    expect(progress).to_contain_text("1 tagged · 1 left")  # Loveless, saved before leaving
    expect(page.locator(".genre-chip")).to_have_text(["rock×"])  # no MusicBrainz genres: Deezer's stay
    expect(page.locator("#triage-genres")).not_to_contain_text("Checking MusicBrainz")
    page.keyboard.press("ArrowLeft")
    expect(page.get_by_role("heading", name="Loveless")).to_be_visible()
    expect(_vibe(page, "fall")).to_be_checked()
    expect(_vibe(page, "winter")).not_to_be_checked()

    page.keyboard.press("ArrowRight")
    expect(page.get_by_role("link", name="Finish →")).to_be_visible()
    page.keyboard.press("Enter")
    expect(page.get_by_text("That's all 2 albums.")).to_be_visible()
    expect(page.get_by_text("1 of them has vibes now.")).to_be_visible()


def test_moving_on_waits_for_the_save(logged_in, make_album, db):
    a, b = make_album("Slow"), make_album("Next")
    page = logged_in
    held = []
    page.route("**/triage/*/vibes", lambda route: held.append(route))  # hold the save until we let it go
    page.goto(f"/triage?ids={a.id},{b.id}")
    expect(page.get_by_role("heading", name="Slow")).to_be_visible()
    page.keyboard.press("3")  # summer
    while not held:
        page.wait_for_timeout(20)
    page.keyboard.press("ArrowRight")  # straight away
    page.wait_for_timeout(300)
    expect(page.get_by_role("heading", name="Slow")).to_be_visible()  # still here: leaving would drop the save
    held[0].continue_()
    expect(page.get_by_role("heading", name="Next")).to_be_visible()
    db.expire_all()
    assert [v.name for v in db.get(Item, a.id).vibes] == ["summer"]


def test_edit_genres(logged_in, make_album, db):
    a = make_album("Album", genres=["pop", "rock"], genres_checked=True)
    b = make_album("After", genres_checked=True)
    page = logged_in
    page.goto(f"/triage?ids={a.id},{b.id}")
    page.get_by_role("button", name="Remove pop").click()
    expect(page.locator(".genre-chip")).to_have_text(["rock×"])
    box = page.get_by_role("combobox", name="Add a genre")
    box.fill("krautrock")
    box.press("Enter")  # adds the genre, doesn't move to the next album
    expect(page.locator(".genre-chip")).to_have_text(["krautrock×", "rock×"])
    expect(box).to_have_value("")
    expect(page.get_by_role("heading", name="Album")).to_be_visible()
    db.expire_all()
    assert [g.name for g in db.get(Item, a.id).genres] == ["krautrock", "rock"]
    # removing a genre swaps out the button that saved it; that save must still count as finished
    page.get_by_role("link", name="Next →").click()
    expect(page.get_by_role("heading", name="After")).to_be_visible()


def test_remove_from_library(logged_in, make_album, db):
    a, b = make_album("Wrong match").id, make_album("Keeper").id
    page = logged_in
    page.goto(f"/triage?ids={a},{b}")
    page.once("dialog", lambda d: d.accept())
    page.get_by_role("button", name="Remove from library").click()
    expect(page.get_by_role("heading", name="Keeper")).to_be_visible()
    expect(page.locator(".triage-progress")).to_contain_text("1 of 1")
    db.expire_all()
    assert db.get(Item, a) is None


def test_swipe_on_a_phone(browser, base_url, user, make_album, db):
    a, b = make_album("First", "Someone", 1990, genres=["rock"], genres_checked=True), make_album("Second")
    context = browser.new_context(base_url=base_url, viewport={"width": 390, "height": 844}, has_touch=True,
                                  is_mobile=True)
    page = context.new_page()
    page.set_default_timeout(5000)
    page.goto("/login")
    page.fill("input[name=username]", "tester")
    page.fill("input[name=password]", "password1")
    page.click("button[type=submit]")
    page.wait_for_url("**/")
    page.goto(f"/triage?ids={a.id},{b.id}")
    _vibe(page, "spring").tap()
    expect(_vibe(page, "spring")).to_be_checked()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")  # no sideways scrolling

    page.evaluate("""([x0, x1]) => {
        const card = document.querySelector("[data-triage-card] .cover");
        const touch = (x) => new Touch({identifier: 1, target: card, clientX: x, clientY: 300});
        card.dispatchEvent(new TouchEvent("touchstart", {bubbles: true, touches: [touch(x0)], changedTouches: [touch(x0)]}));
        card.dispatchEvent(new TouchEvent("touchmove", {bubbles: true, touches: [touch(x1)], changedTouches: [touch(x1)]}));
        card.dispatchEvent(new TouchEvent("touchend", {bubbles: true, touches: [], changedTouches: [touch(x1)]}));
    }""", [320, 80])
    expect(page.get_by_role("heading", name="Second")).to_be_visible()
    db.expire_all()
    assert [v.name for v in db.get(Item, a.id).vibes] == ["spring"]
    context.close()
