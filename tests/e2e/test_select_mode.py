import re

from playwright.sync_api import expect


def test_select_all_none_and_done(logged_in, make_album):
    for title in ("One", "Two", "Three"):
        make_album(title)
    page = logged_in
    page.goto("/")
    count, none = page.locator("[data-bulk-count]"), page.locator("[data-select-none]")

    page.click("[data-select-mode][aria-pressed]")
    expect(page.locator(".library")).to_have_class(re.compile(r"\bselecting\b"))
    expect(none).to_be_disabled()

    page.click(".card:has-text('Two')")  # clicking a card ticks it rather than opening it
    expect(count).to_have_text("1")
    expect(page).to_have_url(re.compile(r"/$"))
    expect(none).to_be_enabled()

    page.click("[data-select-all]")
    expect(count).to_have_text("3")
    page.click("[data-select-all]")  # All only ever selects
    expect(count).to_have_text("3")

    page.click("[data-select-none]")
    expect(count).to_have_text("0")
    expect(page.locator(".select-box:checked")).to_have_count(0)
    expect(page.locator(".library")).to_have_class(re.compile(r"\bselecting\b"))  # still in select mode

    page.click(".card:has-text('One')")
    page.get_by_role("button", name="Done").click()
    expect(page.locator(".library")).not_to_have_class(re.compile(r"\bselecting\b"))
    expect(page.locator(".select-box:checked")).to_have_count(0)


def test_bulk_add_vibe(logged_in, make_album, make_vibe):
    make_vibe("winter")
    for title in ("One", "Two", "Three"):
        make_album(title)
    page = logged_in
    page.goto("/")

    page.click("[data-select-mode][aria-pressed]")
    page.click(".card:has-text('One')")
    page.click(".card:has-text('Three')")
    page.select_option("select[name=bulk_vibe]", label="winter")
    page.get_by_role("button", name="+ Add vibe").click()

    expect(page.locator(".card:has-text('One') .chip")).to_have_text("winter")
    expect(page.locator(".card:has-text('Three') .chip")).to_have_text("winter")
    expect(page.locator(".card:has-text('Two') .chip")).to_have_count(0)


def test_library_fits_a_phone(logged_in, make_album):
    make_album("Untagged")  # so every toolbar button shows
    page = logged_in
    page.set_viewport_size({"width": 360, "height": 740})
    page.goto("/")
    expect(page.get_by_role("link", name="+ Add album")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")  # no sideways scrolling


def test_bulk_edits_update_the_untagged_button(logged_in, make_album, make_vibe):
    make_vibe("winter")
    for title in ("One", "Two"):
        make_album(title)
    page = logged_in
    page.goto("/")
    button = page.get_by_role("link", name=re.compile("untagged"))
    expect(button).to_have_text("🏷 Tag 2 untagged")
    page.click("[data-select-mode][aria-pressed]")
    page.click(".card:has-text('One')")
    page.select_option("select[name=bulk_vibe]", label="winter")
    page.get_by_role("button", name="+ Add vibe").click()
    expect(button).to_have_text("🏷 Tag 1 untagged")
    page.click("[data-select-all]")
    page.get_by_role("button", name="+ Add vibe").click()
    expect(button).to_be_hidden()
    page.click("[data-select-all]")
    page.get_by_role("button", name="− Remove vibe").click()
    expect(button).to_have_text("🏷 Tag 2 untagged")


def test_selection_outlasts_filter_changes(logged_in, make_album, make_vibe, db):
    """Select some albums, search for others and select those too: a bulk edit covers them all."""
    make_vibe("winter")
    for title in ("Alpha", "Beta", "Gamma"):
        make_album(title)
    page = logged_in
    page.goto("/")
    count = page.locator("[data-bulk-count]")
    search = page.get_by_role("searchbox")

    page.click("[data-select-mode][aria-pressed]")
    page.click(".card:has-text('Alpha')")
    search.fill("Beta")
    expect(page.locator(".card")).to_have_count(1)
    expect(count).to_have_text("1")
    expect(page.locator(".bulk-count")).to_contain_text("(1 not shown)")
    page.click(".card:has-text('Beta')")
    expect(count).to_have_text("2")

    page.select_option("select[name=bulk_vibe]", label="winter")
    page.get_by_role("button", name="+ Add vibe").click()
    expect(page.locator(".status-message")).to_have_text("Added “winter” to 2 albums.")

    search.fill("")
    expect(page.locator(".card")).to_have_count(3)
    expect(page.locator(".select-box:checked")).to_have_count(2)
    expect(page.locator(".card:has-text('Alpha') .chip")).to_have_text("winter")
    expect(page.locator(".card:has-text('Gamma') .chip")).to_have_count(0)

    # None clears albums out of view too; leaving select mode forgets the selection
    search.fill("Gamma")
    expect(page.locator(".card")).to_have_count(1)
    page.click("[data-select-none]")
    expect(count).to_have_text("0")
    page.click(".card:has-text('Gamma')")
    page.get_by_role("button", name="Done").click()
    page.click("[data-select-mode][aria-pressed]")
    expect(count).to_have_text("0")


def test_bulk_delete_clears_the_selection(logged_in, make_album):
    for title in ("Alpha", "Beta", "Gamma"):
        make_album(title)
    page = logged_in
    page.goto("/")
    page.on("dialog", lambda d: d.accept())
    page.click("[data-select-mode][aria-pressed]")
    page.click(".card:has-text('Alpha')")
    page.get_by_role("searchbox").fill("Beta")
    expect(page.locator(".card")).to_have_count(1)
    page.click(".card:has-text('Beta')")
    page.get_by_role("button", name="Delete").click()
    expect(page.locator(".status-message")).to_have_text("Deleted 2 albums.")
    expect(page.locator("[data-bulk-count]")).to_have_text("0")
    page.get_by_role("searchbox").fill("")
    expect(page.locator(".card")).to_have_count(1)
    expect(page.locator(".card")).to_contain_text("Gamma")
