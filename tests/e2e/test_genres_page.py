"""The Manage Genres page."""

from playwright.sync_api import expect

from app.models import Genre


def test_delete_works_after_emptying_the_name(logged_in, make_album, db):
    make_album("Tagged", genres=["dream pop"])
    page = logged_in
    page.goto("/genres")
    row = page.locator(".vibe-list li").filter(has=page.locator("input[name=name][value='dream pop']"))
    row.get_by_role("textbox", name="Genre name").fill("")
    page.once("dialog", lambda d: d.accept())
    row.get_by_role("button", name="Delete").click()
    expect(page.get_by_role("textbox", name="Genre name")).to_have_count(0)
    db.expire_all()
    assert db.query(Genre).filter_by(name="dream pop").count() == 0
