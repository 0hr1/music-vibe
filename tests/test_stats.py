def test_stats_empty(client):
    r = client.get("/stats")
    assert r.status_code == 200 and "Nothing to count yet" in r.text


def test_stats_counts(client, make_album, make_vibe):
    winter, happy = make_vibe("winter"), make_vibe("happy")
    make_album("A", "Radiohead", 1997, vibes=[winter], genres=["alternative"])
    make_album("B", "radiohead", 2000, vibes=[winter, happy], genres=["alternative", "electronic"])
    make_album("C", "Fishmans", 1996, genres=["dub"])
    make_album("D", "", None)
    r = client.get("/stats")
    assert r.status_code == 200
    t = r.text
    assert '<span class="tile-value">4</span><span class="tile-label">albums' in t
    assert '<span class="tile-value">2</span><span class="tile-label">artists' in t  # case-insensitive
    assert '<span class="tile-value">3</span><span class="tile-label">genres' in t
    assert "1996–2000" in t and "50%" in t and "2 albums without any vibe" in t
    assert 'aria-label="1990s: 2 albums"' in t and 'aria-label="2000s: 1 album"' in t
    assert 'aria-label="winter: 2 albums"' in t and 'aria-label="happy: 1 album"' in t
    assert 'aria-label="alternative: 2 albums"' in t and 'href="/?genre=alternative"' in t
    assert 'aria-label="Radiohead: 2 albums"' in t
