#!/usr/bin/env bash
#
# Portable installer for the self-contained honesty ("anti-lying") hooks.
#
# Installs them GLOBALLY for whoever runs it — into that user's ~/.claude/ —
# so they apply to every Claude Code session and every repo for that user.
#
# Usage:
#   ./install-honesty-hooks.sh              # install for the current user
#   ./install-honesty-hooks.sh --uninstall  # remove them again
#   TARGET_HOME=/home/bob ./install-honesty-hooks.sh   # install for another user*
#
#   * requires permission to write that user's home (e.g. run via sudo -u bob,
#     or as root with TARGET_HOME set).
#
# For OTHER COMPUTERS: copy this whole hooks/ directory over (or clone the repo)
# and run this script there. It only needs bash, python3, and jq.
#
# Idempotent: re-running updates the scripts and de-duplicates the settings
# entries rather than stacking them. It MERGES into an existing
# ~/.claude/settings.json — your other settings and hooks are preserved.

set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST_HOME="${TARGET_HOME:-$HOME}"
DEST_CLAUDE="$DEST_HOME/.claude"
DEST_HOOKS="$DEST_CLAUDE/hooks"
SETTINGS="$DEST_CLAUDE/settings.json"

GATE_CMD='python3 ~/.claude/hooks/honesty-stop-gate.py'
REMINDER_CMD='python3 ~/.claude/hooks/honesty-reminder.py'

need() { command -v "$1" >/dev/null 2>&1 || { echo "ERROR: '$1' is required but not found." >&2; exit 1; }; }
need python3
need jq

# Start from whatever settings already exist (or an empty object), then strip
# any prior honesty entries so re-runs don't duplicate them.
read_settings() {
  if [[ -f "$SETTINGS" ]]; then
    jq '.' "$SETTINGS" 2>/dev/null || { echo "ERROR: $SETTINGS is not valid JSON; fix or remove it first." >&2; exit 1; }
  else
    echo '{}'
  fi
}

strip_ours() {
  # Remove any hook entries whose command references our two scripts, and drop
  # now-empty groups. Leaves every other hook untouched.
  jq '
    def clean(event):
      (.hooks[event] // [])
      | map(.hooks |= map(select((.command // "") | test("honesty-stop-gate.py|honesty-reminder.py") | not)))
      | map(select((.hooks | length) > 0));
    .hooks = (.hooks // {})
    | .hooks.UserPromptSubmit = clean("UserPromptSubmit")
    | .hooks.Stop            = clean("Stop")
    # prune empty arrays / empty hooks object for tidiness
    | .hooks |= with_entries(select(.value | length > 0))
    | if (.hooks | length) == 0 then del(.hooks) else . end
  '
}

do_install() {
  echo "Installing honesty hooks into: $DEST_CLAUDE"
  mkdir -p "$DEST_HOOKS"
  cp "$SRC_DIR/honesty-stop-gate.py" "$SRC_DIR/honesty-reminder.py" "$DEST_HOOKS/"
  [[ -f "$SRC_DIR/README-honesty-hooks.md" ]] && cp "$SRC_DIR/README-honesty-hooks.md" "$DEST_HOOKS/"
  chmod +x "$DEST_HOOKS/honesty-stop-gate.py" "$DEST_HOOKS/honesty-reminder.py"

  local merged
  merged="$(read_settings | strip_ours | jq \
    --arg gate "$GATE_CMD" --arg rem "$REMINDER_CMD" '
    .hooks = (.hooks // {})
    | .hooks.UserPromptSubmit = ((.hooks.UserPromptSubmit // []) + [{"hooks":[{"type":"command","command":$rem}]}])
    | .hooks.Stop            = ((.hooks.Stop // [])            + [{"matcher":"","hooks":[{"type":"command","command":$gate}]}])
  ')"
  printf '%s\n' "$merged" > "$SETTINGS"
  echo "Wrote $SETTINGS"
  echo "Done. New Claude Code sessions for this user will load the honesty hooks."
}

do_uninstall() {
  echo "Removing honesty hooks from: $DEST_CLAUDE"
  rm -f "$DEST_HOOKS/honesty-stop-gate.py" "$DEST_HOOKS/honesty-reminder.py" "$DEST_HOOKS/README-honesty-hooks.md"
  if [[ -f "$SETTINGS" ]]; then
    local cleaned
    cleaned="$(read_settings | strip_ours)"
    printf '%s\n' "$cleaned" > "$SETTINGS"
    echo "Cleaned settings entries from $SETTINGS"
  fi
  echo "Done."
}

case "${1:-}" in
  --uninstall|-u) do_uninstall ;;
  ""|--install|-i) do_install ;;
  *) echo "Usage: $0 [--install|--uninstall]" >&2; exit 2 ;;
esac
