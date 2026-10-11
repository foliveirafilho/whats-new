# Internal contract (fetcher ↔ orchestrator)

## Layout
```
pyproject.toml            uv project, package `whats_new` under src/, console script `whats-new`
src/whats_new/            fetch, dedup, state logic (Python 3.12, stdlib + feedparser + httpx + selectolax/bs4 allowed)
tests/                    pytest
sources.toml              source list (name, category, kind=rss|hn|github_trending|hf_models|hf_papers|scrape, url, filters)
profile.md                user-edited personalization (orchestrator owns)
prompts/curate.md         curation prompt for `claude -p` (orchestrator owns)
bin/run.sh                daily entrypoint (orchestrator owns)
systemd/                  whats-new.service + whats-new.timer (orchestrator owns)
reports/YYYY-MM-DD.md     output (tracked in git)
state/                    gitignored: seen.sqlite, last_success, candidates.json, logs/
```

## CLI (fetcher owns)
- `uv run whats-new fetch` — reads `state/last_success` (ISO-8601 UTC; if missing → now-48h) as `since`.
  Pulls every source in `sources.toml`; keeps items published after `since` (items with no date are kept);
  drops URLs (normalized: strip utm_*/fragment/trailing slash) already in `state/seen.sqlite`;
  MERGES with any existing `state/candidates.json` left by a failed run (dedupe by normalized URL);
  writes `state/candidates.json` atomically. A dead source never fails the command — it is recorded in `sources_health`.
  Exit 0 on success (even with zero candidates), non-zero only on internal bugs.
- `uv run whats-new commit` — called by run.sh ONLY after a report was written. Inserts every candidate URL into
  `seen`, writes `generated_at` of candidates.json into `state/last_success`, deletes `state/candidates.json`.
- `uv run whats-new status` — prints since, #candidates, sources health (debug aid).
- `uv run whats-new render-html --out <path>` — renders every `reports/YYYY-MM-DD[-n].md` into one
  self-contained, sanitised HTML page (newest first, `<section id="YYYY-MM-DD[-n]">` per run). Deterministic
  output. Used by `bin/publish.sh` (gh-pages publishing, orchestrator owns).

## state/candidates.json
```json
{
  "generated_at": "2026-10-11T10:30:00Z",
  "since": "2026-10-10T10:30:00Z",
  "sources_health": [
    {"source": "OpenAI Blog", "category": "lab", "status": "ok", "items": 3, "error": null}
  ],
  "candidates": [
    {
      "id": "sha1-of-normalized-url (12 chars)",
      "source": "OpenAI Blog",
      "category": "lab | news | community",
      "title": "…",
      "url": "https://…",
      "published": "2026-10-10T18:00:00Z or null",
      "summary": "plain text, HTML stripped, ≤ 400 chars",
      "signals": {"hn_points": 312, "hn_comments": 120, "discussion_url": "…", "stars_today": 800}
    }
  ]
}
```
`signals` keys are optional per source. Per-source cap 25 items, total cap 250 (prefer lab > community-by-score > news).
