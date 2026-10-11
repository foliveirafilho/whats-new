from datetime import UTC, datetime

import pytest

from whats_new.text import normalize_url, parse_date_text, parse_iso, strip_html, to_iso, url_id


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("https://example.com/post/", "https://example.com/post"),
        ("https://example.com/post#comments", "https://example.com/post"),
        ("https://example.com/post?utm_source=rss&utm_medium=feed", "https://example.com/post"),
        ("https://example.com/post?id=3&utm_campaign=x", "https://example.com/post?id=3"),
        ("https://Example.COM/Post", "https://example.com/Post"),
        ("http://example.com/post", "https://example.com/post"),
        ("https://example.com:443/post", "https://example.com/post"),
        ("https://example.com/", "https://example.com"),
        ("https://example.com/a?b=2&a=1", "https://example.com/a?b=2&a=1"),
        ("  https://example.com/x//  ", "https://example.com/x"),
        ("", ""),
    ],
)
def test_normalize_url(raw, expected):
    assert normalize_url(raw) == expected


def test_url_id_is_12_chars_and_stable_across_variants():
    a = url_id("https://example.com/post/?utm_source=x#frag")
    assert a == url_id("https://example.com/post")
    assert len(a) == 12


def test_strip_html_removes_tags_entities_and_truncates():
    assert strip_html("<p>Hello&nbsp;<b>world</b> &amp; co</p><script>x()</script>") == "Hello world & co"
    assert strip_html("&lt;p&gt;double &amp;amp; escaped&lt;/p&gt;") == "double & escaped"
    long = strip_html("word " * 200)
    assert len(long) <= 400 and long.endswith("…")
    assert strip_html(None) == ""


def test_dates():
    assert parse_date_text("Oct 8, 2026") == datetime(2026, 10, 8, tzinfo=UTC)
    assert parse_date_text("April 08, 2026") == datetime(2026, 4, 8, tzinfo=UTC)
    assert parse_date_text("Published Sept 3, 2026 by X") == datetime(2026, 9, 3, tzinfo=UTC)
    assert parse_date_text("no date here") is None
    assert parse_iso("2026-08-27T07:01-07:00") == datetime(2026, 8, 27, 14, 1, tzinfo=UTC)
    assert to_iso(parse_iso("2026-10-10T18:00:00.000Z")) == "2026-10-10T18:00:00Z"
    assert parse_iso("garbage") is None
