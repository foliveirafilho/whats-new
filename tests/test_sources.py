import json

import pytest

from whats_new.sources import (
    ParseError,
    Source,
    parse_feed,
    parse_github_trending,
    parse_hf_models,
    parse_hf_papers,
    parse_hn,
    parse_scrape,
)

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>Blog</title>
<item><title>Big &amp; new model</title><link>https://lab.example/news/big-model/?utm_source=rss</link>
<pubDate>Sat, 10 Oct 2026 18:00:00 GMT</pubDate>
<description>&lt;p&gt;We &lt;b&gt;release&lt;/b&gt; a model.&lt;/p&gt;</description></item>
<item><title>Undated post</title><link>/news/undated</link></item>
</channel></rss>"""

ATOM_PH = b"""<?xml version="1.0" encoding="UTF-8"?><feed xmlns="http://www.w3.org/2005/Atom"><title>PH</title>
<entry><id>1</id><published>2026-10-10T21:14:18-07:00</published>
<link rel="alternate" type="text/html" href="https://www.producthunt.com/products/x"/><title>X</title>
<content type="html">&lt;p&gt;An AI agent for your Mac&lt;/p&gt;&lt;p&gt;&lt;a href="#"&gt;Discussion&lt;/a&gt; | &lt;a href="#"&gt;Link&lt;/a&gt;&lt;/p&gt;</content>
</entry></feed>"""


def src(kind="rss", category="lab", url="https://lab.example/news", **filters):
    return Source(name="S", category=category, kind=kind, url=url, filters=filters)


def test_parse_feed_rss():
    items = parse_feed(RSS, src())
    assert items[0]["title"] == "Big & new model"
    assert items[0]["published"] == "2026-10-10T18:00:00Z"
    assert items[0]["summary"] == "We release a model."
    assert items[0]["category"] == "lab" and items[0]["source"] == "S" and len(items[0]["id"]) == 12
    assert items[1]["url"] == "https://lab.example/news/undated"
    assert items[1]["published"] is None


def test_parse_feed_atom_strips_product_hunt_footer():
    (it,) = parse_feed(ATOM_PH, src(category="community"))
    assert it["summary"] == "An AI agent for your Mac"
    assert it["published"] == "2026-10-11T04:14:18Z"


def test_parse_feed_rejects_html_error_page():
    with pytest.raises(ParseError):
        parse_feed(b"<html><body>Too many requests</body></html>", src())


def test_parse_hn_signals_and_self_posts():
    payload = {
        "hits": [
            {"objectID": "1", "title": "Claude does X", "url": "https://x.com/a", "points": 312,
             "num_comments": 120, "created_at": "2026-10-10T18:00:00Z"},
            {"objectID": "2", "title": "Ask HN: LLM tips?", "url": None, "points": 80, "num_comments": 5,
             "created_at": "2026-10-10T19:00:00Z", "story_text": "<p>How do you&#x27;all</p>"},
            {"objectID": "3", "title": "Low", "url": "https://x.com/low", "points": 3, "created_at": "2026-10-10T19:00:00Z"},
        ]
    }
    items = parse_hn(json.dumps(payload).encode(), src(kind="hn", category="community", min_points=50))
    assert [i["url"] for i in items] == ["https://x.com/a", "https://news.ycombinator.com/item?id=2"]
    assert items[0]["signals"] == {
        "hn_points": 312, "hn_comments": 120, "discussion_url": "https://news.ycombinator.com/item?id=1"
    }
    assert items[1]["summary"] == "How do you'all"


GH = b"""<html><body>
<article class="Box-row"><h2 class="h3"><a href="/acme/agent-kit">
  acme /
  agent-kit</a></h2><p class="col-9">An LLM agent toolkit</p>
<span itemprop="programmingLanguage">Python</span>
<a href="/acme/agent-kit/stargazers">12,345</a>
<span class="d-inline-block float-sm-right">1,024 stars today</span></article>
</body></html>"""


def test_parse_github_trending():
    (it,) = parse_github_trending(GH, src(kind="github_trending", category="community"))
    assert it["title"] == "acme/agent-kit"
    assert it["url"] == "https://github.com/acme/agent-kit"
    assert it["summary"] == "An LLM agent toolkit [Python]"
    assert it["signals"] == {"stars_today": 1024, "stars": 12345}
    assert it["published"] is None


def test_parse_hf_models_and_papers():
    models = [{"id": "org/m", "likes": 10, "downloads": 5, "trendingScore": 99,
               "pipeline_tag": "text-generation", "createdAt": "2020-01-01T00:00:00Z"}]
    (m,) = parse_hf_models(json.dumps(models).encode(), src(kind="hf_models", category="community"))
    assert m["url"] == "https://huggingface.co/org/m" and m["published"] is None
    assert m["signals"]["trending_score"] == 99
    papers = [{"paper": {"id": "2610.1", "title": "Paper", "summary": "Abs", "upvotes": 7}, "numComments": 2}]
    (p,) = parse_hf_papers(json.dumps(papers).encode(), src(kind="hf_papers", category="community"))
    assert p["url"] == "https://huggingface.co/papers/2610.1" and p["signals"]["upvotes"] == 7
    with pytest.raises(ParseError):
        parse_hf_models(b'{"error": "x"}', src(kind="hf_models"))


SCRAPE = b"""<html><body>
<header><a href="/news/menu-link">Menu link title that is long</a></header>
<section>
  <a href="/news/hero"><div>FEATURED</div><img alt="x"></a>
  <div><a href="/news/hero">Hero article title</a><time datetime="2026-10-10T12:00:00Z">Oct 10</time></div>
</section>
<ul>
  <li><a href="/news/two"><div><time>Oct 9, 2026</time><span>Announcements</span></div>
      <span class="Item_title">Second article</span></a></li>
  <li><div class="card"><h4>Research</h4><h4>Third article with a long heading</h4>
      <p>October 1, 2026</p><p>A summary paragraph that is long enough to be kept as such.</p>
      <a href="/news/three/">Learn More</a></div></li>
</ul>
<footer><a href="/news/evergreen">Evergreen policy page</a></footer>
</body></html>"""


def test_parse_scrape_heuristics():
    items = parse_scrape(SCRAPE, src(kind="scrape", link_pattern=r"^https://lab\.example/news/[^/]+/?$"))
    by_url = {i["url"]: i for i in items}
    assert set(by_url) == {
        "https://lab.example/news/hero",
        "https://lab.example/news/two",
        "https://lab.example/news/three/",
    }
    assert by_url["https://lab.example/news/hero"]["title"] == "Hero article title"
    assert by_url["https://lab.example/news/hero"]["published"] == "2026-10-10T12:00:00Z"
    assert by_url["https://lab.example/news/two"]["title"] == "Second article"
    assert by_url["https://lab.example/news/two"]["published"] == "2026-10-09T00:00:00Z"
    three = by_url["https://lab.example/news/three/"]
    assert three["title"] == "Third article with a long heading"
    assert three["published"] == "2026-10-01T00:00:00Z"
    assert three["summary"].startswith("A summary paragraph")


def test_parse_scrape_require_date_and_layout_change():
    page = b'<a href="/news/a">Undated article title here</a><a href="/news/b"><time>Oct 9, 2026</time>B title</a>'
    s = src(kind="scrape", link_pattern=r"/news/", require_date=True)
    assert [i["url"] for i in parse_scrape(page, s)] == ["https://lab.example/news/b"]
    with pytest.raises(ParseError):
        parse_scrape(b"<html><a href='/about'>About</a></html>", s)
