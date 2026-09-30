"""Phone-sized screens: nothing covers something you need to tap or read."""

import pytest
from playwright.sync_api import expect

PHONES = [320, 360, 390]


def _box(locator):
    return locator.bounding_box()


@pytest.mark.parametrize("width", PHONES)
def test_taskbar_tabs_arent_covered_by_the_tray(logged_in, width):
    page = logged_in
    page.set_viewport_size({"width": width, "height": 640})
    page.goto("/")
    tray = _box(page.locator(".tray"))
    for name in ("Library", "+ Add", "Vibes", "Stats"):
        tab = page.locator(".taskbar-nav").get_by_role("link", name=name, exact=True)
        tab.scroll_into_view_if_needed()
        box = _box(tab)
        assert box["x"] + box["width"] <= tray["x"], f"{name} runs under the tray"
    page.locator(".taskbar-nav").get_by_role("link", name="Stats").click(timeout=2000)
    expect(page).to_have_url("/stats")
    account = _box(page.get_by_role("link", name="Account"))
    assert account["width"] >= 32 and account["height"] >= 32  # big enough to tap


@pytest.mark.parametrize("width", PHONES)
def test_bulk_bar_doesnt_hide_the_last_albums(logged_in, make_album, width):
    for i in range(7):
        make_album(f"Album {i}")
    page = logged_in
    page.set_viewport_size({"width": width, "height": 640})
    page.goto("/")
    page.get_by_role("button", name="☑ Select").click()
    bar = page.locator(".bulk-bar")
    expect(bar).to_be_visible()
    page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
    page.wait_for_timeout(100)
    last = _box(page.locator(".card").last)
    assert last["y"] + last["height"] <= _box(bar)["y"]


@pytest.mark.parametrize("width", PHONES)
def test_name_boxes_stay_wide_enough_to_read(logged_in, make_album, width):
    make_album("Tagged", genres=["dream pop"])
    page = logged_in
    page.set_viewport_size({"width": width, "height": 640})
    for path, box in (("/vibes", "Name"), ("/genres", "Genre name"), ("/admin", "Note")):
        page.goto(path)
        for field in page.get_by_role("textbox", name=box, exact=True).all():
            assert _box(field)["width"] >= 150, f"{path}: {box} box too narrow"
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), path
