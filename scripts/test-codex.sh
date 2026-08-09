#!/usr/bin/env bash
# Test Tier 2: `communicate codex ask` over a device, including resume continuity.
# Defaults to device=local. Pass a device as $1 to test over ssh.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLI="$ROOT/bin/communicate"
DEV="${1:-local}"

if [ -z "$("$CLI" codex probe "$DEV" 2>/dev/null)" ]; then
  echo "SKIP: codex not installed on '$DEV'"; exit 0
fi

echo "1) one-shot ask"
A="$("$CLI" codex ask "$DEV" --new --thread t$$ --dir /tmp -- 'Reply with exactly the word ASKPONG. No tools.' 2>/dev/null)"
echo "   -> $A"
printf '%s' "$A" | grep -qi ASKPONG || { echo "FAIL: bad one-shot reply"; exit 1; }

echo "2) continuity via resume"
"$CLI" codex ask "$DEV" --thread t$$ --dir /tmp -- 'Remember the number 8675309. Just say OK.' >/dev/null 2>&1
B="$("$CLI" codex ask "$DEV" --thread t$$ --dir /tmp -- 'What number did I ask you to remember? Digits only.' 2>/dev/null)"
echo "   -> $B"
printf '%s' "$B" | grep -q 8675309 || { echo "FAIL: resume did not recall the fact"; exit 1; }

"$CLI" codex forget "$DEV" >/dev/null 2>&1
echo "PASS: codex ask + resume continuity work on '$DEV'"
