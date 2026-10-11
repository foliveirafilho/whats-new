"""Source config loading and per-kind parsers (pure: bytes/JSON in, candidate dicts out)."""

from __future__ import annotations

import calendar
import json
import re
import tomllib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import feedparser
from bs4 import BeautifulSoup, Tag

from .text import DATE_TEXT_RE, parse_date_text, parse_iso, strip_html, to_iso, url_id

KINDS = {"rss", "hn", "github_trending", "hf_models", "hf_papers", "scrape"}
CATEGORIES = {"lab", "news", "community"}


@dataclass(frozen=True)
class Source:
    name: str
    category: str
    kind: str
    url: str
    enabled: bool = True
    filters: dict[str, Any] = field(default_factory=dict)
    browser_ua: bool = False

    @property
    def keywords(self) -> list[str]:
        return list(self.filters.get("keywords") or [])


def load_sources(path: Path) -> list[Source]:
    with open(path, "rb") as fh:
        data = tomllib.load(fh)
    sources = []
    for raw in data.get("source", []):
        kind = raw["kind"]
        category = raw["category"]
        if kind not in KINDS:
            raise ValueError(f"source {raw.get('name')!r}: unknown kind {kind!r}")
        if category not in CATEGORIES:
            raise ValueError(f"source {raw.get('name')!r}: unknown category {category!r}")
        sources.append(
            Source(
                name=raw["name"],
                category=category,
                kind=kind,
                url=raw["url"],
                enabled=raw.get("enabled", True),
                filters=dict(raw.get("filters", {})),
                browser_ua=raw.get("browser_ua", False),
            )
        )
    return sources


def make_item(
    source: Source,
    title: str,
    url: str,
    published: datetime | None,
    summary: str = "",
    signals: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": url_id(url),
        "source": source.name,
        "category": source.category,
        "title": strip_html(title, limit=300),
        "url": url,
        "published": to_iso(published),
        "summary": strip_html(summary),
        "signals": {k: v for k, v in (signals or {}).items() if v is not None},
    }


class ParseError(Exception):
    """The payload could not be understood (layout/API change)."""


# --------------------------------------------------------------------------- RSS / Atom

_PH_FOOTER_RE = re.compile(r"\s*Discussion\s*\|\s*Link\s*$")


def _struct_to_dt(st) -> datetime | None:
    if not st:
        return None
    try:
        return datetime.fromtimestamp(calendar.timegm(st), tz=UTC)
    except (OverflowError, ValueError, TypeError):
        return None


def parse_feed(content: bytes, source: Source) -> list[dict]:
    feed = feedparser.parse(content)
    if not feed.entries:
        reason = getattr(feed, "bozo_exception", None)
        raise ParseError(f"feed has no entries{f' ({reason})' if reason else ''}")
    items = []
    for entry in feed.entries:
        link = entry.get("link") or ""
        title = entry.get("title") or ""
        if not link or not title:
            continue
        link = urljoin(source.url, link)
        published = _struct_to_dt(entry.get("published_parsed") or entry.get("updated_parsed"))
        summary = entry.get("summary") or ""
        if not summary and entry.get("content"):
            summary = entry["content"][0].get("value", "")
        summary = _PH_FOOTER_RE.sub("", strip_html(summary))
        items.append(make_item(source, title, link, published, summary))
    return items


# --------------------------------------------------------------------------- Hacker News (Algolia)

def hn_params(source: Source, start: datetime) -> dict[str, str]:
    """Algolia query: stories created after `start` with at least `min_points`."""
    min_points = int(source.filters.get("min_points", 50))
    return {
        "tags": "story",
        "numericFilters": f"created_at_i>{int(start.timestamp())},points>={min_points}",
        "hitsPerPage": "1000",
    }


def parse_hn(content: bytes, source: Source) -> list[dict]:
    try:
        data = json.loads(content)
        hits = data["hits"]
    except (ValueError, KeyError, TypeError) as exc:
        raise ParseError(f"unexpected HN payload: {exc}") from exc
    min_points = int(source.filters.get("min_points", 50))
    items = []
    for hit in hits:
        title = hit.get("title") or ""
        object_id = hit.get("objectID")
        points = hit.get("points") or 0
        if not title or not object_id or points < min_points:
            continue
        discussion = f"https://news.ycombinator.com/item?id={object_id}"
        url = hit.get("url") or discussion
        published = parse_iso(hit.get("created_at"))
        if published is None and hit.get("created_at_i"):
            published = datetime.fromtimestamp(hit["created_at_i"], tz=UTC)
        summary = hit.get("story_text") or ""
        items.append(
            make_item(
                source,
                title,
                url,
                published,
                summary,
                {"hn_points": points, "hn_comments": hit.get("num_comments") or 0, "discussion_url": discussion},
            )
        )
    return items


