from datetime import UTC, datetime

import pytest

NOW = datetime(2026, 10, 11, 10, 30, tzinfo=UTC)


def item(url, source="Src", category="news", published=None, title="t", signals=None):
    return {
        "id": "x",
        "source": source,
        "category": category,
        "title": title,
        "url": url,
        "published": published,
        "summary": "",
        "signals": signals or {},
    }


@pytest.fixture
def now():
    return NOW
