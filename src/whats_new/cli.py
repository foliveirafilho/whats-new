"""`whats-new fetch | commit | status | render-html`."""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from .state import NothingToCommit, Paths, SeenStore, commit, load_candidates, project_root, read_since
from .text import to_iso


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def cmd_fetch(paths: Paths) -> int:
    from .fetch import run_fetch

    payload = run_fetch(paths, _now())
    bad = [h for h in payload["sources_health"] if h["status"] != "ok"]
    print(
        f"wrote {paths.candidates}: {len(payload['candidates'])} candidates since {payload['since']}"
        f" ({len(payload['sources_health']) - len(bad)} sources ok, {len(bad)} failing)"
    )
    for h in bad:
        print(f"  ! {h['source']}: {h['error']}", file=sys.stderr)
    return 0


def cmd_commit(paths: Paths) -> int:
    try:
        added, stamp = commit(paths, _now())
    except NothingToCommit as exc:
        print(f"nothing to commit: {exc}", file=sys.stderr)
        return 1
    print(f"marked {added} new URLs as seen; last_success = {stamp}; removed {paths.candidates.name}")
    return 0


def cmd_status(paths: Paths) -> int:
    now = _now()
    since = read_since(paths, now)
    has_last = paths.last_success.exists()
    print(f"root:          {paths.root}")
    print(f"since:         {to_iso(since)}{'' if has_last else '  (no last_success yet: now-48h)'}")
    seen_count = 0
    if paths.seen_db.exists():
        with SeenStore(paths.seen_db) as seen:
            seen_count = seen.count()
    print(f"seen URLs:     {seen_count}")
    data = load_candidates(paths)
    if data is None:
        print("candidates:    none pending (state/candidates.json absent)")
        return 0
    cands = data["candidates"]
    by_cat = Counter(c.get("category") for c in cands)
    print(f"candidates:    {len(cands)} (generated_at {data.get('generated_at')}, since {data.get('since')})")
    print("               " + ", ".join(f"{k}={v}" for k, v in sorted(by_cat.items())))
    print("sources health:")
    for h in data.get("sources_health", []):
        mark = "ok " if h["status"] == "ok" else "ERR"
        line = f"  [{mark}] {h['source']:<28} {h['category']:<10} {h['items']:>3} items"
        if h.get("error"):
            line += f"  — {h['error']}"
        print(line)
    return 0


def cmd_render_html(paths: Paths, out: Path) -> int:
    from .html import write_digest

    n = write_digest(paths.root / "reports", out)
    print(f"wrote {out}: {n} report(s)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="whats-new", description="Fetch/dedupe/state half of the whats-new routine")
    parser.add_argument("--root", type=Path, default=None, help="project root (default: $WHATS_NEW_ROOT or the checkout)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("fetch", help="pull sources and write state/candidates.json")
    sub.add_parser("commit", help="after a report was written: mark candidates seen, advance last_success")
    sub.add_parser("status", help="print since, pending candidates and sources health")
    rh = sub.add_parser("render-html", help="render all reports/*.md into one self-contained HTML page")
    rh.add_argument("--out", type=Path, required=True, help="output HTML file")
    args = parser.parse_args(argv)
    paths = Paths((args.root or project_root()).resolve())
    if args.command == "render-html":
        return cmd_render_html(paths, args.out)
    handler = {"fetch": cmd_fetch, "commit": cmd_commit, "status": cmd_status}[args.command]
    return handler(paths)


if __name__ == "__main__":
    raise SystemExit(main())
