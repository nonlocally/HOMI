"""homi session view: read-only transcript turns for a live agent session.

The talk page's second tab. An agent's NAME resolves to a live Claude
session the same way the daemon's delivery scan does it (session registry
file: name match, pid alive, socket file present; newest startedAt wins a
collision), then to that session's transcript by session-id glob — never by
munging cwd, which drifts when a session changes directory. Records are
parsed into renderable turns: the human's words and the agent's prose stay,
tool calls become one-line receipts, compactions become dividers, and
machine plumbing (thinking, tool results, system reminders, injected
command records) is dropped. Files-as-API: no daemon involvement, read-only.

Scale honestly: transcripts are megabytes and only ever grow, so each is
parsed once and appended incrementally (partial trailing lines are left for
the next read). At most CAP renderable turns are held per transcript; a
request that pages below the cap says truncated=True rather than
pretending the history starts there.
"""
import glob
import json
import os
import re
import threading

CAP = 1000          # renderable turns held per transcript
DEFAULT_N = 150     # turns served when the client names no window

# Machine-plumbing prefixes: user records that are injections, not the
# human speaking. A cross-session message IS correspondence — its inner
# text is extracted below, before this filter sees it.
_SKIP_PREFIXES = (
    "<system-reminder>", "<command-name>", "<local-command-stdout>",
    "[SYSTEM NOTIFICATION", "<task-notification>",
)
_XSESSION_RE = re.compile(
    r"<cross-session-message[^>]*>\n?(.*?)\n?</cross-session-message>",
    re.S)


def sessions_dir():
    d = os.environ.get("HOMI_SESSIONS_DIR")
    if d:
        return d
    return os.path.join(os.environ.get("CLAUDE_CONFIG_DIR")
                        or os.path.expanduser("~/.claude"), "sessions")


def projects_dir():
    return os.path.join(os.environ.get("CLAUDE_CONFIG_DIR")
                        or os.path.expanduser("~/.claude"), "projects")


def find_session(name):
    """The live session registered under `name`, or None. Mirrors the
    daemon's sidecar scan: real record, pid alive, socket file present;
    deterministic winner on collision (interactive first, then newest)."""
    cands = []
    try:
        files = os.listdir(sessions_dir())
    except OSError:
        return None
    for fn in files:
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(sessions_dir(), fn)) as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(d, dict) or d.get("version") == "communicate-homi":
            continue
        if d.get("name") != name:
            continue
        pid, sock = d.get("pid"), d.get("messagingSocketPath")
        if not pid or not sock or not d.get("sessionId"):
            continue
        try:
            os.kill(int(pid), 0)
        except (OSError, ValueError):
            continue
        if not os.path.exists(sock):
            continue
        cands.append(d)
    cands.sort(key=lambda d: (d.get("kind") == "interactive",
                              d.get("startedAt") or 0), reverse=True)
    return cands[0] if cands else None


def find_transcript(name):
    """(transcript_path, session_record) for a live session, else None."""
    sess = find_session(name)
    if not sess:
        return None
    sid = sess["sessionId"]
    hits = glob.glob(os.path.join(projects_dir(), "*", sid + ".jsonl"))
    if not hits:
        return None
    # A session id is unique; multiple hits would be copies — newest wins.
    hits.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return hits[0], sess


def _iso_ts(rec):
    t = rec.get("timestamp")
    if not isinstance(t, str):
        return None
    try:
        from datetime import datetime
        return datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _tool_line(block):
    name = block.get("name") or "tool"
    inp = block.get("input") or {}
    if name == "Bash":
        hint = inp.get("description") or (inp.get("command") or "")[:80]
        return "$ " + hint
    if name in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        return "✎ " + os.path.basename(inp.get("file_path") or "?")
    hint = ""
    for k in ("description", "file_path", "pattern", "skill", "to", "name"):
        v = inp.get(k)
        if isinstance(v, str) and v:
            hint = " " + (os.path.basename(v) if k == "file_path" else v)[:60]
            break
    return "⟶ " + name + hint


