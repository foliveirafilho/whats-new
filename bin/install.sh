#!/usr/bin/env bash
# Installs the whats-new systemd *user* units (service + daily timer) for this checkout.
# Re-run after moving the repo or editing systemd/*.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

mkdir -p "$UNIT_DIR"
chmod +x "$REPO_ROOT/bin/run.sh"

for unit in whats-new.service whats-new.timer; do
  sed "s|@REPO_ROOT@|$REPO_ROOT|g" "$REPO_ROOT/systemd/$unit" > "$UNIT_DIR/$unit"
  echo "installed $UNIT_DIR/$unit"
done

systemctl --user daemon-reload
systemctl --user enable --now whats-new.timer
echo
systemctl --user list-timers whats-new.timer --no-pager
echo
echo "Run once now:   systemctl --user start whats-new.service"
echo "Follow logs:    journalctl --user -u whats-new -f"
