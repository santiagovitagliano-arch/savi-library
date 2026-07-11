#!/bin/bash
# Test harness for the self-contained honesty hooks.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GATE="$HERE/honesty-stop-gate.py"
REM="$HERE/honesty-reminder.py"
WORK="$(mktemp -d)/fixtures"
mkdir -p "$WORK"
PASS=0; FAIL=0

reset_counter() { rm -f "$HOME/.honesty-gate/turn-counter.json"; }

# make_transcript <file> <uuid> <assistant_json_content>
make_transcript() {
  local file="$1" uuid="$2" content="$3"
  {
    printf '%s\n' "{\"type\":\"user\",\"uuid\":\"$uuid\",\"message\":{\"role\":\"user\",\"content\":\"do the thing\"}}"
    printf '%s\n' "{\"type\":\"assistant\",\"message\":{\"model\":\"claude-opus-4-8\",\"content\":$content}}"
  } > "$file"
}

# run_gate <transcript_path> -> prints stdout, sets RC
run_gate() {
  OUT="$(printf '{"transcript_path":"%s","stop_hook_active":false}' "$1" | python3 "$GATE" 2>/dev/null)"
  RC=$?
}

check() {
  local name="$1" want="$2" got="$3"
  if [[ "$got" == "$want" ]]; then echo "PASS: $name"; PASS=$((PASS+1));
  else echo "FAIL: $name  (want=[$want] got=[$got])"; FAIL=$((FAIL+1)); fi
}

echo "===== honesty-stop-gate.py ====="

# 1. Completion claim, zero tools -> BLOCK
reset_counter
make_transcript "$WORK/t1.jsonl" "u1" '[{"type":"text","text":"All tests pass and I committed the changes."}]'
run_gate "$WORK/t1.jsonl"
DEC="$(echo "$OUT" | jq -r '.decision' 2>/dev/null)"
check "claim + no tools -> block" "block" "$DEC"

# 2. Same claim but a tool ran this turn -> ALLOW (empty stdout)
reset_counter
make_transcript "$WORK/t2.jsonl" "u2" '[{"type":"tool_use","name":"Bash","id":"x","input":{}},{"type":"text","text":"All tests pass."}]'
run_gate "$WORK/t2.jsonl"
check "claim + tool ran -> allow" "" "$OUT"

# 3. No completion claim -> ALLOW
reset_counter
make_transcript "$WORK/t3.jsonl" "u3" '[{"type":"text","text":"Here is a summary of the code you asked about."}]'
run_gate "$WORK/t3.jsonl"
check "no claim -> allow" "" "$OUT"

# 4. Empty / missing transcript -> ALLOW
reset_counter
run_gate "/nonexistent/path.jsonl"
check "missing transcript -> allow" "" "$OUT"

# 5. Malformed stdin -> ALLOW
reset_counter
OUT="$(printf 'not json' | python3 "$GATE" 2>/dev/null)"; RC=$?
check "malformed stdin -> allow" "" "$OUT"

# 6. Counter cap: 3rd identical block-worthy stop for same turn should ALLOW (cap=2)
reset_counter
make_transcript "$WORK/t6.jsonl" "u6" '[{"type":"text","text":"All tests pass."}]'
run_gate "$WORK/t6.jsonl"; A="$(echo "$OUT" | jq -r '.decision' 2>/dev/null)"
run_gate "$WORK/t6.jsonl"; B="$(echo "$OUT" | jq -r '.decision' 2>/dev/null)"
run_gate "$WORK/t6.jsonl"; C="$OUT"
check "cap: block #1" "block" "$A"
check "cap: block #2" "block" "$B"
check "cap: allow after cap(2)" "" "$C"

# 7. HONESTY_GATE_DISABLE=1 -> ALLOW
reset_counter
make_transcript "$WORK/t7.jsonl" "u7" '[{"type":"text","text":"All tests pass."}]'
OUT="$(HONESTY_GATE_DISABLE=1 bash -c "printf '{\"transcript_path\":\"$WORK/t7.jsonl\"}' | python3 '$GATE'" 2>/dev/null)"
check "disable flag -> allow" "" "$OUT"

# 8. Model gate mismatch -> ALLOW (transcript model is opus, require 'sonnet')
reset_counter
OUT="$(HONESTY_GATE_MODEL=sonnet bash -c "printf '{\"transcript_path\":\"$WORK/t7.jsonl\"}' | python3 '$GATE'" 2>/dev/null)"
check "model gate mismatch -> allow" "" "$OUT"

# 8b. Model gate match -> BLOCK
reset_counter
OUT="$(HONESTY_GATE_MODEL=opus bash -c "printf '{\"transcript_path\":\"$WORK/t7.jsonl\"}' | python3 '$GATE'" 2>/dev/null)"
DEC="$(echo "$OUT" | jq -r '.decision' 2>/dev/null)"
check "model gate match -> block" "block" "$DEC"

# 9. Sidechain (subagent) assistant text must NOT count. Claim only in a
#    sidechain entry -> ALLOW.
reset_counter
{
  printf '%s\n' '{"type":"user","uuid":"u9","message":{"role":"user","content":"go"}}'
  printf '%s\n' '{"type":"assistant","isSidechain":true,"message":{"model":"claude-opus-4-8","content":[{"type":"text","text":"All tests pass."}]}}'
  printf '%s\n' '{"type":"assistant","message":{"model":"claude-opus-4-8","content":[{"type":"text","text":"Working on it."}]}}'
} > "$WORK/t9.jsonl"
run_gate "$WORK/t9.jsonl"
check "sidechain claim ignored -> allow" "" "$OUT"

echo
echo "===== honesty-reminder.py ====="

# R1. Normal -> emits additionalContext
OUT="$(printf '{}' | python3 "$REM" 2>/dev/null)"
CTX="$(echo "$OUT" | jq -r '.hookSpecificOutput.additionalContext' 2>/dev/null)"
if [[ -n "$CTX" && "$CTX" != "null" ]]; then check "reminder emits context" "1" "1"; else check "reminder emits context" "1" "0"; fi
EVT="$(echo "$OUT" | jq -r '.hookSpecificOutput.hookEventName' 2>/dev/null)"
check "reminder event name" "UserPromptSubmit" "$EVT"

# R2. Disable -> no output
OUT="$(HONESTY_REMINDER_DISABLE=1 bash -c "printf '{}' | python3 '$REM'" 2>/dev/null)"
check "reminder disabled -> empty" "" "$OUT"

# R3. Custom body override
OUT="$(HONESTY_REMINDER_BODY='CUSTOM_XYZ' bash -c "printf '{}' | python3 '$REM'" 2>/dev/null)"
CTX="$(echo "$OUT" | jq -r '.hookSpecificOutput.additionalContext' 2>/dev/null)"
check "reminder custom body" "CUSTOM_XYZ" "$CTX"

echo
echo "===== RESULT: $PASS passed, $FAIL failed ====="
[[ "$FAIL" -eq 0 ]]
