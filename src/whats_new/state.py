"""On-disk state under state/: seen.sqlite, last_success, candidates.json."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .text import normalize_url, parse_iso, to_iso

DEFAULT_LOOKBACK = timedelta(hours=48)


def project_root() -> Path:
    """WHATS_NEW_ROOT, else the checkout this package lives in, else the cwd."""
    env = os.environ.get("WHATS_NEW_ROOT")
    if env:
        return Path(env).resolve()
    here = Path(__file__).resolve().parents[2]
    if (here / "sources.toml").exists():
        return here
    return Path.cwd()


@dataclass(frozen=True)
class Paths:
    root: Path

    @property
    def state_dir(self) -> Path:
        return self.root / "state"

    @property
    def sources(self) -> Path:
        return self.root / "sources.toml"

    @property
    def seen_db(self) -> Path:
        return self.state_dir / "seen.sqlite"

    @property
    def last_success(self) -> Path:
        return self.state_dir / "last_success"

    @property
    def candidates(self) -> Path:
        return self.state_dir / "candidates.json"


def atomic_write_text(path: Path, text: str) -> None:
    """Write via a temp file in the same dir + fsync + rename, so readers never see a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def read_since(paths: Paths, now: datetime) -> datetime:
    """last_success (ISO-8601 UTC) or now-48h when missing/unreadable."""
    try:
        dt = parse_iso(paths.last_success.read_text(encoding="utf-8").strip())
    except FileNotFoundError:
        dt = None
    return dt if dt is not None else now - DEFAULT_LOOKBACK


def load_candidates(paths: Paths) -> dict[str, Any] | None:
    """The pending candidates.json, or None. A corrupt file is moved aside, never fatal."""
    try:
        raw = paths.candidates.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    try:
        data = json.loads(raw)
        if not isinstance(data, dict) or not isinstance(data.get("candidates"), list):
            raise ValueError("missing candidates list")
        return data
    except ValueError:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        paths.candidates.rename(paths.candidates.with_name(f"candidates.corrupt-{stamp}.json"))
        return None


def write_candidates(paths: Paths, payload: dict[str, Any]) -> None:
    atomic_write_text(paths.candidates, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


class SeenStore:
    """SQLite table seen(url_norm PRIMARY KEY, first_seen)."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.execute("CREATE TABLE IF NOT EXISTS seen (url_norm TEXT PRIMARY KEY, first_seen TEXT NOT NULL)")
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> SeenStore:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def seen_among(self, urls: Iterable[str]) -> set[str]:
        """Normalized forms of `urls` that are already in the table."""
        keys = list({normalize_url(u) for u in urls if u})
        found: set[str] = set()
        for i in range(0, len(keys), 500):
            chunk = keys[i : i + 500]
            marks = ",".join("?" * len(chunk))
            found.update(r[0] for r in self.conn.execute(f"SELECT url_norm FROM seen WHERE url_norm IN ({marks})", chunk))
        return found

    def add(self, urls: Iterable[str], when: datetime) -> int:
        stamp = to_iso(when)
        rows = [(normalize_url(u), stamp) for u in urls if u]
        before = self.conn.total_changes
        with self.conn:
            self.conn.executemany("INSERT OR IGNORE INTO seen (url_norm, first_seen) VALUES (?, ?)", rows)
        return self.conn.total_changes - before

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM seen").fetchone()[0]


class NothingToCommit(Exception):
    pass


def commit(paths: Paths, now: datetime) -> tuple[int, str]:
    """Mark every candidate as seen, advance last_success to generated_at, delete candidates.json.

    Order matters for crash-safety: seen first (idempotent), then last_success, then delete.
    Returns (#newly seen, new last_success).
    """
    try:
        data = json.loads(paths.candidates.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise NothingToCommit(f"{paths.candidates} not found") from exc
    generated_at = parse_iso(data.get("generated_at")) or now
    with SeenStore(paths.seen_db) as seen:
        added = seen.add((c.get("url", "") for c in data.get("candidates", [])), now)
    stamp = to_iso(generated_at)
    atomic_write_text(paths.last_success, stamp + "\n")
    paths.candidates.unlink()
    return added, stamp
