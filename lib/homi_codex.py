"""Codex session view: render a codex agent's rollout into the SAME turn
shape as the Claude adapter, so a codex-bound seat gets the full session view
on the talk page (not just the messages plane).

Codex writes per-session rollout JSONL under $CODEX_HOME/sessions/YYYY/MM/DD/
rollout-<ts>-<id>.jsonl. Typed records: response_item(message) is the
conversation (role user/assistant kept; developer/system/skill prompts are
plumbing), custom_tool_call / function_call / *_shell_call are tool receipts,
reasoning and *_output are machine plumbing, compacted is a divider.

The pane -> rollout link is the ACTUAL open file descriptor of the pane's
codex process (lsof) — never a name or cwd guess. A codex process holds
several rollouts open when it spawns sub-agents, so among its open rollouts
the pane's MAIN session is the earliest-started one in the pane's cwd
(sub-agents spawn later). Parsing, caching, and windowing are reused from
homi_transcript so the two adapters page and poll identically.
"""
import glob
import json
import os
import subprocess
import time

import homi_transcript as _ht


def codex_home():
    return os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")


def _iso(ts):
    if not isinstance(ts, str):
        return None
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return None


def _text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text") or "" for b in content
                         if isinstance(b, dict) and (b.get("type") in
                                                     (None, "text", "input_text",
                                                      "output_text")))
    return ""


_TOOL_KINDS = ("custom_tool_call", "function_call", "local_shell_call",
               "shell_call", "tool_call", "exec_command")
_SHELLY = ("shell", "local_shell", "bash", "exec", "exec_command")


def _tool_line(p):
    name = p.get("name") or p.get("type") or "tool"
    inp = p.get("input")
    if isinstance(inp, str):
        try:
            inp = json.loads(inp)
        except ValueError:
            inp = {"_": inp}
    hint = ""
    if isinstance(inp, dict):
        for k in ("command", "cmd", "path", "file_path", "file", "query",
                  "description"):
            v = inp.get(k)
            if isinstance(v, list) and v:
                v = " ".join(str(x) for x in v)
            if isinstance(v, str) and v:
                hint = v.strip()[:80]
                break
    if name in _SHELLY:
        return "$ " + (hint or name)
    return "⟶ " + name + ((" " + hint) if hint else "")


def _parse_record(rec):
    """One codex rollout record -> renderable turns (0/1). Same output shape
    as homi_transcript._parse_record."""
    kind = rec.get("type")
    if kind == "compacted":
        return [{"role": "mark", "ts": None, "text": "· context compacted ·"}]
    if kind != "response_item":
        return []   # session_meta, event_msg, world_state, turn_context: plumbing
    p = rec.get("payload")
    if not isinstance(p, dict):
        return []
    ts = _iso(rec.get("timestamp"))
    pt = p.get("type")
    if pt == "message":
        role = p.get("role")
        if role not in ("user", "assistant"):
            return []   # developer / system / skill instructions: plumbing
        text = _text_of(p.get("content")).strip()
        if not text:
            return []
        return [{"role": role, "ts": ts, "text": text}]
    if pt in _TOOL_KINDS:
        return [{"role": "tool", "ts": ts, "text": _tool_line(p)}]
    return []   # reasoning, *_output, and anything else: plumbing


_cache = _ht._Cache(_parse_record)


