#!/usr/bin/env python3
"""Self-contained "anti-lying" Stop hook.

Purpose
-------
Stop a turn from ending when the final assistant message *claims* work is
complete or verified — "all tests pass", "committed and pushed", "verified
that it works", "ran successfully" — but the turn did no actual work to back
that claim up (zero tool calls this turn). This is the classic fabrication
shape: a confident completion claim produced from pure narration.

When that pattern is detected the hook returns {"decision":"block", ...},
which re-prompts the model with an instruction to either actually run the
verification now or explicitly qualify the statement as unverified.

This is a heuristic, not a proof. It is deliberately tuned for a LOW
false-positive rate:
  * It only fires on high-signal *verification* claims (test/build/deploy
    results, commit/push, "verified"/"confirmed"/"successfully"), never on a
    bare "done".
  * It only fires when the turn contained ZERO main-loop tool calls. If the
    model ran anything this turn, it is given the benefit of the doubt.
  * A per-turn counter caps re-prompts (default 2) well below the CLI's global
    8-block ceiling, so a model that will not comply still gets to stop.

Optional git check
------------------
Set HONESTY_GATE_GIT=1 to additionally block when the final message claims a
commit/push ("committed", "pushed", "merged") but `git status` shows a dirty
tree or unpushed commits. Off by default.

Everything below is self-contained: no Slack, no MCP tools, no CCR env vars.
The hook fails OPEN on any unexpected condition — it must never trap a session.

Config via environment (all optional):
  HONESTY_GATE_DISABLE=1      -> no-op (allow always)
  HONESTY_GATE_CAP=<int>      -> per-turn block cap (default 2)
  HONESTY_GATE_GIT=1          -> enable the git commit/push claim check
  HONESTY_GATE_MODEL=<substr> -> only gate models whose id contains <substr>
                                 (case-insensitive). Default: gate all models.
  HONESTY_GATE_PATTERNS=<a||b||c>
                              -> replace the default claim regexes with a
                                 "||"-separated list (case-insensitive).
"""

import json
import os
import re
import subprocess
import sys
import tempfile

DEFAULT_CAP = 2

# High-signal completion / verification claims. Kept intentionally narrow so a
# plain "I'm done" or "that should work" does not trip the gate. Case-insensitive.
DEFAULT_PATTERNS = [
    r"\ball\s+(?:the\s+)?tests?\s+(?:pass|passed|passing|are\s+green)\b",
    r"\btests?\s+(?:pass|passed|passing|are\s+green|now\s+pass)\b",
    r"\b(?:the\s+)?build\s+(?:succeed|succeeds|succeeded|passes|passed|is\s+green|works)\b",
    r"\b(?:successfully|success)\s+(?:ran|run|built|build|compiled|deployed|tested|completed|passed)\b",
    r"\bran\s+(?:it\s+)?(?:successfully|and\s+(?:it\s+)?(?:passed|passes|worked))\b",
    r"\b(?:i\s+)?(?:verified|confirmed)\s+that\b",
    r"\b(?:committed|pushed|merged|deployed)\s+(?:and\s+\w+\s+)?(?:the\s+)?(?:changes?|code|it|everything|fix|branch)\b",
    r"\bcommitted\s+and\s+pushed\b",
    r"\bno\s+(?:errors?|failures?)\b",
    r"\beverything\s+(?:works|passes|is\s+working|is\s+green)\b",
]

GIT_CLAIM = re.compile(r"\b(committed|pushed|merged)\b", re.IGNORECASE)


def allow():
    # exit 0 with no stdout = allow the stop.
    sys.exit(0)


def block(reason):
    json.dump({"decision": "block", "reason": reason}, sys.stdout)
    sys.exit(0)


def patterns():
    raw = os.environ.get("HONESTY_GATE_PATTERNS")
    src = raw.split("||") if raw else DEFAULT_PATTERNS
    out = []
    for p in src:
        p = p.strip()
        if not p:
            continue
        try:
            out.append(re.compile(p, re.IGNORECASE))
        except re.error:
            continue  # skip a bad user-supplied regex rather than crash
    return out


def cap():
    try:
        return int(os.environ.get("HONESTY_GATE_CAP", ""))
    except ValueError:
        return DEFAULT_CAP


def read_transcript(path):
    entries = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue  # tolerate a partially-written tail line
    except OSError:
        return []
    return entries


def model_gate_ok(entries):
    """Honor HONESTY_GATE_MODEL: gate only when the transcript's most recent
    main-loop assistant model id contains the configured substring. Absent the
    env var, gate all models. Indeterminate model -> gate (fail toward the
    check; the cap still bounds it)."""
    want = os.environ.get("HONESTY_GATE_MODEL", "").strip().lower()
    if not want:
        return True
    for entry in reversed(entries):
        if entry.get("type") != "assistant" or entry.get("isSidechain"):
            continue
        model = (entry.get("message") or {}).get("model")
        if model == "<synthetic>":
            continue
        return want in (model or "").lower()
    return True


