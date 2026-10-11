#!/usr/bin/env bash
# Daily entrypoint for whats-new: fetch -> curate with Claude (read-only) -> save report -> commit state -> publish -> notify.
#
# Usage: bin/run.sh [--dry-run]
#   --dry-run   run the fetch, then print the exact claude command instead of running it.
#
# Env overrides:
#   WHATS_NEW_RETRY_DELAY   seconds to wait before retrying a failed Claude run (default 600)
#   WHATS_NEW_CLAUDE_BIN    claude binary (default $HOME/.local/bin/claude)
#   WHATS_NEW_CLAUDE_TIMEOUT  max seconds per Claude run (default 1200)
#   WHATS_NEW_PUBLISH       set to 0 to skip publishing the HTML digest to GitHub Pages (default 1)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

export PATH="$HOME/.local/bin:$PATH"
# notify-send talks to the session bus; systemd user services usually have it, cron/ssh may not.
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=/run/user/$(id -u)/bus}"

TODAY="$(date +%F)"
RETRY_DELAY="${WHATS_NEW_RETRY_DELAY:-600}"
CLAUDE_BIN="${WHATS_NEW_CLAUDE_BIN:-$HOME/.local/bin/claude}"
CLAUDE_TIMEOUT="${WHATS_NEW_CLAUDE_TIMEOUT:-1200}"
PREFLIGHT="$HOME/.claude/account-pool/bin/pool_preflight.sh"

mkdir -p state/logs reports
LOG_FILE="state/logs/$TODAY.log"
exec > >(tee -a "$LOG_FILE") 2>&1

log() { printf '[%s] %s\n' "$(date '+%F %T')" "$*"; }

notify() { # notify [-u urgency] title body
  local urgency=normal
  if [[ "${1:-}" == "-u" ]]; then urgency="$2"; shift 2; fi
  notify-send -u "$urgency" -a whats-new "$1" "$2" 2>/dev/null || log "notify-send failed (no session bus?): $1 — $2"
}

fail() {
  log "ERROR: $*"
  notify -u critical "What's new in AI — failed" "$* (log: $REPO_ROOT/$LOG_FILE)"
  exit 1
}

# One run at a time (timer + manual run overlap).
exec 9>state/.run.lock
flock -n 9 || { log "another run is in progress; exiting"; exit 0; }

log "=== whats-new run start (dry_run=$DRY_RUN) ==="

# ---- 1. fetch -----------------------------------------------------------------
log "fetching sources"
uv run whats-new fetch || fail "fetch step failed"
[[ -f state/candidates.json ]] || fail "fetch did not produce state/candidates.json"

# ---- 2. curate ----------------------------------------------------------------
# Claude gets NO write tool and no shell: only Read + WebFetch. This script writes the report.
CLAUDE_CMD=(
  "$CLAUDE_BIN" -p
  --model sonnet
  --restricted
  --safe-mode
  --strict-mcp-config
  --tools "Read,WebFetch"
  --allowedTools "Read WebFetch"
  --permission-mode dontAsk
  --permission-prompts none
  --no-session-persistence
  --output-format json
  --max-budget-usd 3
)

render_prompt() { sed "s/{{DATE}}/$TODAY/g" prompts/curate.md; }

if (( DRY_RUN )); then
  log "dry run: fetch done, not calling Claude"
  printf 'cd %q && sed "s/{{DATE}}/%s/g" prompts/curate.md | timeout %s' "$REPO_ROOT" "$TODAY" "$CLAUDE_TIMEOUT"
  printf ' %q' "${CLAUDE_CMD[@]}"
  printf '\n'
  exit 0
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

