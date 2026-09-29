"""The library's filter form: it updates the grid as you go, and Back puts both back in step."""

import re

from playwright.sync_api import expect


def _chip(page, name):
    return page.locator("#filter-form label.chip-toggle", has_text=name).locator("input")


def test_back_shows_the_filters_the_grid_was_filtered_by(logged_in, make_album, make_vibe):
    fall, summer = make_vibe("fall"), make_vibe("summer")
    make_album("Autumn", vibes=[fall])
    make_album("Both", vibes=[fall, summer])
    make_album("Neither")
    page = logged_in
    page.goto("/")

    _chip(page, "fall").click()
    expect(page.locator(".card")).to_have_count(2)
    _chip(page, "summer").click()
    expect(page.locator(".card")).to_have_count(1)

    page.go_back()
    expect(page).to_have_url(re.compile(rf"vibe={fall.id}"))
    expect(page.locator(".card")).to_have_count(2)
    expect(_chip(page, "fall")).to_be_checked()
    expect(_chip(page, "summer")).not_to_be_checked()

    # Filtering on from here builds on what's shown
    _chip(page, "summer").click()
    expect(page.locator(".card")).to_have_count(1)
    expect(page.locator(".card")).to_contain_text("Both")


def test_back_shows_the_search_the_grid_was_filtered_by(logged_in, make_album):
    make_album("Alpha")
    make_album("Beta")
    page = logged_in
    page.goto("/")
    search = page.get_by_role("searchbox")

    search.fill("Alpha")
    expect(page.locator(".card")).to_have_count(1)
    search.fill("Beta")
    expect(page.locator(".card")).to_contain_text("Beta")

    page.go_back()
    expect(page.locator(".card")).to_contain_text("Alpha")
    expect(page.get_by_role("searchbox")).to_have_value("Alpha")


def test_year_filter_applies_as_you_type(logged_in, make_album):
    make_album("Old", year=1970)
    make_album("New", year=2020)
    page = logged_in
    page.goto("/")
    page.get_by_role("spinbutton", name="From year").fill("2000")  # no Tab or Enter
    expect(page.locator(".card")).to_have_count(1)
    expect(page.locator(".card")).to_contain_text("New")
    expect(page).to_have_url(re.compile(r"year_min=2000"))
    page.get_by_role("spinbutton", name="To year").fill("1999")
    expect(page.locator(".card")).to_have_count(0)