def last_user_boundary(entries):
    """Index of the newest real user message (not meta, not a tool result, not
    a subagent/sidechain prompt). Everything after it is this turn."""
    for i in range(len(entries) - 1, -1, -1):
        e = entries[i]
        if (
            e.get("type") == "user"
            and not e.get("isMeta")
            and not e.get("toolUseResult")
            and not e.get("isSidechain")
        ):
            return i
    return -1


def turn_text_and_tools(entries, boundary_idx):
    """Return (final_assistant_text, tool_use_count) for main-loop assistant
    entries after the boundary. Sidechain (subagent) entries are ignored."""
    text_parts = []
    tool_count = 0
    for e in entries[boundary_idx + 1 :]:
        if e.get("type") != "assistant" or e.get("isSidechain"):
            continue
        content = (e.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for blk in content:
            if not isinstance(blk, dict):
                continue
            if blk.get("type") == "text":
                text_parts.append(blk.get("text") or "")
            elif blk.get("type") == "tool_use":
                tool_count += 1
    return "\n".join(text_parts), tool_count


def turn_key(entries, boundary_idx):
    if boundary_idx < 0:
        return "no-user-boundary"
    e = entries[boundary_idx]
    return e.get("uuid") or e.get("timestamp") or "unkeyed-boundary"


def counter_paths():
    # Base the counter on $HOME so it never lands inside a customer git tree
    # that a sibling git-check Stop hook would flag as untracked.
    home = os.path.expanduser("~")
    if not home or home == "~" or not os.access(home, os.W_OK):
        home = tempfile.gettempdir()
    d = os.path.join(home, ".honesty-gate")
    return d, os.path.join(d, "turn-counter.json")


def bump_counter(key):
    d, path = counter_paths()
    state = {}
    try:
        with open(path, encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, json.JSONDecodeError, ValueError):
        state = {}
    if not isinstance(state, dict):
        state = {}
    count = state.get("count", 0) if state.get("turn_key") == key else 0
    count += 1
    try:
        os.makedirs(d, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"turn_key": key, "count": count}, f)
    except OSError:
        pass
    return count


def git_is_dirty_or_unpushed():
    """Best-effort: True if the working tree has changes or there are unpushed
    commits on the current branch. Any git failure returns False (fail open)."""
    def git(*args):
        return subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            timeout=10,
        )
    try:
        if git("rev-parse", "--git-dir").returncode != 0:
            return False
        status = git("status", "--porcelain")
        if status.returncode == 0 and status.stdout.strip():
            return True
        branch = git("branch", "--show-current").stdout.strip()
        if not branch:
            return False
        if git("rev-parse", f"origin/{branch}").returncode != 0:
            return False  # no upstream to compare against
        rev = git("rev-list", f"origin/{branch}..HEAD", "--count")
        if rev.returncode == 0 and rev.stdout.strip().isdigit():
            return int(rev.stdout.strip()) > 0
    except (OSError, subprocess.SubprocessError, ValueError):
        return False
    return False


def run():
    if os.environ.get("HONESTY_GATE_DISABLE") == "1":
        allow()

    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        allow()

    entries = read_transcript(payload.get("transcript_path") or "")
    if not entries:
        allow()

    if not model_gate_ok(entries):
        allow()

    boundary = last_user_boundary(entries)
    text, tool_count = turn_text_and_tools(entries, boundary)
    if not text.strip():
        allow()

    claimed = None
    for p in patterns():
        m = p.search(text)
        if m:
            claimed = m.group(0).strip()
            break
    if not claimed:
        allow()

    # Optional git check: a commit/push claim against a dirty/unpushed tree is a
    # verifiable lie regardless of whether tools ran this turn.
    if (
        os.environ.get("HONESTY_GATE_GIT") == "1"
        and GIT_CLAIM.search(text)
        and git_is_dirty_or_unpushed()
    ):
        key = turn_key(entries, boundary)
        if bump_counter(key) > cap():
            allow()
        block(
            "Your message claims changes were committed/pushed, but the working "
            "tree still has uncommitted changes or unpushed commits. Either "
            "actually commit and push now, or correct the statement to say what "
            "is really outstanding. Do not report the work as saved when it is not."
        )

    # Core anti-lying check: a completion/verification claim with no work behind
    # it this turn.
    if tool_count > 0:
        allow()  # the model did something this turn; give benefit of the doubt

    key = turn_key(entries, boundary)
    if bump_counter(key) > cap():
        allow()  # degrade below the CLI global block ceiling

    block(
        "Your message claims work was completed or verified (matched: "
        f"\"{claimed}\"), but you did not run any tool this turn to back that up. "
        "Either run the verification now (execute the tests/build/command and "
        "show the real output), or rewrite the statement to say honestly that it "
        "is NOT yet verified. Do not assert that something passes, builds, is "
        "committed, or works unless you actually observed it this session."
    )


def main():
    try:
        run()
    except Exception as e:
        # Fail open; only the exception type name (never user content).
        sys.stderr.write("honesty_gate_allow exc_type=%s\n" % type(e).__name__)
        sys.exit(0)


if __name__ == "__main__":
    main()
