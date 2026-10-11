"""Pure candidate logic: since-window, keyword filter, seen-dedupe, merge, caps.

A candidate is a plain dict shaped like the `candidates[]` entries in
docs/CONTRACT.md (id, source, category, title, url, published, summary, signals).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime
from functools import lru_cache

from .text import normalize_url, parse_iso

PER_SOURCE_CAP = 25
TOTAL_CAP = 250

CATEGORY_ORDER = ("lab", "community", "news")

# Signals that express "how hot is this" for community items, in lookup order.
SCORE_KEYS = ("hn_points", "stars_today", "upvotes", "trending_score", "likes")


def published_dt(item: dict) -> datetime | None:
    return parse_iso(item.get("published"))


def filter_since(items: Iterable[dict], since: datetime) -> list[dict]:
    """Keep items published strictly after `since`; undated items are always kept."""
    kept = []
    for item in items:
        dt = published_dt(item)
        if dt is None or dt > since:
            kept.append(item)
    return kept


@lru_cache(maxsize=64)
def _keyword_regex(keywords: tuple[str, ...]) -> re.Pattern[str]:
    parts = []
    for kw in keywords:
        if kw.endswith("*"):
            parts.append(r"\b" + re.escape(kw[:-1]))
        else:
            parts.append(r"\b" + re.escape(kw) + r"\b")
    return re.compile("|".join(parts), re.I)


def matches_keywords(text: str, keywords: Iterable[str]) -> bool:
    """Whole-word, case-insensitive match; a trailing `*` makes a keyword a prefix."""
    kws = tuple(k for k in keywords if k)
    if not kws:
        return True
    return bool(_keyword_regex(kws).search(text or ""))


def filter_keywords(items: Iterable[dict], keywords: Iterable[str] | None) -> list[dict]:
    kws = tuple(keywords or ())
    if not kws:
        return list(items)
    return [i for i in items if matches_keywords(f"{i.get('title', '')} {i.get('summary', '')}", kws)]


def dedupe(items: Iterable[dict]) -> list[dict]:
    """Drop later items whose normalized URL was already seen in this list."""
    seen: set[str] = set()
    out = []
    for item in items:
        key = normalize_url(item.get("url", ""))
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def drop_seen(items: Iterable[dict], seen_urls: set[str]) -> list[dict]:
    """Drop items whose normalized URL is in `seen_urls` (already normalized)."""
    return [i for i in items if normalize_url(i.get("url", "")) not in seen_urls]


def merge_candidates(leftover: Iterable[dict], fresh: Iterable[dict]) -> list[dict]:
    """Union of a failed run's leftover candidates and a fresh fetch, by normalized URL.

    Fresh data wins on conflicts (newer signals/summary); leftovers that the fresh
    fetch no longer returns are kept so nothing from a failed run is lost.
    """
    fresh = dedupe(fresh)
    fresh_keys = {normalize_url(i["url"]) for i in fresh}
    kept_leftover = [i for i in dedupe(leftover) if normalize_url(i.get("url", "")) not in fresh_keys]
    return fresh + kept_leftover


def score(item: dict) -> float:
    signals = item.get("signals") or {}
    for key in SCORE_KEYS:
        value = signals.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return 0.0


def _recency_key(item: dict) -> tuple[int, float]:
    dt = published_dt(item)
    # Dated items first (newest first), undated after them in original order.
    return (0, -dt.timestamp()) if dt else (1, 0.0)


def _rank_key(item: dict) -> tuple:
    if item.get("category") == "community":
        return (-score(item), *_recency_key(item))
    return _recency_key(item)


def cap_per_source(items: Iterable[dict], cap: int = PER_SOURCE_CAP) -> list[dict]:
    """Keep at most `cap` items per source: best score for community, newest otherwise."""
    by_source: dict[str, list[dict]] = {}
    for item in items:
        by_source.setdefault(item.get("source", ""), []).append(item)
    out = []
    for group in by_source.values():
        out.extend(sorted(group, key=_rank_key)[:cap])
    return out


def apply_total_cap(items: Iterable[dict], cap: int = TOTAL_CAP) -> list[dict]:
    """Order lab > community (by score) > news (newest first) and keep the first `cap`."""
    items = list(items)
    ordered = []
    for category in CATEGORY_ORDER:
        ordered.extend(sorted((i for i in items if i.get("category") == category), key=_rank_key))
    ordered.extend(sorted((i for i in items if i.get("category") not in CATEGORY_ORDER), key=_rank_key))
    return ordered[:cap]


def select_candidates(
    fresh: Iterable[dict],
    leftover: Iterable[dict],
    seen_urls: set[str],
    per_source_cap: int = PER_SOURCE_CAP,
    total_cap: int = TOTAL_CAP,
) -> list[dict]:
    """Merge window-filtered fresh items with leftovers, drop seen URLs, apply caps."""
    merged = merge_candidates(leftover, fresh)
    merged = drop_seen(merged, seen_urls)
    merged = cap_per_source(merged, per_source_cap)
    return apply_total_cap(merged, total_cap)
