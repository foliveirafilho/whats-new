"""Pure helpers: URL normalization, ids, HTML stripping, date handling."""

from __future__ import annotations

import hashlib
import html
import re
from datetime import UTC, datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

SUMMARY_MAX = 400

# Tracking params dropped besides every utm_* one.
_TRACKING_PARAMS = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref_src"}


def normalize_url(url: str) -> str:
    """Canonical form used for dedupe.

    Lowercases scheme/host and treats http as https. Strips utm_* (and a few
    common tracking) params, the fragment, default ports and trailing slashes on
    the path. Other query params are kept in their original order.
    """
    url = (url or "").strip()
    if not url:
        return ""
    parts = urlsplit(url)
    scheme = (parts.scheme or "https").lower()
    if scheme == "http":
        scheme = "https"
    netloc = parts.netloc.lower()
    if netloc.endswith(":443") or netloc.endswith(":80"):
        netloc = netloc.rsplit(":", 1)[0]
    path = parts.path or ""
    while len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    if path == "/":
        path = ""
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in _TRACKING_PARAMS
    ]
    return urlunsplit((scheme, netloc, path, urlencode(query), ""))


def url_id(url: str) -> str:
    """12-char sha1 of the normalized URL."""
    return hashlib.sha1(normalize_url(url).encode("utf-8")).hexdigest()[:12]


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_DROP_BLOCKS_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.I | re.S)


def strip_html(text: str | None, limit: int = SUMMARY_MAX) -> str:
    """Plain text, entities decoded, whitespace collapsed, truncated to `limit` chars."""
    if not text:
        return ""
    text = _DROP_BLOCKS_RE.sub(" ", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    # Feeds sometimes double-escape (`&amp;#8217;`), and a second pass can reveal tags.
    if "&" in text or "<" in text:
        text = _TAG_RE.sub(" ", html.unescape(text))
    text = _WS_RE.sub(" ", text).strip()
    if len(text) > limit:
        cut = text[: limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:.-")
        text = cut + "…"
    return text


def to_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: str | None) -> datetime | None:
    """Parse ISO-8601 (with `Z` or offset); naive values are treated as UTC."""
    if not value:
        return None
    value = value.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


_DATE_FORMATS = (
    "%b %d, %Y",
    "%B %d, %Y",
    "%d %b %Y",
    "%d %B %Y",
    "%b %d %Y",
    "%B %d %Y",
    "%Y-%m-%d",
)

DATE_TEXT_RE = re.compile(
    r"\b(?:"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}"
    r"|\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{4}"
    r"|\d{4}-\d{2}-\d{2}"
    r")\b"
)


def parse_date_text(text: str | None) -> datetime | None:
    """Parse human dates like 'Oct 8, 2026' / 'April 08, 2026' / '2026-10-08' (UTC midnight)."""
    if not text:
        return None
    m = DATE_TEXT_RE.search(text)
    if not m:
        return parse_iso(text)
    raw = _WS_RE.sub(" ", m.group(0).replace(".", "")).replace("Sept ", "Sep ")
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None
