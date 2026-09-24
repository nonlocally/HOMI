#!/usr/bin/env bash
# Native authentication by default. Optional private adapters are executable paths,
# not strings evaluated by a shell. The account adapter accepts anu-account's argv.
set -e
kind="${1:-claude}"
[ $# -eq 0 ] || shift
provider=claude
boxed=0
case "$kind" in
  claude) ;;
  cx) set -- --allow-dangerously-skip-permissions "$@" ;;
  cxx) set -- --dangerously-skip-permissions "$@" ;;
  cxc) boxed=1; set -- --dangerously-skip-permissions "$@" ;;
  cdx|codex) provider=codex ;;
  cdxx) provider=codex; set -- --yolo "$@" ;;
  cdxxs) provider=codex; set -- --sandbox workspace-write --ask-for-approval never "$@" ;;
  c|opencode) exec opencode "$@" ;;
  pi) exec pi "$@" ;;
  *) printf 'homi-agent: unknown alias: %s\n' "$kind" >&2; exit 2 ;;
esac
if [ -n "${HOMI_ACCOUNT_LAUNCHER:-}" ]; then
  [ -x "$HOMI_ACCOUNT_LAUNCHER" ] || { echo 'homi-agent: configured account launcher is not executable' >&2; exit 1; }
  if [ "$boxed" = 1 ]; then
    exec "$HOMI_ACCOUNT_LAUNCHER" launch --provider "$provider" --box -- "$@"
  fi
  exec "$HOMI_ACCOUNT_LAUNCHER" launch --provider "$provider" -- "$@"
fi
if [ "$boxed" = 1 ]; then
  [ -n "${HOMI_BOX_LAUNCHER:-}" ] && [ -x "$HOMI_BOX_LAUNCHER" ] || {
    echo 'homi-agent: cxc needs HOMI_BOX_LAUNCHER or a configured account launcher; no uncontained fallback' >&2
    exit 1
  }
  exec "$HOMI_BOX_LAUNCHER" claude "$@"
fi
exec "$provider" "$@"
