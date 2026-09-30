import re

from playwright.sync_api import expect


def _titles(page):
    return page.locator(".card-title").all_inner_texts()


def test_random_order_shuffle_and_keep(logged_in, make_album, make_vibe):
    make_vibe("winter")
    titles = [f"Album {i:02}" for i in range(12)]
    for t in titles:
        make_album(t)
    page = logged_in
    page.goto("/?sort=title&order=asc")
    expect(page.locator(".card-title").first).to_have_text("Album 00")
    shuffle = page.get_by_role("button", name="⤮ Shuffle")
    expect(shuffle).to_be_hidden()

    page.get_by_label("Order").select_option("random")
    expect(page).to_have_url(re.compile(r"order=random&seed=\d+"))
    expect(page.locator(".card-title")).to_have_count(12)
    shuffled = _titles(page)
    assert sorted(shuffled) == titles and shuffled != titles
    seed = re.search(r"seed=(\d+)", page.url)[1]

    # Re-rendering the grid keeps the order: a bulk edit, then a reload
    page.click("[data-select-mode][aria-pressed]")
    page.click(f".card:has-text('{shuffled[0]}')")
    page.select_option("select[name=bulk_vibe]", label="winter")
    page.get_by_role("button", name="+ Add vibe").click()
    expect(page.locator(".card").first.locator(".chip")).to_have_text("winter")
    assert _titles(page) == shuffled
    page.get_by_role("button", name="Done").click()
    page.reload()
    assert _titles(page) == shuffled

    # Shuffle deals a new order, straight away
    expect(shuffle).to_be_visible()
    shuffle.click()
    expect(page).not_to_have_url(re.compile(rf"seed={seed}\b"))
    expect(page.locator(".card-title")).not_to_have_text(shuffled)
    assert sorted(_titles(page)) == titles

    # Back to a normal order
    page.get_by_label("Order").select_option("asc")
    expect(page.locator(".card-title")).to_have_text(titles)
    expect(shuffle).to_be_hidden()