# Runs Claude once; on success leaves the validated report in $TMP_DIR/report.md.
run_claude() {
  local raw="$TMP_DIR/claude.json" report="$TMP_DIR/report.md"
  rm -f "$raw" "$report"

  if [[ -f "$PREFLIGHT" ]]; then
    bash "$PREFLIGHT" || true
  fi

  log "running claude (sonnet, tools: Read,WebFetch)"
  local rc=0
  render_prompt | timeout "$CLAUDE_TIMEOUT" "${CLAUDE_CMD[@]}" >"$raw" || rc=$?
  if (( rc != 0 )); then
    log "claude exited with code $rc"
    head -c 2000 "$raw" 2>/dev/null || true; echo
    # A JSON error payload may still be present; fall through to parsing for a better message.
  fi

  if ! python3 - "$raw" "$report" <<'PY'
import json, sys
raw_path, out_path = sys.argv[1], sys.argv[2]
try:
    with open(raw_path, encoding="utf-8") as f:
        data = json.load(f)
except Exception as e:
    print(f"could not parse claude JSON output: {e}", file=sys.stderr)
    sys.exit(1)
if isinstance(data, list):  # defensive: some versions emit an array of events
    data = next((d for d in reversed(data) if isinstance(d, dict) and d.get("type") == "result"), {})
print(f"claude result: subtype={data.get('subtype')} is_error={data.get('is_error')} "
      f"cost_usd={data.get('total_cost_usd')} turns={data.get('num_turns')} "
      f"duration_ms={data.get('duration_ms')} "
      f"permission_denials={[d.get('tool_name') for d in data.get('permission_denials') or []]}",
      file=sys.stderr)
if data.get("is_error") or data.get("subtype") != "success":
    print(f"claude reported failure: {str(data.get('result', ''))[:500]}", file=sys.stderr)
    sys.exit(1)
text = (data.get("result") or "").strip()
# Tolerate a stray preamble or code fence: start at the required heading.
idx = text.find("# What's new in AI")
if idx == -1:
    print("output does not contain '# What's new in AI' heading; first 300 chars:", file=sys.stderr)
    print(text[:300], file=sys.stderr)
    sys.exit(1)
text = text[idx:]
if text.endswith("```"):
    text = text[:-3].rstrip()
with open(out_path, "w", encoding="utf-8") as f:
    f.write(text + "\n")
PY
  then
    return 1
  fi
  [[ -s "$report" ]] && head -n1 "$report" | grep -q "^# What's new in AI"
}

if ! run_claude; then
  log "claude run failed or produced invalid output; retrying in ${RETRY_DELAY}s"
  sleep "$RETRY_DELAY"
  run_claude || fail "Claude curation failed twice; candidates kept for the next run"
fi

# ---- 3. save report ------------------------------------------------------------
REPORT_PATH="reports/$TODAY.md"
n=2
while [[ -e "$REPORT_PATH" ]]; do
  REPORT_PATH="reports/$TODAY-$n.md"
  n=$((n + 1))
done
cp "$TMP_DIR/report.md" "$REPORT_PATH"
log "report written: $REPORT_PATH"

# ---- 4. advance state (only after the report exists) --------------------------
uv run whats-new commit || fail "report saved to $REPORT_PATH but 'whats-new commit' failed (state not advanced)"

# ---- 5. publish (best effort: a failure never fails the run or undoes state) ------
PAGES_URL=""
if [[ "${WHATS_NEW_PUBLISH:-1}" != "0" ]]; then
  log "publishing HTML digest to GitHub Pages"
  if PUBLISH_OUT="$(timeout 300 "$REPO_ROOT/bin/publish.sh")"; then
    PAGES_URL="$(printf '%s\n' "$PUBLISH_OUT" | tail -n1)"
    log "published: $PAGES_URL"
  else
    log "ERROR: publish failed (report and state are fine)"
    notify -u critical "What's new in AI — publish failed" \
      "Report saved to $REPO_ROOT/$REPORT_PATH but publishing to GitHub Pages failed (log: $REPO_ROOT/$LOG_FILE)"
  fi
else
  log "WHATS_NEW_PUBLISH=0: skipping publish"
fi

# ---- 6. notify -------------------------------------------------------------------
HEADLINE="$(sed -n 's/^> \*\*Headline:\*\* *//p' "$REPORT_PATH" | head -n1)"
[[ -n "$HEADLINE" ]] || HEADLINE="New report ready"
RUN_ID="$(basename "$REPORT_PATH" .md)"
if [[ -n "$PAGES_URL" ]]; then
  notify "What's new in AI" "$HEADLINE — ${PAGES_URL}#$RUN_ID (local: $REPO_ROOT/$REPORT_PATH)"
else
  notify "What's new in AI" "$HEADLINE — $REPO_ROOT/$REPORT_PATH"
fi
log "=== done ==="
