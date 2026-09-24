#!/usr/bin/env bash
# Retained core regression gate. Every runtime resource is isolated by its fixture.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
for tool in python3 node npm tmux jq openssl; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'UNQUALIFIED: required test dependency missing: %s\n' "$tool" >&2
    exit 127
  }
done
export PYTHONDONTWRITEBYTECODE=1
python3 "$ROOT/scripts/test-homi-entrypoint.py"
python3 "$ROOT/scripts/test-homi-interface.py"
python3 "$ROOT/scripts/test-homi-seat-unit.py"
python3 "$ROOT/scripts/test-homi-control-token.py"
python3 "$ROOT/scripts/test-provider-qualification.py"
bash "$ROOT/scripts/test-native-fixtures.sh"
bash "$ROOT/scripts/test-homi-adopt.sh"
for suite in homi-seat-menu homi-core homi-seat homi-spawn homi-ask homi-link homi-seat-link homi-pair homi-connect; do
  bash "$ROOT/scripts/test-$suite.sh"
done
bash "$ROOT/scripts/test-bus.sh"
node "$ROOT/scripts/test-homi-mcp-interface.mjs"
npm --prefix "$ROOT/packages/communicate" test
printf 'PASS: retained core, namespace, seat, bus and package regressions\n'
