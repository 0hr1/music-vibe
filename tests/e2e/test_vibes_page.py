"""The Vibes page: making, quick-adding, renaming and deleting vibes."""

from playwright.sync_api import expect

from app.models import Vibe


def _row(page, name):
    return page.locator(".vibe-list li").filter(has=page.locator(f"input[name=name][value='{name}']"))


def test_create_quick_add_rename_delete(logged_in, make_album, db, user):
    page = logged_in
    page.goto("/vibes")
    page.get_by_role("textbox", name="Vibe name").fill("rainy sunday")
    page.get_by_role("button", name="Create").click()
    expect(_row(page, "rainy sunday")).to_have_count(1)

    page.get_by_role("button", name="+ happy").click()  # a suggestion
    expect(_row(page, "happy")).to_have_count(1)
    expect(page.get_by_role("button", name="+ happy")).to_have_count(0)  # no longer suggested

    row = _row(page, "rainy sunday")
    row.get_by_role("textbox", name="Name").fill("rainy day")
    row.get_by_role("button", name="Save").click()
    expect(_row(page, "rainy day")).to_have_count(1)
    expect(_row(page, "rainy sunday")).to_have_count(0)

    page.once("dialog", lambda d: d.accept())
    _row(page, "happy").get_by_role("button", name="Delete").click()
    expect(_row(page, "happy")).to_have_count(0)
    names = {v.name for v in db.query(Vibe).filter_by(user_id=user.id)}
    assert "rainy day" in names and "happy" not in names and "rainy sunday" not in names


def test_delete_works_after_emptying_the_name(logged_in, make_vibe, db, user):
    make_vibe("winter")
    page = logged_in
    page.goto("/vibes")
    row = _row(page, "winter")
    row.get_by_role("textbox", name="Name").fill("")  # a natural first step when getting rid of it
    page.once("dialog", lambda d: d.accept())
    row.get_by_role("button", name="Delete").click()
    expect(_row(page, "winter")).to_have_count(0)
    assert "winter" not in {v.name for v in db.query(Vibe).filter_by(user_id=user.id)}


def test_deleting_a_vibe_keeps_its_albums(logged_in, make_album, make_vibe, db):
    album = make_album("Keeper", vibes=[make_vibe("winter")])
    page = logged_in
    page.goto("/vibes")
    expect(_row(page, "winter")).to_contain_text("1 album")
    page.once("dialog", lambda d: d.accept())
    _row(page, "winter").get_by_role("button", name="Delete").click()
    expect(_row(page, "winter")).to_have_count(0)
    page.goto("/")
    expect(page.locator(".card")).to_contain_text("Keeper")
    db.expire_all()
    assert db.get(type(album), album.id).vibes == []
