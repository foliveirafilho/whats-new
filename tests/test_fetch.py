import json
from datetime import timedelta
from email.utils import format_datetime

import httpx
import pytest

from whats_new.cli import main
from whats_new.fetch import run_fetch
from whats_new.state import Paths, SeenStore, write_candidates

SOURCES = """
[[source]]
name = "Good Lab"
category = "lab"
kind = "rss"
url = "https://good.example/rss"

[[source]]
name = "Dead News"
category = "news"
kind = "rss"
url = "https://dead.example/rss"

[[source]]
name = "Unreachable"
category = "news"
kind = "rss"
url = "https://down.example/rss"

[[source]]
name = "Garbage"
category = "news"
kind = "rss"
url = "https://garbage.example/rss"

[[source]]
name = "HN"
category = "community"
kind = "hn"
url = "https://hn.example/api"
filters.min_points = 50
filters.keywords = ["AI"]

[[source]]
name = "Off"
category = "news"
kind = "rss"
url = "https://off.example/rss"
enabled = false
"""


def rss(*entries):
    body = "".join(
        f"<item><title>{t}</title><link>{u}</link>"
        + (f"<pubDate>{format_datetime(d)}</pubDate>" if d else "")
        + "</item>"
        for t, u, d in entries
    )
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>{body}</channel></rss>'.encode()


@pytest.fixture
def root(tmp_path):
    (tmp_path / "sources.toml").write_text(SOURCES)
    return tmp_path


def make_transport(now, seen_requests):
    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        host = request.url.host
        if host == "good.example":
            return httpx.Response(
                200,
                content=rss(
                    ("Fresh post", "https://good.example/p/fresh", now - timedelta(hours=2)),
                    ("Old post", "https://good.example/p/old", now - timedelta(days=5)),
                    # Before `since` (2026-10-10T10:30Z) but inside the 24h lookback: a late-indexed post.
                    ("Late post", "https://good.example/p/late", now - timedelta(hours=26)),
                    ("Seen post", "https://good.example/p/seen/", now - timedelta(hours=3)),
                    ("Undated post", "https://good.example/p/undated", None),
                ),
            )
        if host == "dead.example":
            return httpx.Response(503, text="maintenance")
        if host == "down.example":
            raise httpx.ConnectError("name resolution failed", request=request)
        if host == "garbage.example":
            return httpx.Response(200, text="<html>not a feed</html>")
        if host == "hn.example":
            hits = [
                {"objectID": "1", "title": "AI thing", "url": "https://x.example/ai", "points": 100,
                 "num_comments": 9, "created_at": (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")},
                {"objectID": "2", "title": "Rust thing", "url": "https://x.example/rust", "points": 400,
                 "num_comments": 9, "created_at": (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")},
            ]
            return httpx.Response(200, json={"hits": hits})
        raise AssertionError(f"unexpected request {request.url}")

    return httpx.MockTransport(handler)


def test_run_fetch_full_flow(root, now):
    paths = Paths(root)
    paths.state_dir.mkdir()
    paths.last_success.write_text("2026-10-10T10:30:00Z\n")
    with SeenStore(paths.seen_db) as seen:
        seen.add(["https://good.example/p/seen"], now)
    write_candidates(
        paths,
        {
            "generated_at": "2026-10-10T12:00:00Z",
            "since": "2026-10-10T10:30:00Z",
            "sources_health": [],
            "candidates": [
                {"id": "a", "source": "Dead News", "category": "news", "title": "Leftover", "url": "https://dead.example/left",
                 "published": None, "summary": "", "signals": {}},
            ],
        },
    )
    requests = []
    payload = run_fetch(paths, now, transport=make_transport(now, requests))

    assert payload["since"] == "2026-10-10T10:30:00Z"
    assert payload["generated_at"] == "2026-10-11T10:30:00Z"
    urls = [c["url"] for c in payload["candidates"]]
    assert urls == [
        "https://good.example/p/fresh",
        "https://good.example/p/late",
        "https://good.example/p/undated",
        "https://x.example/ai",
        "https://dead.example/left",
    ]
    health = {h["source"]: h for h in payload["sources_health"]}
    assert set(health) == {"Good Lab", "Dead News", "Unreachable", "Garbage", "HN"}  # disabled source skipped
    assert health["Good Lab"] == {"source": "Good Lab", "category": "lab", "status": "ok", "items": 3, "error": None}
    assert health["Dead News"]["status"] == "error" and "503" in health["Dead News"]["error"]
    assert health["Dead News"]["items"] == 1  # the leftover still counts as its candidate
    assert health["Unreachable"]["status"] == "error" and "ConnectError" in health["Unreachable"]["error"]
    assert health["Garbage"]["status"] == "error" and "no entries" in health["Garbage"]["error"]
    assert health["HN"]["items"] == 1

    hn_req = next(r for r in requests if r.url.host == "hn.example")
    # since - 24h default lookback
    assert "created_at_i>1791541800" in hn_req.url.params["numericFilters"]
    assert "points>=50" in hn_req.url.params["numericFilters"]
    assert all(r.headers["user-agent"].startswith("whats-new/") for r in requests)

    on_disk = json.loads(paths.candidates.read_text())
    assert on_disk == payload


def test_fetch_and_commit_via_cli(root, now, monkeypatch, capsys):
    import whats_new.cli as cli
    import whats_new.fetch as fetch_mod

    requests = []
    transport = make_transport(now, requests)
    real_run = fetch_mod.run_fetch
    monkeypatch.setattr(fetch_mod, "run_fetch", lambda paths, _now: real_run(paths, now, transport=transport))
    monkeypatch.setattr(cli, "_now", lambda: now)

    assert main(["--root", str(root), "fetch"]) == 0
    assert main(["--root", str(root), "status"]) == 0
    out = capsys.readouterr().out
    assert "candidates:" in out and "Dead News" in out

    assert main(["--root", str(root), "commit"]) == 0
    paths = Paths(root)
    assert not paths.candidates.exists()
    assert paths.last_success.read_text().strip() == "2026-10-11T10:30:00Z"

    # Second run: everything already seen, window starts at the committed timestamp.
    assert main(["--root", str(root), "fetch"]) == 0
    data = json.loads(paths.candidates.read_text())
    assert data["since"] == "2026-10-11T10:30:00Z"
    assert data["candidates"] == []
    assert main(["--root", str(root), "commit"]) == 0
    assert main(["--root", str(root), "commit"]) == 1  # nothing pending