# --------------------------------------------------------------------------- GitHub Trending

_INT_RE = re.compile(r"[\d,]+")


def _to_int(text: str | None) -> int | None:
    if not text:
        return None
    m = _INT_RE.search(text)
    return int(m.group(0).replace(",", "")) if m else None


def parse_github_trending(content: bytes, source: Source) -> list[dict]:
    soup = BeautifulSoup(content, "html.parser")
    rows = soup.select("article.Box-row")
    if not rows:
        raise ParseError("no repositories found (page layout changed?)")
    items = []
    for row in rows:
        link = row.select_one("h2 a[href]")
        if not link:
            continue
        path = link["href"].strip()
        repo = path.strip("/")
        url = urljoin("https://github.com/", path)
        desc_el = row.select_one("p")
        desc = desc_el.get_text(" ", strip=True) if desc_el else ""
        lang_el = row.select_one('[itemprop="programmingLanguage"]')
        lang = lang_el.get_text(strip=True) if lang_el else ""
        stars_el = row.select_one('a[href$="/stargazers"]')
        stars_today = None
        for span in row.select("span"):
            text = span.get_text(" ", strip=True)
            if "stars today" in text or "star today" in text:
                stars_today = _to_int(text)
                break
        summary = desc
        if lang:
            summary = f"{desc} [{lang}]" if desc else f"[{lang}]"
        items.append(
            make_item(
                source,
                repo.replace(" ", ""),
                url,
                None,
                summary,
                {"stars_today": stars_today, "stars": _to_int(stars_el.get_text() if stars_el else None)},
            )
        )
    return items


# --------------------------------------------------------------------------- Hugging Face

def parse_hf_models(content: bytes, source: Source) -> list[dict]:
    try:
        models = json.loads(content)
        assert isinstance(models, list)
    except (ValueError, AssertionError) as exc:
        raise ParseError(f"unexpected HF models payload: {exc}") from exc
    items = []
    for m in models:
        model_id = m.get("id") or m.get("modelId")
        if not model_id:
            continue
        bits = [b for b in (m.get("pipeline_tag"), m.get("library_name")) if b]
        stats = f"{m.get('likes', 0)} likes, {m.get('downloads', 0)} downloads"
        summary = " · ".join([*bits, stats])
        # A trending list, not a feed: undated so the since-window never hides it;
        # repeats are prevented by the seen table.
        items.append(
            make_item(
                source,
                model_id,
                f"https://huggingface.co/{model_id}",
                None,
                summary,
                {"trending_score": m.get("trendingScore"), "likes": m.get("likes"), "downloads": m.get("downloads")},
            )
        )
    return items


def parse_hf_papers(content: bytes, source: Source) -> list[dict]:
    try:
        rows = json.loads(content)
        assert isinstance(rows, list)
    except (ValueError, AssertionError) as exc:
        raise ParseError(f"unexpected HF papers payload: {exc}") from exc
    items = []
    for row in rows:
        paper = row.get("paper") or {}
        paper_id = paper.get("id")
        title = paper.get("title") or row.get("title")
        if not paper_id or not title:
            continue
        summary = paper.get("ai_summary") or paper.get("summary") or row.get("summary") or ""
        # Daily-papers is a curated list that lags arXiv by days: undated on purpose
        # (seen table prevents repeats), ranked by upvotes.
        items.append(
            make_item(
                source,
                title,
                f"https://huggingface.co/papers/{paper_id}",
                None,
                summary,
                {
                    "upvotes": paper.get("upvotes"),
                    "comments": row.get("numComments"),
                    "github_repo": paper.get("githubRepo"),
                },
            )
        )
    return items


# --------------------------------------------------------------------------- Generic HTML scraper

_GENERIC_LINK_TEXT = {"learn more", "read more", "read the post", "read", "more", "continue reading", "read story"}
_CHROME_TAGS = {"nav", "footer", "header"}
_CHROME_CLASS_RE = re.compile(r"footer|navigation|navbar|site-?nav|menu", re.I)
_HEADINGS = ["h1", "h2", "h3", "h4", "h5", "h6"]


