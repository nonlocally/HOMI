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
# String scans only — a transcript line is attacker-influencable text
# (fleet mail is delivered verbatim into turns), and a backtracking regex
# over megabyte lines measurably froze the cache lock. Every extraction
# below is a bounded find().
_XS_OPEN = "<cross-session-message"
_XS_CLOSE = "</cross-session-message>"


# Harness packaging around a delivered message: the preamble line above the
# wrapper and the stapled-on advisory after it. Matching is by marker, and
# the failure direction is deliberate: unrecognized packaging RENDERS (noise
# you can see) — it is never silently eaten.
_XS_PREAMBLES = ("Another Claude session sent a message:",
                 "[Cross-session delivery notice]")
_XS_BOIL_START = "This came from another Claude session"
_XS_BOIL_END = "permission laundering."


def _xsession_turns(text, ts):
    """Turns for a user record containing a cross-session wrapper ANYWHERE
    (the harness prepends a preamble line, so start-anchored matching let
    raw envelopes render whole). Returns None when no wrapper; [] for a
    hostile/unparseable wrapper (machine noise, skipped); else 1-2 turns:
    the delivered message under its sender's byline, plus any text the
    operator themselves typed after the packaging. An attacker-length
    from-name DEGRADES (truncate / 'unknown sender'), never renders as
    'you'."""
    idx = text.find(_XS_OPEN)
    if idx < 0:
        return None
    gt = text.find(">", idx, idx + 4096)
    if gt < 0:
        # No honest wrapper has a 4 KB opener tag: machine noise, whole
        # turn skipped (the old fallback leaked raw markup as "you").
        return []
    head = text[idx:gt]
    who = "unknown sender"
    i = head.find('from-name="')
    if i >= 0:
        j = head.find('"', i + 11)
        if j > i + 11:
            name = head[i + 11:j]
            who = name[:120] + ("…" if len(name) > 120 else "")
    j = text.find(_XS_CLOSE, gt)
    inner = text[gt + 1:j] if j > gt else text[gt + 1:]
    post = text[j + len(_XS_CLOSE):] if j > gt else ""
    out = []
    pre = text[:idx].strip()
    if pre and not pre.startswith(_XS_PREAMBLES):
        # Unknown pre-text: keep it visible rather than guess.
        out.append({"role": "user", "ts": ts, "text": pre})
    inner = inner.strip()
    if inner:
        out.append({"role": "user", "ts": ts, "text": inner, "who": who})
    b = post.find(_XS_BOIL_START)
    if b >= 0:
        e = post.find(_XS_BOIL_END, b)
        post = (post[:b] + post[e + len(_XS_BOIL_END):]) if e >= 0 \
            else post[:b]
    post = post.strip()
    if post:
        out.append({"role": "user", "ts": ts, "text": post})
    return out


def _strip_spans(text, open_tag="<system-reminder>",
                 close_tag="</system-reminder>"):
    """Remove embedded machine-note spans from an otherwise-human turn.
    An unclosed span drops the tail (it is machine noise, not prose)."""
    out, i = [], 0
    while True:
        j = text.find(open_tag, i)
        if j < 0:
            out.append(text[i:])
            break
        out.append(text[i:j])
        k = text.find(close_tag, j)
        if k < 0:
            break
        i = k + len(close_tag)
    return "".join(out)


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
    cands.sort(key=lambda dd: (dd.get("kind") == "interactive",
                               dd.get("startedAt") or 0), reverse=True)
    if len(cands) > 1:
        # Mirror the daemon's chooser EXACTLY (Homi._choose_session): on a
        # collision the probe-live socket wins, so the tab shows the same
        # session your messages reach — a file that exists is not a listener.
        try:
            from homi import probe
        except ImportError:
            probe = None
        if probe is not None:
            live = [d for d in cands
                    if probe(d.get("messagingSocketPath") or "") == "live"]
            if live:
                return live[0]
    return cands[0] if cands else None


_SID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


def _pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def _find_session_dead(name):
    """All REGISTERED sessions for `name`, newest first, regardless of
    process state — a sleeping agent still deserves to show its last
    working life, and the caller walks the list until a transcript exists."""
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
        if d.get("name") != name or not d.get("sessionId"):
            continue
        cands.append(d)
    cands.sort(key=lambda dd: dd.get("startedAt") or 0, reverse=True)
    return cands


def find_transcript(name):
    """(transcript_path, session_record) — the LIVE session when one exists,
    else the newest dead one THAT HAS a transcript on disk, else None."""
    live = find_session(name)
    cands = [live] if live else _find_session_dead(name)
    root = os.path.realpath(projects_dir())
    for sess in cands:
        sid = sess["sessionId"]
        # The registry is local trusted state, but a session id feeds a
        # glob — refuse shapes that could ever mean anything to the
        # filesystem, and verify containment on what the glob returned.
        if not _SID_RE.match(sid):
            continue
        hits = []
        for p in glob.glob(os.path.join(projects_dir(), "*", sid + ".jsonl")):
            try:
                rp = os.path.realpath(p)
                if not rp.startswith(root + os.sep):
                    continue
                hits.append((os.path.getmtime(p), p))
            except OSError:
                continue   # vanished between glob and stat
        if hits:
            # A session id is unique; multiple hits are copies — newest wins.
            hits.sort(reverse=True)
            return hits[0][1], sess
    return None


