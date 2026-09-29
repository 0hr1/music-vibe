import os
import re

from app import main


def test_pages_link_static_files_by_content_hash(client):
    html = client.get("/login").text
    urls = re.findall(r'(?:href|src)="(/static/[^"]+)"', html)
    assert {u.split("?")[0] for u in urls} == {"/static/style.css", "/static/htmx.min.js", "/static/app.js"}
    assert all(re.search(r"\?v=[0-9a-f]{12}$", u) for u in urls)


def test_hashed_urls_are_cached_long_and_plain_ones_are_not(client):
    url = main.static_url("app.js")
    assert "immutable" in client.get(url).headers["cache-control"]
    assert "cache-control" not in client.get("/static/app.js").headers
    assert client.get("/static/nope.js?v=123").status_code == 404


def test_url_changes_when_the_file_does(tmp_path, monkeypatch):
    (tmp_path / "static").mkdir()
    f = tmp_path / "static" / "app.js"
    f.write_text("one")
    monkeypatch.setattr(main, "HERE", tmp_path)
    before = main.static_url("app.js")
    f.write_text("two")
    os.utime(f, ns=(1, f.stat().st_mtime_ns + 1))  # a new mtime even on coarse filesystem clocks
    assert main.static_url("app.js") != before
