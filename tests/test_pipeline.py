from datetime import timedelta

from conftest import item

from whats_new.pipeline import (
    apply_total_cap,
    cap_per_source,
    drop_seen,
    filter_keywords,
    filter_since,
    merge_candidates,
    select_candidates,
)
from whats_new.text import normalize_url, to_iso


def test_filter_since_keeps_newer_and_undated(now):
    since = now - timedelta(hours=24)
    items = [
        item("https://a/new", published=to_iso(now - timedelta(hours=1))),
        item("https://a/old", published=to_iso(now - timedelta(hours=30))),
        item("https://a/exact", published=to_iso(since)),
        item("https://a/undated", published=None),
    ]
    kept = [i["url"] for i in filter_since(items, since)]
    assert kept == ["https://a/new", "https://a/undated"]


def test_drop_seen_uses_normalized_urls():
    seen = {normalize_url("https://a.com/post")}
    items = [item("https://a.com/post/?utm_source=x"), item("https://a.com/other")]
    assert [i["url"] for i in drop_seen(items, seen)] == ["https://a.com/other"]


def test_merge_keeps_leftovers_and_prefers_fresh():
    leftover = [
        item("https://a.com/1", title="old title"),
        item("https://a.com/2", title="only in leftover"),
    ]
    fresh = [item("https://a.com/1/#x", title="fresh title"), item("https://a.com/3")]
    merged = merge_candidates(leftover, fresh)
    by_url = {normalize_url(i["url"]): i["title"] for i in merged}
    assert by_url == {
        "https://a.com/1": "fresh title",
        "https://a.com/2": "only in leftover",
        "https://a.com/3": "t",
    }


def test_merge_dedupes_within_fresh():
    fresh = [item("https://a.com/1"), item("https://a.com/1?utm_medium=x")]
    assert len(merge_candidates([], fresh)) == 1


def test_cap_per_source_keeps_newest_or_highest_score(now):
    news = [item(f"https://n/{i}", source="N", published=to_iso(now - timedelta(hours=i))) for i in range(30)]
    community = [
        item(f"https://c/{i}", source="C", category="community", signals={"hn_points": i}) for i in range(30)
    ]
    capped = cap_per_source(news + community, cap=25)
    n = [i for i in capped if i["source"] == "N"]
    c = [i for i in capped if i["source"] == "C"]
    assert len(n) == 25 and n[0]["url"] == "https://n/0" and "https://n/29" not in {i["url"] for i in n}
    assert len(c) == 25 and c[0]["signals"]["hn_points"] == 29 and min(i["signals"]["hn_points"] for i in c) == 5


def test_total_cap_prefers_lab_then_community_by_score_then_news(now):
    lab = [item(f"https://l/{i}", source="L", category="lab") for i in range(3)]
    community = [
        item("https://c/low", source="C", category="community", signals={"stars_today": 10}),
        item("https://c/high", source="C", category="community", signals={"upvotes": 99}),
    ]
    news = [item(f"https://n/{i}", source="N", published=to_iso(now - timedelta(hours=i))) for i in range(3)]
    out = apply_total_cap(news + community + lab, cap=6)
    assert [i["url"] for i in out] == [
        "https://l/0",
        "https://l/1",
        "https://l/2",
        "https://c/high",
        "https://c/low",
        "https://n/0",
    ]


def test_filter_keywords_whole_word_and_prefix():
    items = [
        item("https://1", title="New LLM benchmark"),
        item("https://2", title="Airbnb pricing"),  # "AI" must not match "Airbnb"
        item("https://3", title="Agentic coding with Claude"),
        item("https://4", title="Gardening tips"),
    ]
    kept = filter_keywords(items, ["AI", "LLM*", "agentic"])
    assert [i["url"] for i in kept] == ["https://1", "https://3"]
    assert filter_keywords(items, []) == items


def test_select_candidates_end_to_end():
    fresh = [item("https://a/1"), item("https://a/seen"), item("https://lab/1", category="lab", source="L")]
    leftover = [item("https://a/left")]
    out = select_candidates(fresh, leftover, {normalize_url("https://a/seen")})
    assert [i["url"] for i in out] == ["https://lab/1", "https://a/1", "https://a/left"]
