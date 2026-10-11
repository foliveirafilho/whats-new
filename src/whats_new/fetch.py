"""Network side of `whats-new fetch`: pull every source concurrently, never crash."""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timedelta
from typing import Any

import httpx

from . import __version__
from .pipeline import filter_keywords, filter_since, select_candidates
from .sources import PARSERS, Source, hn_params, load_sources
from .state import Paths, SeenStore, load_candidates, read_since, write_candidates
from .text import to_iso

TIMEOUT = 20.0
CONCURRENCY = 8
# How far before `since` each source is re-checked. Feeds often stamp posts with a
# date-level time (OpenAI: 07:00Z) or index them late, and scrapers only see days;
# without slack a post going live just after a run would never be picked up.
# The seen table turns the overlap into a no-op for anything already reported.
DEFAULT_LOOKBACK_HOURS = 24
SCRAPE_LOOKBACK_HOURS = 48

BOT_UA = f"whats-new/{__version__} (personal daily AI-news digest; contact: local user)"
# A few sites (VentureBeat) answer 429 to anything that doesn't look like a browser.
BROWSER_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
BASE_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _error_text(exc: BaseException) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code} from {exc.request.url}"
    if isinstance(exc, httpx.TimeoutException):
        return f"timeout after {TIMEOUT:.0f}s ({type(exc).__name__})"
    msg = str(exc) or type(exc).__name__
    if len(msg) > 300:
        msg = msg[:297] + "..."
    return f"{type(exc).__name__}: {msg}" if not msg.startswith(type(exc).__name__) else msg


async def fetch_source(client: httpx.AsyncClient, source: Source, since: datetime) -> tuple[list[dict], str | None]:
    """Items for one source inside its since-window, or ([], error message). Never raises."""
    try:
        default = SCRAPE_LOOKBACK_HOURS if source.kind == "scrape" else DEFAULT_LOOKBACK_HOURS
        window = since - timedelta(hours=int(source.filters.get("lookback_hours", default)))
        headers = {"User-Agent": BROWSER_UA if source.browser_ua else BOT_UA}
        params = hn_params(source, window) if source.kind == "hn" else None
        resp = await client.get(source.url, params=params, headers=headers)
        resp.raise_for_status()
        items = PARSERS[source.kind](resp.content, source)
        items = filter_keywords(items, source.keywords)
        return filter_since(items, window), None
    except Exception as exc:  # a dead/changed source must never fail the run
        return [], _error_text(exc)


async def fetch_all(
    sources: list[Source], since: datetime, transport: httpx.AsyncBaseTransport | None = None
) -> list[tuple[Source, list[dict], str | None]]:
    sem = asyncio.Semaphore(CONCURRENCY)

    async with httpx.AsyncClient(
        timeout=TIMEOUT, follow_redirects=True, headers=BASE_HEADERS, transport=transport
    ) as client:

        async def one(src: Source):
            async with sem:
                items, err = await fetch_source(client, src, since)
                return src, items, err

        return await asyncio.gather(*(one(s) for s in sources))


def run_fetch(paths: Paths, now: datetime, transport: httpx.AsyncBaseTransport | None = None) -> dict[str, Any]:
    """Build and atomically write state/candidates.json. Returns the payload."""
    since = read_since(paths, now)
    sources = [s for s in load_sources(paths.sources) if s.enabled]
    results = asyncio.run(fetch_all(sources, since, transport))

    fresh = [item for _, items, _ in results for item in items]
    previous = load_candidates(paths)
    leftover = previous["candidates"] if previous else []

    with SeenStore(paths.seen_db) as seen:
        seen_urls = seen.seen_among(i["url"] for i in [*fresh, *leftover])
    candidates = select_candidates(fresh, leftover, seen_urls)

    per_source = Counter(c["source"] for c in candidates)
    health = [
        {
            "source": src.name,
            "category": src.category,
            "status": "error" if err else "ok",
            "items": per_source.get(src.name, 0),
            "error": err,
        }
        for src, _, err in results
    ]
    payload = {
        "generated_at": to_iso(now),
        "since": to_iso(since),
        "sources_health": health,
        "candidates": candidates,
    }
    write_candidates(paths, payload)
    return payload
