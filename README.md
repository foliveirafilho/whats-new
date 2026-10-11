# whats-new

A local routine that runs every morning, finds what's new in AI since the last
successful run, and writes a personal report: featured stories with all their
source links, "Why it matters", a "Try it in 15 min" step, and product ideas
for your work, side projects and dev tooling. When it's done you get a
desktop notification with the headline and the path of the report.

## How it works

```
systemd user timer (07:30, Persistent)
  └─ bin/run.sh
       1. uv run whats-new fetch    → state/candidates.json   (Python: RSS/HN/GitHub/HF, dedup vs SQLite)
       2. claude -p (Sonnet)        → reads candidates + profile.md, WebFetches the top ~10, prints the report
       3. run.sh saves it           → reports/YYYY-MM-DD.md   (-2, -3… if one already exists today)
       4. uv run whats-new commit   → marks URLs seen, advances state/last_success, deletes candidates.json
       5. bin/publish.sh            → renders ALL reports into one HTML page, pushes it to gh-pages (best effort)
       6. notify-send               → "<headline> — <pages url>#<run> (local: <path>)"
```

- **Coverage window:** everything since the last *successful* run (first run: last 48h).
  The window only advances when a report was actually written.
- **Failures:** a dead source is skipped and listed in the report's "Sources health"
  footer. If Claude fails or prints something that isn't a report, `run.sh` waits
  10 minutes and retries once; if that fails too you get a critical notification
  and `state/candidates.json` is kept, so the next run covers those items too.

## Setup

```bash
cd ~/Documents/projects/whats-new
uv sync                     # install the fetcher
$EDITOR profile.md          # fill in the <!-- TODO --> placeholders — ranking depends on it
bin/run.sh --dry-run        # fetch + print the exact claude command, without calling Claude
bin/install.sh              # install + enable the systemd user timer
```

`bin/install.sh` renders `systemd/whats-new.{service,timer}` with this checkout's
absolute path into `~/.config/systemd/user/`, reloads systemd and enables the
timer. Re-run it if you move the repo or edit the units.

Requirements: `uv` and `claude` in `~/.local/bin`, Claude Code logged in
(OAuth is fine — the run does not use `--bare`), `notify-send` (libnotify).

**Linger:** not needed on a desktop where you log in every day — the user
manager runs while you're logged in, and `Persistent=true` catches up a missed
07:30 run at the next login/boot. Only if you want it to run with no session at
all would you need `loginctl enable-linger $USER` (and then notifications have
nowhere to go anyway).

## Manual run

```bash
bin/run.sh                              # full run, same as the timer
systemctl --user start whats-new        # run through systemd (logs in journal)
WHATS_NEW_RETRY_DELAY=5 bin/run.sh      # shorter retry wait while testing
uv run whats-new status                 # since, #candidates, sources health
```

## Publishing (GitHub Pages)

After every successful run, `bin/publish.sh` renders **all** `reports/*.md` into a
single self-contained HTML page (newest run first, a table of contents at the
top, each run in `<section id="YYYY-MM-DD[-n]">`) and pushes it to the
`gh-pages` branch. The page is rebuilt from every report each time, so new runs
(including `-2`, `-3` reruns of the same day) simply show up at the top.

- **Find the URL:** `cat state/pages.conf` →
  `https://foliveirafilho.github.io/whats-new/<PAGES_TOKEN>/`. The success
  notification also carries the link (with `#<run>` anchor), and
  `bin/publish.sh` prints it as its last line.
- **Pages is public** even though the repo is private. The page lives under an
  unguessable 24-char path; the site root is a blank page, `robots.txt` disallows
  everything and the digest has `noindex,nofollow` + `no-referrer`. Treat the URL
  like a secret link: anyone who has it can read it.
- **gh-pages contains only** `.nojekyll`, a blank `index.html`, `robots.txt` and
  `<token>/index.html`. Publishing uses its own clone in `state/pages-repo/`;
  your working tree and branch are never touched. A commit is only made when
  the page actually changed.
- **Rendering is sanitised** (reports come from untrusted web pages): raw HTML in
  the markdown is escaped, only `http(s)`/`mailto` links and `http(s)` images are
  kept (anything else, e.g. `javascript:`, stays as plain text), and links get
  `rel="noopener noreferrer"`. Preview locally with
  `uv run whats-new render-html --out /tmp/digest.html`.