def _in_page_chrome(anchor: Tag) -> bool:
    """True for links in nav/footer/header (site menus link evergreen posts)."""
    for parent in anchor.parents:
        if parent.name in _CHROME_TAGS:
            return True
        classes = " ".join(parent.get("class") or [])
        if classes and _CHROME_CLASS_RE.search(classes):
            return True
    return False


def _container_for(anchor: Tag, url: str, pattern: re.Pattern[str], base: str) -> Tag:
    """Largest ancestor of `anchor` whose matching links all point to `url` (the article card)."""
    node = anchor
    while node.parent is not None and node.parent.name not in ("body", "html", "[document]"):
        parent = node.parent
        urls = {
            urljoin(base, a["href"]).split("#")[0]
            for a in parent.find_all("a", href=True)
            if pattern.search(urljoin(base, a["href"]))
        }
        if len(urls) > 1:
            break
        node = parent
    return node


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _longest_text(elements) -> str:
    texts = [_clean(e.get_text(" ")) for e in elements]
    return max((t for t in texts if t and t.lower() not in _GENERIC_LINK_TEXT), key=len, default="")


def _title_in(scope: Tag) -> str:
    return _longest_text(scope.find_all(_HEADINGS)) or _longest_text(scope.find_all(class_=re.compile("title", re.I)))


def _title_for(anchor: Tag, card: Tag) -> str:
    """Heading/title-ish element inside the link, then the link text, then the card's longest heading."""
    title = _title_in(anchor)
    if title:
        return title
    link_text = _clean(anchor.get_text(" "))
    if link_text.lower() in _GENERIC_LINK_TEXT:
        link_text = ""
    if len(link_text) >= 15:
        return link_text
    return _title_in(card) or link_text


def _date_for(anchor: Tag, card: Tag) -> datetime | None:
    for scope in (anchor, card):
        time_el = scope.find("time")
        if time_el:
            dt = parse_iso(time_el.get("datetime")) or parse_date_text(time_el.get_text(" "))
            if dt:
                return dt
    m = DATE_TEXT_RE.search(card.get_text(" "))
    return parse_date_text(m.group(0)) if m else None


def _summary_for(anchor: Tag, card: Tag, title: str) -> str:
    for scope in (anchor, card):
        best = ""
        for p in scope.find_all("p"):
            text = _clean(p.get_text(" "))
            if len(text) >= 40 and text != title and len(text) > len(best):
                best = text
        if best:
            return best
    return ""


def parse_scrape(content: bytes, source: Source) -> list[dict]:
    """Scrape a blog/news index: links matching `filters.link_pattern` are articles.

    Each link's "card" is its largest ancestor holding no other article link. Title:
    a heading/"title" element in the link, else the link text, else the card's longest
    heading. Date: a <time> element or the first date-looking text in the card.
    Summary: the longest <p> in the link, else in the card. Links in nav/footer/header
    are ignored; `filters.require_date = true` also drops undated links.
    """
    pattern = re.compile(source.filters["link_pattern"])
    require_date = bool(source.filters.get("require_date", False))
    soup = BeautifulSoup(content, "html.parser")
    index = source.url.split("#")[0].rstrip("/")
    found: dict[str, dict] = {}
    matched_links = 0
    for anchor in soup.find_all("a", href=True):
        url = urljoin(source.url, anchor["href"]).split("#")[0]
        if not pattern.search(url) or url.rstrip("/") == index or _in_page_chrome(anchor):
            continue
        matched_links += 1
        card = _container_for(anchor, url, pattern, source.url)
        title = _title_for(anchor, card)
        published = _date_for(anchor, card)
        summary = _summary_for(anchor, card, title)
        prev = found.get(url)
        if prev is not None:
            # Same article linked twice (hero + list): keep the most descriptive title
            # (hero cards often only offer a "FEATURED" label) and fill gaps.
            if len(title) > len(prev["title"]):
                prev["title"] = strip_html(title, limit=300)
            if prev["published"] is None and published is not None:
                prev["published"] = to_iso(published)
            if not prev["summary"] and summary:
                prev["summary"] = strip_html(summary)
            continue
        found[url] = make_item(source, title, url, published, summary)
    if not matched_links:
        raise ParseError("no article links matched (page layout changed?)")
    items = [i for i in found.values() if i["title"]]
    if require_date:
        items = [i for i in items if i["published"]]
    return items


PARSERS = {
    "rss": parse_feed,
    "hn": parse_hn,
    "github_trending": parse_github_trending,
    "hf_models": parse_hf_models,
    "hf_papers": parse_hf_papers,
    "scrape": parse_scrape,
}
