#!/usr/bin/env bash
# Publish all reports as one HTML digest to the gh-pages branch (GitHub Pages).
#
# Usage: bin/publish.sh
#   Prints the digest URL on stdout (last line); progress goes to stderr.
#
# The gh-pages branch contains ONLY: .nojekyll, a blank index.html, robots.txt and
# <TOKEN>/index.html (the digest). TOKEN is an unguessable 24-char string stored in
# state/pages.conf (PAGES_TOKEN=...). Publishing happens in a dedicated clone at
# state/pages-repo/, so the main checkout and its branch are never touched.
#
# Env overrides:
#   WHATS_NEW_PAGES_REMOTE   git remote URL to push to (default: origin of this checkout)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
export PATH="$HOME/.local/bin:$PATH"

CONF="state/pages.conf"
PAGES_REPO="$REPO_ROOT/state/pages-repo"
BRANCH="gh-pages"
TRAILER="Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
REMOTE="${WHATS_NEW_PAGES_REMOTE:-$(git -C "$REPO_ROOT" remote get-url origin)}"

log() { printf '[publish] %s\n' "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }

mkdir -p state
exec 8>state/.publish.lock
flock -w 300 8 || die "another publish is still running"

# ---- dedicated clone ------------------------------------------------------------
if [[ ! -d "$PAGES_REPO/.git" ]]; then
  log "initialising publish clone at $PAGES_REPO"
  rm -rf "$PAGES_REPO"
  git init -q "$PAGES_REPO"
  git -C "$PAGES_REPO" remote add origin "$REMOTE"
fi
git -C "$PAGES_REPO" remote set-url origin "$REMOTE"
# Commit as the main repo's identity (repo-local noreply email, not the global one).
for key in user.name user.email; do
  val="$(git -C "$REPO_ROOT" config "$key" || true)"
  [[ -n "$val" ]] && git -C "$PAGES_REPO" config "$key" "$val"
done
G=(git -C "$PAGES_REPO")

REMOTE_HAS_BRANCH=0
if "${G[@]}" ls-remote --exit-code --heads origin "$BRANCH" >/dev/null 2>&1; then
  REMOTE_HAS_BRANCH=1
  "${G[@]}" fetch -q origin "+refs/heads/$BRANCH:refs/remotes/origin/$BRANCH"
else
  rc=$?
  # ls-remote exits 2 when the ref is missing; anything else is a real error (network/auth).
  (( rc == 2 )) || die "cannot reach $REMOTE (git ls-remote exit $rc)"
fi

# ---- token ------------------------------------------------------------------------
PAGES_TOKEN=""
if [[ -f "$CONF" ]]; then
  PAGES_TOKEN="$(sed -n 's/^PAGES_TOKEN=//p' "$CONF" | head -n1)"
fi
if [[ -z "$PAGES_TOKEN" ]] && (( REMOTE_HAS_BRANCH )); then
  PAGES_TOKEN="$("${G[@]}" ls-tree -d --name-only "origin/$BRANCH" | grep -E '^[A-Za-z0-9_-]{24}$' | head -n1 || true)"
  [[ -n "$PAGES_TOKEN" ]] && log "recovered token from the $BRANCH branch"
fi
if [[ -z "$PAGES_TOKEN" ]]; then
  PAGES_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')"
  log "generated a new token"
fi
[[ "$PAGES_TOKEN" =~ ^[A-Za-z0-9_-]{24}$ ]] || die "invalid PAGES_TOKEN in $CONF (want 24 url-safe chars)"
if [[ "$(sed -n 's/^PAGES_TOKEN=//p' "$CONF" 2>/dev/null | head -n1)" != "$PAGES_TOKEN" ]]; then
  (umask 077; printf 'PAGES_TOKEN=%s\n' "$PAGES_TOKEN" >"$CONF")
fi

# Pages URL: https://<owner>.github.io/<repo>/<token>/
SLUG="$(printf '%s' "$REMOTE" | sed -E 's#^(git@github\.com:|https://github\.com/|ssh://git@github\.com/)##; s#\.git$##')"
OWNER="${SLUG%%/*}"; REPO_NAME="${SLUG#*/}"
PAGES_URL="https://${OWNER,,}.github.io/$REPO_NAME/$PAGES_TOKEN/"

# ---- branch -----------------------------------------------------------------------
if (( REMOTE_HAS_BRANCH )); then
  "${G[@]}" checkout -q -B "$BRANCH" "origin/$BRANCH"
  "${G[@]}" reset -q --hard "origin/$BRANCH"
elif ! "${G[@]}" rev-parse -q --verify "refs/heads/$BRANCH" >/dev/null; then
  "${G[@]}" checkout -q --orphan "$BRANCH"
else
  "${G[@]}" checkout -q "$BRANCH"
fi

# The branch holds only our four files: wipe everything else (old token dirs included).
find "$PAGES_REPO" -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
mkdir -p "$PAGES_REPO/$PAGES_TOKEN"
: >"$PAGES_REPO/.nojekyll"
printf 'User-agent: *\nDisallow: /\n' >"$PAGES_REPO/robots.txt"
cat >"$PAGES_REPO/index.html" <<'HTML'
<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="robots" content="noindex,nofollow"><title></title></head><body></body></html>
HTML
uv run --project "$REPO_ROOT" --quiet whats-new --root "$REPO_ROOT" render-html --out "$PAGES_REPO/$PAGES_TOKEN/index.html" >&2

"${G[@]}" add -A
if "${G[@]}" diff --cached --quiet && "${G[@]}" rev-parse -q --verify HEAD >/dev/null; then
  log "digest unchanged; nothing to push"
else
  LATEST="$(ls reports | grep -E '^[0-9]{4}-[0-9]{2}-[0-9]{2}(-[0-9]+)?\.md$' | sort -V | tail -n1 || true)"
  "${G[@]}" commit -q -m "Publish digest${LATEST:+ (latest: ${LATEST%.md})}" -m "$TRAILER"
  "${G[@]}" push -q origin "$BRANCH"
  log "pushed $BRANCH"
fi

printf '%s\n' "$PAGES_URL"