- **Skip publishing:** `WHATS_NEW_PUBLISH=0 bin/run.sh`. A failed publish sends a
  critical notification and is logged, but never fails the run or undoes state;
  the next run republishes everything. Publish by hand any time with `bin/publish.sh`.
- **Rotate the token** (e.g. the link leaked): `rm state/pages.conf` won't do it
  (the token is recovered from the branch). Instead write a new one and publish —
  the old directory is removed from `gh-pages` on the next publish:
  ```bash
  printf 'PAGES_TOKEN=%s\n' "$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')" > state/pages.conf
  bin/publish.sh
  ```
  (The old page stays in the branch history of the private repo, but is no
  longer served.)
- **One-time Pages setup:** after the first publish,
  `gh api -X POST repos/foliveirafilho/whats-new/pages -f "source[branch]=gh-pages" -f "source[path]=/"`.
  Pages on a **private** repo needs a paid plan (GitHub Pro) on a personal account;
  on the free plan this call fails with `422 Your current plan does not support
  GitHub Pages for this repository.`

## Where things go

| Path | What |
|---|---|
| `reports/YYYY-MM-DD.md` | the reports (tracked in git) |
| `profile.md` | who the report is for — edit by hand |
| `prompts/curate.md` | the curation prompt (`{{DATE}}` is substituted by run.sh) |
| `sources.toml` | the source list |
| `state/` (gitignored) | `seen.sqlite`, `last_success`, `candidates.json`, `logs/YYYY-MM-DD.log`, `pages.conf` (Pages token), `pages-repo/` (publish clone) |

## Security model

The Claude step reads untrusted web content (feeds and fetched pages), so it is
locked down:

- **Tools: `Read` and `WebFetch` only.** No Bash, no Edit/Write, no MCP servers.
  Claude cannot write files at all — `run.sh` takes the report from Claude's
  JSON output on stdout, checks it starts with `# What's new in AI`, and writes
  it itself. (This is stricter than the original design, which allowed writes
  under `reports/`.)
- `--restricted` removes the code-running tools and ignores user/project/local
  settings; `--safe-mode` disables CLAUDE.md, hooks, skills, plugins, MCP;
  `--strict-mcp-config` with no `--mcp-config` means zero MCP servers.
- `--permission-mode dontAsk --permission-prompts none`: anything not
  pre-approved is denied, nobody can be prompted.
- `--no-session-persistence`, `--max-budget-usd 3` per run, and a 20-minute
  timeout per Claude run.
- The prompt tells Claude explicitly that fetched content is data, never
  instructions, to read only `state/candidates.json` and `profile.md`, and to
  fetch only URLs that appear in the candidates.

Exact command (see `bin/run.sh --dry-run`):

```
sed "s/{{DATE}}/<today>/g" prompts/curate.md | timeout 1200 ~/.local/bin/claude -p \
  --model sonnet --restricted --safe-mode --strict-mcp-config \
  --tools "Read,WebFetch" --allowedTools "Read WebFetch" \
  --permission-mode dontAsk --permission-prompts none \
  --no-session-persistence --output-format json --max-budget-usd 3
```

If `~/.claude/account-pool/bin/pool_preflight.sh` exists, it runs right before
each Claude call so scheduled runs get the same account rotation as the
interactive `claude` shell function.

## Troubleshooting

- **Logs:** `state/logs/YYYY-MM-DD.log` (everything run.sh prints, including the
  Claude result subtype and cost), or `journalctl --user -u whats-new` for runs
  started by systemd.
- **Is the timer scheduled?** `systemctl --user list-timers whats-new.timer`
- **No notification:** run.sh sets `DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$UID/bus`
  when it's unset; check `notify-send test` works in your session. A failed
  notification is logged, it never fails the run.
- **"Claude curation failed twice":** look at the log for `subtype=` /
  `is_error=` (e.g. budget exceeded, auth expired → run `claude` interactively to
  log in again). Candidates are kept; the next run picks them up.
- **Report seems stale or empty:** `uv run whats-new status` shows the `since`
  timestamp and which sources are failing.
- **Two runs at once:** run.sh takes a lock (`state/.run.lock`); a second run exits immediately.
- **Uninstall:** `systemctl --user disable --now whats-new.timer && rm ~/.config/systemd/user/whats-new.{service,timer} && systemctl --user daemon-reload`