def _session_meta(path):
    """(session_id, cwd, start_ts) from a rollout's first session_meta —
    cheap (reads only the head of the file)."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for _ in range(8):
                line = f.readline()
                if not line:
                    break
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get("type") == "session_meta":
                    pl = d.get("payload") or {}
                    return (pl.get("session_id"), pl.get("cwd"),
                            _iso(d.get("timestamp")))
    except OSError:
        pass
    return (None, None, None)


def _pick_main(cands, cwd):
    """The pane's MAIN session among the rollouts its codex process holds
    open. cands = [(path, start_ts, cwd)]. Prefer the earliest-started one
    whose cwd matches the pane's; sub-agents spawn later. Falls back to the
    earliest overall if none match the cwd."""
    if not cands:
        return None
    same = [c for c in cands if cwd and c[2] == cwd]
    pool = same or cands
    pool = sorted(pool, key=lambda c: (c[1] if c[1] is not None else 1e18))
    return pool[0][0]


def _pane_pid(seat):
    tmux = os.environ.get("HOMI_TMUX_BIN", "tmux")
    sock = os.environ.get("HOMI_TMUX_SOCKET")
    base = [tmux] + (["-L", sock] if sock else [])
    try:
        r = subprocess.run(base + ["display", "-t", seat, "-p", "#{pane_pid}"],
                           capture_output=True, text=True, timeout=6)
        return r.stdout.strip() or None
    except Exception:
        return None


def _descendants(pid):
    out = [pid]
    try:
        kids = subprocess.run(["pgrep", "-P", pid], capture_output=True,
                              text=True, timeout=6).stdout.split()
    except Exception:
        kids = []
    for k in kids:
        out += _descendants(k)
    return out


def _open_rollouts(pid):
    try:
        ls = subprocess.run(["lsof", "-p", pid], capture_output=True,
                            text=True, timeout=15).stdout
    except Exception:
        return []
    paths = []
    for line in ls.splitlines():
        i = line.find("/")
        if i < 0:
            continue
        path = line[i:].strip()
        if "rollout-" in path and path.endswith(".jsonl"):
            paths.append(path)
    return paths


_rollout_cache = {}   # seat -> {"at": ts, "path": path}
_ROLLOUT_TTL = 20.0


def rollout_for_seat(seat):
    """Resolve a seat to its codex MAIN-session rollout via the open fd of
    the pane's codex process. Cached briefly (lsof is not free), so it also
    auto-follows a codex restart within the TTL. None when the pane isn't a
    codex agent (no open rollout)."""
    now = time.time()
    ent = _rollout_cache.get(seat)
    if ent and now - ent["at"] < _ROLLOUT_TTL:
        return ent["path"]
    path = None
    pid = _pane_pid(seat)
    if pid:
        pane_cwd = None
        seen = set()
        cands = []
        for sub in _descendants(pid):
            for rp in _open_rollouts(sub):
                if rp in seen:
                    continue
                seen.add(rp)
                sid, cwd, start = _session_meta(rp)
                cands.append((rp, start, cwd))
        # the pane cwd: any candidate's cwd is close enough, but prefer the
        # tmux-reported one when available
        path = _pick_main(cands, _pane_cwd(seat))
    _rollout_cache[seat] = {"at": now, "path": path}
    return path


def _pane_cwd(seat):
    tmux = os.environ.get("HOMI_TMUX_BIN", "tmux")
    sock = os.environ.get("HOMI_TMUX_SOCKET")
    base = [tmux] + (["-L", sock] if sock else [])
    try:
        r = subprocess.run(base + ["display", "-t", seat, "-p",
                                   "#{pane_current_path}"],
                           capture_output=True, text=True, timeout=6)
        return r.stdout.strip() or None
    except Exception:
        return None


def turns_for(seat, after=None, before=None, n=None, _rollout=False):
    """The codex session view for a bound seat, in the Claude adapter's exact
    return shape. `_rollout` is a test/override seam: False resolves via
    lsof; None forces the no-rollout path; a path uses it directly."""
    path = rollout_for_seat(seat) if _rollout is False else _rollout
    if not path:
        return {"ok": True, "live": False, "present": False, "turns": [],
                "total": 0, "truncated": False}
    ent = _cache.turns(path)
    if ent is None:
        return {"ok": True, "live": False, "present": False, "turns": [],
                "total": 0, "truncated": False}
    sid, _cwd, _start = _session_meta(path)
    out, total, truncated = _ht.window(ent, after, before,
                                       n or _ht.DEFAULT_N)
    return {"ok": True, "live": True, "present": True, "turns": out,
            "total": total, "truncated": truncated, "sid": sid or ""}
