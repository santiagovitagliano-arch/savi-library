#!/usr/bin/env python3
"""Self-contained honesty reminder (UserPromptSubmit hook).

Injects a short standing directive as additionalContext on every user turn:
do not claim things are done/tested/committed unless you actually did and
observed them this session. This is the "carrot" that pairs with the
honesty-stop-gate.py "stick": the reminder discourages the fabrication, the
stop gate catches it when it happens anyway.

Self-contained: no Slack, no MCP tools, no CCR env vars. The reminder body is
embedded below and can be overridden with HONESTY_REMINDER_BODY. Setting
HONESTY_REMINDER_DISABLE=1 makes it a no-op. Fails open on any error.
"""

import json
import os
import sys

DEFAULT_BODY = (
    "Honesty check for this turn: do not state that tests pass, a build "
    "succeeds, a command ran successfully, changes were committed or pushed, "
    "or that something 'works' or is 'verified' unless you actually executed "
    "it this session and observed the result. If you did not verify, say so "
    "explicitly (e.g. 'I have not run this yet'). Report failures, skipped "
    "steps, and uncertainty plainly rather than implying success."
)


def main():
    if os.environ.get("HONESTY_REMINDER_DISABLE") == "1":
        sys.exit(0)

    # Drain stdin so the hook protocol is satisfied even though we don't need it.
    try:
        sys.stdin.read()
    except Exception:
        pass

    body = os.environ.get("HONESTY_REMINDER_BODY") or DEFAULT_BODY

    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": body,
            }
        },
        sys.stdout,
    )
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)  # never block a turn on a hook failure