def _parse_record(rec):
    """One raw transcript record -> list of renderable turns (often 0/1)."""
    kind = rec.get("type")
    if kind == "summary":
        return [{"role": "mark", "ts": None, "text": "· context compacted ·"}]
    if kind not in ("user", "assistant"):
        return []
    msg = rec.get("message") or {}
    content = msg.get("content")
    ts = _iso_ts(rec)
    if kind == "user":
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = "\n".join(b.get("text") or "" for b in content
                             if isinstance(b, dict) and b.get("type") == "text")
        else:
            return []
        m = _XSESSION_RE.search(text)
        if m:
            text = m.group(1)
        text = text.strip()
        if not text or text.startswith(_SKIP_PREFIXES):
            return []
        return [{"role": "user", "ts": ts, "text": text}]
    out, buf = [], []
    if not isinstance(content, list):
        return []
    for b in content:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "text" and (b.get("text") or "").strip():
            buf.append(b["text"])
        elif b.get("type") == "tool_use":
            if buf:
                out.append({"role": "assistant", "ts": ts,
                            "text": "\n".join(buf).strip()})
                buf = []
            out.append({"role": "tool", "ts": ts, "text": _tool_line(b)})
    if buf:
        out.append({"role": "assistant", "ts": ts, "text": "\n".join(buf).strip()})
    return out


class _Cache:
    """Per-transcript incremental parse. The file only grows; a shrink
    means replacement and forces a full reparse. Only complete lines are
    consumed — a partial trailing line stays for the next read."""

    def __init__(self):
        self.mu = threading.Lock()
        self.by_path = {}   # path -> {size, offset, base, turns}

    def turns(self, path):
        with self.mu:
            ent = self.by_path.get(path)
            try:
                size = os.path.getsize(path)
            except OSError:
                self.by_path.pop(path, None)
                return None
            if ent is None or size < ent["size"]:
                ent = {"size": 0, "offset": 0, "base": 0, "turns": []}
                self.by_path[path] = ent
            if size > ent["offset"]:
                with open(path, "rb") as f:
                    f.seek(ent["offset"])
                    chunk = f.read()
                last_nl = chunk.rfind(b"\n")
                if last_nl < 0:
                    ent["size"] = size
                    return ent
                for line in chunk[:last_nl].split(b"\n"):
                    if not line.strip():
                        continue
                    try:
                        rec = json.loads(line.decode("utf-8", "replace"))
                    except ValueError:
                        continue
                    ent["turns"].extend(_parse_record(rec))
                ent["offset"] += last_nl + 1
                ent["size"] = size
                drop = len(ent["turns"]) - CAP
                if drop > 0:
                    del ent["turns"][:drop]
                    ent["base"] += drop
            return ent


_cache = _Cache()


def turns_for(name, after=None, before=None, n=DEFAULT_N):
    """The API response for /api/session/<name>. after=i -> turns newer
    than absolute index i (polling). before=i -> the n turns just below i
    (paging back). Neither -> the newest n."""
    n = max(1, min(int(n or DEFAULT_N), 500))
    found = find_transcript(name)
    if not found:
        return {"ok": True, "live": False, "turns": [], "total": 0,
                "truncated": False}
    path, sess = found
    ent = _cache.turns(path)
    if ent is None:
        return {"ok": True, "live": False, "turns": [], "total": 0,
                "truncated": False}
    base, turns = ent["base"], ent["turns"]
    total = base + len(turns)
    if after is not None:
        lo, hi = min(max(int(after) + 1 - base, 0), len(turns)), len(turns)
    elif before is not None:
        hi = min(max(int(before) - base, 0), len(turns))
        lo = max(hi - n, 0)
    else:
        hi = len(turns)
        lo = max(hi - n, 0)
    out = [{"i": base + i, "role": turns[i]["role"], "ts": turns[i]["ts"],
            "text": turns[i]["text"]} for i in range(lo, hi)]
    return {"ok": True, "live": True, "turns": out, "total": total,
            "truncated": base > 0, "cwd": sess.get("cwd") or ""}
