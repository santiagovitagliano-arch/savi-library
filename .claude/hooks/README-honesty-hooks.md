# Self-contained honesty ("anti-lying") hooks

Two hooks that discourage and catch the classic failure where the agent
*claims* work is done/tested/committed without actually doing or observing it.
No Slack, no MCP tools, no CCR env vars — works in any plain Claude Code session.

## Files
- `honesty-reminder.py` — **UserPromptSubmit** hook. Injects a per-turn
  reminder: don't claim tests pass / build succeeds / committed / "works"
  unless you actually ran it and saw the result this session.
- `honesty-stop-gate.py` — **Stop** hook. Blocks a turn from ending when the
  final assistant message contains a completion/verification claim but the turn
  ran **zero tools** to back it up. Re-prompts to verify-now-or-qualify.
- Wired in `~/.claude/settings.json`.

## What the Stop gate does / doesn't catch
- ✅ Catches: a confident "all tests pass / committed and pushed / verified
  that it works" produced from pure narration (no tool ran this turn).
- ✅ Optional (`HONESTY_GATE_GIT=1`): a commit/push claim while `git status`
  shows a dirty tree or unpushed commits — a directly verifiable lie.
- ❌ Does NOT catch a lie made *after* running a tool whose output actually
  showed failure. That would require correlating claims to tool-result exit
  codes; out of scope by design to keep false positives near zero.

Safety: fails **open** on any error, and a per-turn counter caps re-prompts
(default 2) well below the CLI's global 8-block ceiling, so it can never trap a
session.

## Config (all optional, via environment)
| Var | Effect |
|-----|--------|
| `HONESTY_GATE_DISABLE=1` | Stop gate no-ops |
| `HONESTY_GATE_CAP=<n>` | Per-turn block cap (default 2) |
| `HONESTY_GATE_GIT=1` | Enable the git commit/push claim check |
| `HONESTY_GATE_MODEL=<substr>` | Only gate models whose id contains substr (e.g. `opus`) |
| `HONESTY_GATE_PATTERNS=<a\|\|b>` | Replace default claim regexes (`\|\|`-separated) |
| `HONESTY_REMINDER_DISABLE=1` | Reminder no-ops |
| `HONESTY_REMINDER_BODY=<text>` | Override the reminder text |

## Two ways to use these

### A. Per-repo (this repo) — zero install
The hooks live in this repo at `.claude/hooks/` and are wired in
`.claude/settings.json`, which points at them via `$CLAUDE_PROJECT_DIR`. Any
`git clone` of this repo carries them along, and Claude Code loads a project's
`.claude/settings.json` automatically. Nothing to install — they apply whenever
you run Claude Code **inside this repo**. This survives ephemeral/rebuilt
containers because the repo is the thing that persists.

### B. Global (other users / other computers) — run the installer
To make the hooks apply to **every** repo and session for a user, install them
into that user's `~/.claude/`:

```bash
# for the user running the command:
.claude/hooks/install-honesty-hooks.sh

# remove them again:
.claude/hooks/install-honesty-hooks.sh --uninstall

# install for a different user on the same machine (needs write access to their home):
sudo -u bob TARGET_HOME=/home/bob .claude/hooks/install-honesty-hooks.sh
#   ...or as root:
TARGET_HOME=/home/bob .claude/hooks/install-honesty-hooks.sh
```

For **another computer**: copy this `hooks/` directory over (or clone the repo)
and run `install-honesty-hooks.sh` there. Requirements: `bash`, `python3`, `jq`.

The installer is idempotent and **merges** into an existing
`~/.claude/settings.json` — your other settings and hooks are preserved, and
re-running de-duplicates rather than stacking. `--uninstall` removes only the
honesty entries and leaves everything else intact.

## Testing
`test_honesty_hooks.sh` (kept alongside these scripts / in scratchpad during
development) drives every branch by feeding crafted transcript fixtures + stdin
payloads to each hook (16 cases, all green), plus installer install/idempotency/
merge-preserve/uninstall checks.

## Note on ephemeral environments
A cloud container's `~/.claude` is regenerated per session, so a **global**
(option B) install does not survive a container rebuild there. The **per-repo**
(option A) setup does, because it rides along with the repo checkout. On a normal
persistent machine, the global install sticks like any other dotfile.