def _iso_ts(rec):
    t = rec.get("timestamp")
    if not isinstance(t, str):
        return None
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(t.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)   # naive stamps are UTC
        return dt.timestamp()
    except ValueError:
        return None


def _tool_line(block):
    name = block.get("name") or "tool"
    inp = block.get("input")
    if not isinstance(inp, dict):
        inp = {}
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
    msg = rec.get("message")
    if not isinstance(msg, dict):
        return []
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
        xs = _xsession_turns(text, ts)
        if xs is not None:
            return xs
        text = _strip_spans(text).strip()
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
            name = b.get("name") or ""
            inp = b.get("input")
            # A fabric send carries prose and an address: surface both, so
            # the timeline can render replies-to-the-viewer as first-class
            # bubbles (and drop them when mail is authoritative).
            if ((name == "send" or name.endswith("__send"))
                    and isinstance(inp, dict)
                    and isinstance(inp.get("to"), str)
                    and isinstance(inp.get("text"), str)):
                out.append({"role": "reply", "ts": ts,
                            "to": inp["to"], "text": inp["text"]})
            else:
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
                st = os.stat(path)
                f = open(path, "rb")
            except OSError:
                self.by_path.pop(path, None)
                return None
            with f:
                head = f.read(256)
                # Validity is measured, not assumed: a shrink, a different
                # inode, or a different first 256 bytes all mean the path was
                # REPLACED (beam copies transcripts onto these names) — a
                # longer replacement is otherwise indistinguishable from an
                # append and got spliced onto stale turns.
                fresh = (ent is None or st.st_size < ent["size"]
                         or st.st_ino != ent["ino"] or head != ent["head"])
                if not fresh and ent["offset"] > 0 and ent.get("mark"):
                    # Same-inode in-place divergence (cp onto the dest,
                    # rsync --inplace) can keep inode AND head while the
                    # body changed: re-check the bytes just before our
                    # offset — stale turns must never splice onto new tail.
                    f.seek(ent["offset"] - len(ent["mark"]))
                    if f.read(len(ent["mark"])) != ent["mark"]:
                        fresh = True
                if fresh:
                    ent = {"size": 0, "offset": 0, "base": 0, "turns": [],
                           "ino": st.st_ino, "head": head, "mark": b""}
                    self.by_path[path] = ent
                if st.st_size > ent["offset"]:
                    f.seek(ent["offset"])
                    chunk = f.read()
                    last_nl = chunk.rfind(b"\n")
                    if last_nl < 0:
                        ent["size"] = st.st_size
                        return ent
                    for line in chunk[:last_nl].split(b"\n"):
                        if not line.strip():
                            continue
                        # One bad record must never poison the cache: parse
                        # errors skip the LINE, the offset still advances,
                        # and no exception escapes with turns half-applied.
                        try:
                            rec = json.loads(line.decode("utf-8", "replace"))
                            new = _parse_record(rec) if isinstance(rec, dict) \
                                else []
                        except Exception:
                            continue
                        ent["turns"].extend(new)
                    consumed = chunk[:last_nl + 1]
                    ent["mark"] = (ent["mark"] + consumed)[-64:]
                    ent["offset"] += last_nl + 1
                    ent["size"] = st.st_size
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
        return {"ok": True, "live": False, "present": False, "turns": [],
                "total": 0, "truncated": False}
    path, sess = found
    # Live means DELIVERABLE: process alive AND its socket file present —
    # a pid with no socket queues mail like any sleeper and must say so.
    live = (_pid_alive(sess.get("pid"))
            and os.path.exists(sess.get("messagingSocketPath") or ""))
    ent = _cache.turns(path)
    if ent is None:
        return {"ok": True, "live": False, "present": False, "turns": [],
                "total": 0, "truncated": False}
    base, turns = ent["base"], ent["turns"]
    total = base + len(turns)
    if after is not None:
        hi = len(turns)
        lo = min(max(int(after) + 1 - base, 0), hi)
        # A stale/negative cursor must not dump the whole window: bound the
        # answer; the client renders a gap mark when indices jump.
        lo = max(lo, hi - 500)
    elif before is not None:
        hi = min(max(int(before) - base, 0), len(turns))
        lo = max(hi - n, 0)
    else:
        hi = len(turns)
        lo = max(hi - n, 0)
    out = []
    for i in range(lo, hi):
        t = {"i": base + i, "role": turns[i]["role"], "ts": turns[i]["ts"],
             "text": turns[i]["text"]}
        if turns[i].get("who"):
            t["who"] = turns[i]["who"]
        if turns[i].get("to"):
            t["to"] = turns[i]["to"]
        out.append(t)
    return {"ok": True, "live": live, "present": True, "turns": out,
            "total": total, "truncated": base > 0,
            "sid": sess.get("sessionId") or ""}
