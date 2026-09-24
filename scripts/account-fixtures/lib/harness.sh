# shellcheck shell=bash
# ==============================================================================
# Tiny zero-dependency bash test harness for anu.
# ==============================================================================
# Why hand-rolled instead of bats: anu installs onto bare machines with nothing
# but bash + coreutils. A test suite that needs `brew install bats` first would
# be the kind of friction the project exists to remove. This is ~80 lines, runs
# anywhere bash 4+ runs, and prints a readable per-assertion trace.
#
# A test file sources this, makes assertions, and ends with `t_done`. Each
# assertion prints a ✓/✗ line and bumps a counter; `t_done` exits nonzero iff
# anything failed, so the runner can aggregate by exit status alone.
#
#   source "$(dirname "${BASH_SOURCE[0]}")/../lib/harness.sh"
#   t_suite "swarm"
#   assert_eq "★" "$(_swarm_topo_icon star)" "star icon"
#   t_done
#
# No `set -e`/`set -u`: the functions under test are written for interactive
# shells and routinely read unset vars ($TMUX, $ANTHROPIC_API_KEY) and return
# nonzero by design. Assertions capture status explicitly instead.

_T_PASS=0
_T_FAIL=0
_T_SUITE="${0##*/}"
declare -a _T_FAILURES=()

if [[ -t 1 ]]; then
  _T_GREEN=$'\033[32m'; _T_RED=$'\033[31m'; _T_DIM=$'\033[2m'
  _T_BOLD=$'\033[1m'; _T_RESET=$'\033[0m'
else
  _T_GREEN=''; _T_RED=''; _T_DIM=''; _T_BOLD=''; _T_RESET=''
fi

t_suite() { _T_SUITE="$1"; printf '%s── %s ──%s\n' "$_T_BOLD" "$1" "$_T_RESET"; }

# A free-text section header, purely for readable output grouping.
t_section() { printf '%s  %s%s\n' "$_T_DIM" "$1" "$_T_RESET"; }

_t_pass() { _T_PASS=$((_T_PASS + 1)); printf '  %s✓%s %s\n' "$_T_GREEN" "$_T_RESET" "$1"; }
_t_fail() {
  _T_FAIL=$((_T_FAIL + 1))
  _T_FAILURES+=("$1")
  printf '  %s✗ %s%s\n' "$_T_RED" "$1" "$_T_RESET"
  local detail
  for detail in "${@:2}"; do printf '      %s%s%s\n' "$_T_DIM" "$detail" "$_T_RESET"; done
}

# assert_eq EXPECTED ACTUAL [LABEL]
assert_eq() {
  local exp="$1" act="$2" label="${3:-equals}"
  if [[ "$exp" == "$act" ]]; then _t_pass "$label"
  else _t_fail "$label" "expected: [$exp]" "actual:   [$act]"; fi
}

# assert_ne UNEXPECTED ACTUAL [LABEL]
assert_ne() {
  local nexp="$1" act="$2" label="${3:-differs}"
  if [[ "$nexp" != "$act" ]]; then _t_pass "$label"
  else _t_fail "$label" "did not expect: [$nexp]"; fi
}

# assert_contains HAYSTACK NEEDLE [LABEL]
assert_contains() {
  local hay="$1" needle="$2" label="${3:-contains '$2'}"
  if [[ "$hay" == *"$needle"* ]]; then _t_pass "$label"
  else _t_fail "$label" "needle:   [$needle]" "haystack: [$hay]"; fi
}

# assert_not_contains HAYSTACK NEEDLE [LABEL]
assert_not_contains() {
  local hay="$1" needle="$2" label="${3:-excludes '$2'}"
  if [[ "$hay" != *"$needle"* ]]; then _t_pass "$label"
  else _t_fail "$label" "unexpected needle: [$needle]" "in: [$hay]"; fi
}

# assert_match STRING ERE [LABEL]
assert_match() {
  local str="$1" re="$2" label="${3:-matches /$2/}"
  if [[ "$str" =~ $re ]]; then _t_pass "$label"
  else _t_fail "$label" "regex:  [$re]" "string: [$str]"; fi
}

# assert_ok RC [LABEL]   — RC should be 0
assert_ok() {
  local rc="$1" label="${2:-exit 0}"
  if [[ "$rc" -eq 0 ]]; then _t_pass "$label"
  else _t_fail "$label" "expected exit 0, got $rc"; fi
}

# assert_fail RC [LABEL]  — RC should be nonzero
assert_fail() {
  local rc="$1" label="${2:-nonzero exit}"
  if [[ "$rc" -ne 0 ]]; then _t_pass "$label"
  else _t_fail "$label" "expected nonzero exit, got 0"; fi
}

# assert_file PATH [LABEL]
assert_file() {
  local path="$1" label="${2:-file exists: $1}"
  if [[ -f "$path" ]]; then _t_pass "$label"
  else _t_fail "$label" "missing file: $path"; fi
}

# assert_jq FILE FILTER EXPECTED [LABEL] — compares jq -r output to EXPECTED
assert_jq() {
  local file="$1" filter="$2" exp="$3" label="${4:-jq $2 == $3}"
  local act
  act=$(jq -r "$filter" "$file" 2>/dev/null)
  if [[ "$act" == "$exp" ]]; then _t_pass "$label"
  else _t_fail "$label" "jq:       $filter" "expected: [$exp]" "actual:   [$act]"; fi
}

# Mark a deliberate, unconditional failure (e.g. an unreachable branch).
fail() { _t_fail "${1:-explicit failure}" "${@:2}"; }

t_done() {
  printf '%s──%s %s: %s%d passed%s' "$_T_DIM" "$_T_RESET" "$_T_SUITE" "$_T_GREEN" "$_T_PASS" "$_T_RESET"
  if [[ "$_T_FAIL" -gt 0 ]]; then
    printf ', %s%d failed%s\n' "$_T_RED" "$_T_FAIL" "$_T_RESET"
    exit 1
  fi
  printf '\n'
  exit 0
}
