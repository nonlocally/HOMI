"""homi talk — converse with agents over the fabric's own planes.

The human is a claimed identity (the handle): sends go out as one daemon
`send` with from=<handle> (honest attribution, like every agent), replies
land in the HUMAN's durable mailbox, and the phone chat is a merged view of
[what you sent] (a server-side journal) and [what they sent you] (your
inbox, filtered by correspondent). The conversation IS fabric state — it
works while agents sleep, because store→wake is the whole point.

Serving/auth live in homi_board (one server); this module owns conversation
state and the chat page. Stdlib only; no daemon ops added.
"""
import json
import os
import time

import homi  # fully loaded before this module (lazy import in the server)


def human_handle():
    """The claimed handle, or None (talk refuses on an unclaimed fabric —
    a conversation needs an honest from-address)."""
    try:
        u = homi._call({"op": "user"})
    except (Exception, SystemExit):
        return None
    return (u.get("user") or {}).get("handle")


def ensure_human(handle):
    """Claim the handle as a local identity (idempotent): its mailbox is
    where agent replies land, its sidecar is the reply address live
    sessions see."""
    try:
        homi._call({"op": "claim", "name": handle})
        return True
    except (Exception, SystemExit):
        return False


def valid_target(target):
    """<name> or <name>@<device> — the daemon's own grammars, so nothing
    filename- or path-shaped sneaks in. A trailing '@' (empty suffix) is
    rejected: it would render a working page that can never send."""
    base, sep, qual = (target or "").partition("@")
    if not homi.Homi._NAME_RE.match(base):
        return False
    if not sep:
        return True
    return bool(homi.Homi._DEV_RE.match(qual))


def _matches(m, base, qual):
    """Does an inbox arrival belong to THIS conversation? The daemon stamps
    from_name/via distinctly per plane, and getting this wrong LEAKS a granted
    fleet peer's messages into a local thread:
      - local agent → me:      from_name='scout',      via absent
      - my device's agent:     from_name='scout',      via='<device>'   (mail)
      - fleet peer's agent:    from_name='scout@peer',  via='peer'       (mail)
    A BARE target is a LOCAL agent ONLY: exact name, no '@', not arrived over
    any link. A fleet-qualified from_name or any via-stamped arrival never
    matches it — that was the confirmed spoof (scout@peer landing in scout)."""
    fn = m.get("from_name") or ""
    via = m.get("via")
    if not qual:
        return fn == base and via is None
    return fn == (base + "@" + qual) or (fn.partition("@")[0] == base and via == qual)


def _talk_dir():
    import homi_board
    return os.path.join(homi_board.board_dir(), "talk")


def _journal_path(target):
    return os.path.join(_talk_dir(), target.replace("@", "+") + ".jsonl")


def ensure_talk_dir():
    homi.ensure_dir_0700(_talk_dir())


def journal_append(target, entry):
    # One atomic O_APPEND write: a buffered text write of a >8KiB line becomes
    # multiple write(2) calls that two request threads can interleave, and the
    # reader silently drops the torn JSON. Open 0600 from birth (no
    # world-readable window). The dir is ensured once at serve start.
    p = _journal_path(target)
    blob = (json.dumps(entry) + "\n").encode("utf-8")
    fd = os.open(p, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, blob)
    finally:
        os.close(fd)


class SendRefused(ValueError):
    """The daemon refused the send (unknown target, ungranted, …) — a CLIENT
    error carrying the daemon's honest reason, not a server fault."""


def send(handle, target, text):
    """One daemon send + a journal line. Returns the daemon's honest routed
    verdict (live / inbox / link:<dev>) or raises SendRefused on refusal."""
    r = homi._call({"op": "send", "to": target, "text": text, "from": handle})
    if not r.get("ok"):
        raise SendRefused(r.get("err") or "send failed")
    entry = {"ts": time.time(), "dir": "out", "text": text,
             "routed": r.get("routed")}
    journal_append(target, entry)
    return entry


def conversation(handle, target, since=0.0, inbox=None):
    """Merged, time-ordered view: journal (out) + the human's inbox filtered
    by correspondent (in). `inbox` is an optional cached reader (name -> the
    inbox op result) so many pollers share one mailbox read."""
    base, _, qual = target.partition("@")
    entries = []
    try:
        with open(_journal_path(target)) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("ts", 0) > since:
                    entries.append(e)
    except OSError:
        pass
    try:
        r = inbox(handle) if inbox else homi._call(
            {"op": "inbox", "name": handle, "tail": 500})
    except (Exception, SystemExit):
        r = {}
    for m in (r.get("messages") or []):
        if not _matches(m, base, qual):
            continue
        ts = m.get("ts", 0)
        if ts > since:
            entries.append({"ts": ts, "dir": "in", "text": m.get("text", ""),
                            "from": m.get("from_name")})
    entries.sort(key=lambda e: e.get("ts", 0))
    return entries


_seat_cache = {"at": 0.0, "map": {}}


def _seat_for(name, inbox=None):
    """The seat a local identity is bound to (or None), from the daemon
    roster. Cached ~3s — the timeline polls every 2.5s and this only decides
    whether to consult the codex adapter."""
    now = time.time()
    if now - _seat_cache["at"] > 3.0:
        try:
            r = homi._call({"op": "agents"})
        except (Exception, SystemExit):
            r = {}
        rows = r.get("agents") if isinstance(r, dict) else r
        m = {}
        for a in (rows or []):
            if isinstance(a, dict) and a.get("name") and a.get("seat"):
                m[a["name"]] = a["seat"]
        _seat_cache["at"] = now
        _seat_cache["map"] = m
    return _seat_cache["map"].get(name)


def _session_source(base, t_after, t_before, n):
    """The session spine for the timeline: a live Claude transcript if there
    is one, else the codex rollout of a codex-bound seat, else the honest
    empty (messages-only). Both adapters return the same shape, so the caller
    treats the result uniformly."""
    import homi_transcript
    d = homi_transcript.turns_for(base, after=t_after, before=t_before, n=n)
    if d.get("present"):
        return d
    seat = _seat_for(base)
    if seat:
        try:
            import homi_codex
            cd = homi_codex.turns_for(seat, after=t_after, before=t_before, n=n)
            if cd.get("present"):
                return cd
        except Exception:
            pass
    return d


def timeline(handle, target, t_after=None, ts_after=0.0, t_before=None,
             n=150, inbox=None):
    """One merged view of an agent: its session is the spine; the MAIL
    thread is authoritative for correspondence with the viewer. The
    transcript's own copies of that correspondence — user turns attributed
    to the handle, send-replies addressed to it — are dropped
    UNCONDITIONALLY. That single rule kills every duplication case,
    including a queued message delivered later on wake (its transcript twin
    simply never renders). Mark turns carry no timestamp; they inherit the
    previous turn's so sorting never teleports them.

    Cursors are composite: t_after (transcript turn index) + ts_after (mail
    ts). t_before pages the transcript back and skips mail entirely (mail is
    small and fully loaded on the first fetch)."""
    import homi_transcript
    base, _, qual = target.partition("@")
    items, live, present, sid = [], False, False, ""
    t_cursor = -1 if t_after is None else int(t_after)
    t_min = None
    total = 0
    truncated = False
    # One read of each mail source serves both purposes: FULL history feeds
    # the twin sets (a twin can be far older than this poll's window), the
    # since-filter feeds the new items.
    journal, inmsgs = [], []
    if t_before is None or not qual:
        try:
            with open(_journal_path(target)) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue
                    journal.append(e)
        except OSError:
            pass
        try:
            r = inbox(handle) if inbox else homi._call(
                {"op": "inbox", "name": handle, "tail": 500})
        except (Exception, SystemExit):
            r = {}
        inmsgs = [m for m in (r.get("messages") or [])
                  if _matches(m, base, qual)]
    if not qual:
        d = _session_source(base, t_after, t_before, n)
        live, present = d.get("live", False), d.get("present", False)
        sid = d.get("sid") or ""
        total = d.get("total") or 0
        truncated = bool(d.get("truncated"))
        # Twin sets, consume-once, WINDOWED. A drop needs proof a mail copy
        # renders (sends made outside this page and refused sends have no
        # twin), and the window must encode delivery semantics: a
        # live-routed send delivered within seconds, so its twin can only
        # explain a turn within minutes — text-forever matching let any old
        # "hi" in the journal eat every future same-text send. A QUEUED
        # send legitimately delivers whenever the agent next wakes.
        out_twins = [{"text": e.get("text", ""), "ts": e.get("ts") or 0,
                      "routed": e.get("routed")} for e in journal
                     if e.get("dir") == "out"]
        in_twins = [{"text": m.get("text", ""), "ts": m.get("ts") or 0}
                    for m in inmsgs]

        def _consume(twins, text, tts, queued_any_later):
            for tw in twins:
                if tw["text"] != text:
                    continue
                if (queued_any_later and tw.get("routed") != "live"):
                    hit = tts >= tw["ts"] - 5
                else:
                    hit = abs(tts - tw["ts"]) <= 300
                if hit:
                    twins.remove(tw)
                    return True
            return False
        turns = d.get("turns") or []
        last_ts = next((t["ts"] for t in turns if t.get("ts")), 0.0)
        for t in turns:
            if t.get("ts"):
                last_ts = t["ts"]
            if t["i"] > t_cursor:
                t_cursor = t["i"]
            if t_min is None or t["i"] < t_min:
                t_min = t["i"]
            tts = t.get("ts") or last_ts
            if (t["role"] == "user" and t.get("who") == handle
                    and _consume(out_twins, t["text"], tts, True)):
                continue   # the mail thread renders this
            if (t["role"] == "reply" and t.get("to") == handle
                    and _consume(in_twins, t["text"], tts, False)):
                continue   # the mail thread renders this
            it = dict(t)
            it["via"] = "session"
            if not it.get("ts"):
                it["ts"] = last_ts
            if it["role"] == "reply" and it.get("to") != handle:
                it["text"] = ""   # third-party mail: address renders, not prose
            items.append(it)
    ts_cursor = ts_after
    if t_before is None:
        for e in journal:
            if e.get("ts", 0) > ts_after:
                items.append({"via": "mail", "role": "user",
                              "ts": e.get("ts") or 0,
                              "text": e.get("text", ""),
                              "routed": e.get("routed")})
                if e["ts"] > ts_cursor:
                    ts_cursor = e["ts"]
        for m in inmsgs:
            ts = m.get("ts", 0)
            if ts > ts_after:
                items.append({"via": "mail", "role": "in", "ts": ts,
                              "text": m.get("text", ""),
                              "who": m.get("from_name")})
                if ts > ts_cursor:
                    ts_cursor = ts
    items.sort(key=lambda i: i.get("ts") or 0)
    return {"ok": True, "live": live, "present": present, "sid": sid,
            "items": items, "t_cursor": t_cursor, "ts_cursor": ts_cursor,
            "t_min": t_min, "total": total, "truncated": truncated}


def render_talk(handle, target, token):
    """The chat page: fixed template, snapshot-free (it polls /api/conv),
    the mutation token injected exactly once."""
    boot = json.dumps({"handle": handle, "target": target, "token": token}
                      ).replace("<", "\\u003c")
    return TALK_TEMPLATE.replace("__BOOT__", boot, 1)


TALK_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="theme-color" content="#090909">
<link rel="manifest" href="/manifest.webmanifest">
<link rel="apple-touch-icon" href="/icon-192.png">
<meta name="mobile-web-app-capable" content="yes">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; manifest-src 'self'; worker-src 'self'; base-uri 'none'; form-action 'none'">
<meta name="referrer" content="no-referrer">
<title>talk</title>
<style>
  :root {
    color-scheme: dark;
    --plane: #090909; --surface: #131312; --surface-2: #1b1b19;
    --ink: #f6f5f1; --ink-2: #bab8b0; --muted: #898781;
    --grid: #262624; --baseline: #36352f;
    --border: rgba(255, 255, 255, 0.075);
    --status-good: #0ca30c; --status-warn: #fab219; --status-critical: #d03b3b;
    --font-sans: system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    --font-mono: ui-monospace, "SF Mono", SFMono-Regular, Menlo, Consolas, monospace;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  html { background: var(--plane); }
  body {
    background: var(--plane); color: var(--ink);
    font-family: var(--font-sans);
    font-size: 14px; line-height: 1.45; letter-spacing: -0.006em;
    -webkit-font-smoothing: antialiased;
    display: flex; flex-direction: column;
    height: 100vh;   /* fallback for browsers without dvh */
    height: 100dvh;
  }
  :focus-visible { outline: 2px solid var(--ink); outline-offset: 2px; }

  header {
    display: flex; align-items: center; gap: 10px;
    padding: 14px 16px 10px;
    border-bottom: 1px solid var(--grid);
    padding-top: calc(14px + env(safe-area-inset-top));
  }
  header a { color: var(--muted); text-decoration: none;
             font-family: var(--font-mono); font-size: 11px;
             letter-spacing: 0.04em; }
  header a:hover { color: var(--ink); }
  #dot { width: 8px; height: 8px; border-radius: 50%; flex: none;
         background: var(--baseline); }
  #dot.live { background: var(--status-good); }
  #dot.asleep { background: var(--status-warn); }
  header .who { font-size: 16px; font-weight: 500;
                letter-spacing: -0.02em; }
  header .tag { margin-left: auto; font-family: var(--font-mono);
                font-size: 10.5px; letter-spacing: 0.12em;
                text-transform: uppercase; color: var(--muted);
                font-variant-numeric: tabular-nums; }
  #filt { background: transparent; border: 1px solid var(--baseline);
          border-radius: 3px; color: var(--ink-2); padding: 3px 10px;
          font-family: var(--font-mono); font-size: 12px; cursor: pointer;
          transition: border-color 0.15s ease, color 0.15s ease; }
  #filt:hover { border-color: var(--ink); color: var(--ink); }
  #filt.on { border-color: var(--ink); color: var(--ink); }

  #note { display: flex; gap: 8px; align-items: center;
          padding: 8px 16px; color: var(--muted);
          font-family: var(--font-mono); font-size: 11px;
          letter-spacing: 0.06em; border-bottom: 1px solid var(--grid); }
  #note::before { content: ""; width: 7px; height: 7px; border-radius: 50%;
                  background: var(--baseline); flex: none; }
  #note.warn::before { background: var(--status-warn); }
  #note[hidden] { display: none; }

  #log {
    flex: 1; overflow-y: auto;
    padding: 14px 16px;
    display: flex; flex-direction: column; gap: 10px;
  }
  .msg { max-width: 86%; }
  .msg .meta { font-family: var(--font-mono); font-size: 10px;
               letter-spacing: 0.12em; text-transform: uppercase;
               color: var(--muted); margin-bottom: 4px;
               font-variant-numeric: tabular-nums; }
  .msg .body {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 7px;
    padding: 9px 12px;
    white-space: pre-wrap; word-break: break-word;
  }
  .msg.out { align-self: flex-end; }
  .msg.out .meta { text-align: right; }
  .msg.out .body { background: var(--surface-2); }
  /* the substrate: session prose sits BEHIND the correspondence — outlined,
     not surfaced, so the ✉ plane reads as the elevated layer */
  .msg.sess .body { background: transparent; border-color: var(--grid); }
  .msg.sess .meta { opacity: 0.8; }
  .msg .body code {
    font-family: var(--font-mono); font-size: 0.92em;
    background: var(--plane); border: 1px solid var(--grid);
    border-radius: 4px; padding: 0 4px;
  }
  .msg .body strong { font-weight: 600; }
  .msg .body ul { padding-left: 18px; margin: 4px 0; white-space: normal; }
  .msg .body li { margin: 2px 0; }
  .code {
    position: relative;
    font-family: var(--font-mono); font-size: 12.5px;
    background: var(--plane); border: 1px solid var(--grid); border-radius: 6px;
    padding: 8px 10px; margin: 6px 0 2px; overflow-x: auto; white-space: pre;
  }
  .code .cp {
    position: absolute; top: 6px; right: 6px;
    background: var(--surface); color: var(--muted);
    border: 1px solid var(--baseline); border-radius: 3px;
    font-size: 10px; letter-spacing: 0.05em; text-transform: uppercase;
    padding: 2px 8px; cursor: pointer;
  }
  .code .cp:active { color: var(--ink); }

  .trn-tool { font-family: var(--font-mono); font-size: 11px;
              color: var(--muted); padding: 1px 2px;
              white-space: pre-wrap; word-break: break-word; }
  .trn-mark { align-self: center; color: var(--muted);
              font-family: var(--font-mono); font-size: 10.5px;
              letter-spacing: 0.12em; text-transform: uppercase;
              padding: 6px 0; }
  .trn-time { align-self: stretch; display: flex; align-items: center;
              gap: 12px; color: var(--muted);
              font-family: var(--font-mono); font-size: 10px;
              letter-spacing: 0.12em; text-transform: uppercase;
              font-variant-numeric: tabular-nums; padding: 8px 0 0; }
  .trn-time::before, .trn-time::after {
    content: ""; flex: 1; border-top: 1px solid var(--grid); }
  /* the work ledger: consecutive receipts fold into one line of record */
  details.ledger { margin: 0; }
  details.ledger summary {
    cursor: pointer; list-style: none; -webkit-tap-highlight-color: transparent;
    color: var(--muted); font-family: var(--font-mono); font-size: 10.5px;
    letter-spacing: 0.12em; text-transform: uppercase; padding: 3px 0;
    display: flex; align-items: center; gap: 8px;
    font-variant-numeric: tabular-nums;
  }
  details.ledger summary::-webkit-details-marker { display: none; }
  details.ledger summary::before { content: "\25B8"; font-size: 9px;
                                   color: var(--baseline); }
  details.ledger[open] summary::before { content: "\25BE"; }
  details.ledger .trn-tool { padding-left: 14px; margin-left: 3px;
                             border-left: 1px solid var(--grid); }
  #older { align-self: center; background: transparent;
           color: var(--ink-2); border: 1px solid var(--baseline);
           border-radius: 3px; padding: 5px 12px;
           font-family: var(--font-mono); font-size: 11px; cursor: pointer; }
  #older:hover { border-color: var(--ink); color: var(--ink); }
  #pill {
    position: fixed; left: 50%; transform: translateX(-50%);
    bottom: calc(78px + env(safe-area-inset-bottom));
    background: var(--surface-2); color: var(--ink);
    border: 1px solid var(--baseline); border-radius: 7px;
    padding: 5px 12px; font-family: var(--font-mono); font-size: 10.5px;
    letter-spacing: 0.12em; text-transform: uppercase; cursor: pointer;
  }
  #pill[hidden] { display: none; }
  body.msgs .sess, body.msgs details.ledger { display: none; }
  .empty { color: var(--muted); font-size: 13px; padding: 8px 2px; }
  @media (prefers-reduced-motion: no-preference) {
    .msg, .trn-tool, .trn-mark, .trn-time, details.ledger {
      animation: rise 0.18s ease-out; }
  }
  @keyframes rise { from { opacity: 0; transform: translateY(3px); } }

  form {
    display: flex; gap: 8px;
    padding: 10px 12px calc(10px + env(safe-area-inset-bottom));
    border-top: 1px solid var(--grid);
    background: var(--plane);
  }
  #inp {
    flex: 1; resize: none;
    background: var(--surface); color: var(--ink);
    border: 1px solid var(--border); border-radius: 7px;
    padding: 10px 12px; font: inherit; font-size: 16px; /* 16px: no iOS zoom */
    max-height: 30dvh;
  }
  #snd {
    align-self: flex-end;
    background: transparent; color: var(--ink-2);
    border: 1px solid var(--baseline); border-radius: 3px;
    padding: 10px 15px; font-family: var(--font-mono); font-size: 11.5px;
    cursor: pointer;
    transition: border-color 0.15s ease, color 0.15s ease;
  }
  #snd:hover:not(:disabled) { border-color: var(--ink); color: var(--ink); }
  #snd:disabled { opacity: 0.55; cursor: default; }

/* The voice key. An instrument face, not a round mic bubble: state reads
   from the WORD and the border weight, never from colour alone, so it is
   legible in grayscale and to anyone who cannot pick the accent out. */
.composer .mic{
  flex:0 0 auto; min-width:62px; padding:0 10px;
  font:500 10px/1 var(--font-mono); letter-spacing:.1em; text-transform:uppercase;
  color:var(--ink-2); background:var(--surface); border:1px solid var(--border);
  cursor:pointer; -webkit-tap-highlight-color:transparent;
}
.composer .mic:hover{ color:var(--ink); background:var(--surface-2); }
.composer .mic[data-state="listening"]{
  color:var(--ink); border-color:var(--status-good); border-width:2px;
}
.composer .mic[data-state="thinking"]{ color:var(--muted); }
.composer .mic[data-state="speaking"]{
  color:var(--ink); border-color:var(--status-warn); border-width:2px;
}
.composer .mic[disabled]{ opacity:.4; cursor:default; }
/* Live input level — real microphone amplitude, so a dead mic looks dead
   rather than animating reassuringly at nothing. */
.miclvl{ height:2px; background:var(--grid); margin:0 0 4px; }
.miclvl > i{ display:block; height:100%; width:0; background:var(--status-good); }
@media (prefers-reduced-motion:reduce){ .miclvl > i{ transition:none; } }
</style>
</head>
<body>
<script id="talk-boot" type="application/json">__BOOT__</script>
<header>
  <a href="/" id="back">&#9666; board</a>
  <span id="dot"></span>
  <span class="who" id="who"></span>
  <button id="filt" class="msgs-only" type="button" title="messages only">&#9993;</button>
  <span class="tag" id="stat"></span>
</header>
<div id="note" hidden></div>
<div id="log" class="composer-log"></div>
<button id="pill" type="button" hidden>new &#8595;</button>
<form id="composer" class="composer">
  <button id="mic" type="button" class="mic" title="hold to talk"
          aria-label="press to talk">talk</button>
  <textarea id="inp" rows="1" placeholder="message" autocomplete="off"></textarea>
  <button id="snd" type="submit">send</button>
</form>
<script>
(function () {
  "use strict";
  var boot;
  try { boot = JSON.parse(document.getElementById("talk-boot").textContent); }
  catch (e) { document.body.textContent = "no boot data"; return; }
  var target = boot.target, token = boot.token, handle = boot.handle;
  document.title = "talk / " + target;
  document.getElementById("who").textContent = target;

  var log = document.getElementById("log");
  var inp = document.getElementById("inp");
  var snd = document.getElementById("snd");
  var stat = document.getElementById("stat");
  var note = document.getElementById("note");
  var filt = document.getElementById("filt");
  var pill = document.getElementById("pill");
  var dot = document.getElementById("dot");
  // Composite cursor: tCur is the transcript turn index, tsCur the mail
  // timestamp. Both advance ONLY from server responses.
  var tCur = null, tsCur = 0;
  var sSid = null;            // transcript identity — a change resets the pane
  var seen = {};
  var firstI = null, older = null, olderBusy = false;
  var inFlight = false;
  var curLedger = null, curLedgerN = 0;
  var lastDivTs = 0;

  function el(t, c, x) {
    var n = document.createElement(t);
    if (c) n.className = c;
    if (x != null) n.textContent = x;
    return n;
  }
  function ago(ts) {
    var d = Math.max(0, Math.floor(Date.now() / 1000 - Number(ts)));
    if (d < 8) return "just now";
    if (d < 60) return d + "s ago";
    if (d < 3600) return Math.floor(d / 60) + "m ago";
    if (d < 86400) return Math.floor(d / 3600) + "h ago";
    return Math.floor(d / 86400) + "d ago";
  }
  var MONTHS = ["jan","feb","mar","apr","may","jun",
                "jul","aug","sep","oct","nov","dec"];
  function fmtClock(ts) {
    var d = new Date(ts * 1000);
    var hh = ("0" + d.getHours()).slice(-2);
    var mm = ("0" + d.getMinutes()).slice(-2);
    return hh + ":" + mm + " · " + MONTHS[d.getMonth()] + " " + d.getDate();
  }
  // Markdown-lite, textContent-only: fenced code (with copy), inline code,
  // bold, dash lists. Bounded token classes — nothing here backtracks and
  // nothing from the fabric can become markup.
  function inline(parent, s) {
    var re = /(\*\*[^*\n]{1,200}\*\*|`[^`\n]{1,200}`)/g;
    var last = 0, m;
    while ((m = re.exec(s))) {
      if (m.index > last) parent.appendChild(document.createTextNode(s.slice(last, m.index)));
      var tok = m[0];
      if (tok.charAt(0) === "`") parent.appendChild(el("code", null, tok.slice(1, -1)));
      else parent.appendChild(el("strong", null, tok.slice(2, -2)));
      last = m.index + tok.length;
    }
    if (last < s.length) parent.appendChild(document.createTextNode(s.slice(last)));
  }
  function renderBody(parent, text) {
    var parts = String(text).split("```");
    for (var i = 0; i < parts.length; i++) {
      if (!parts[i]) continue;
      if (i % 2 === 1) {
        var codeText = parts[i].replace(/^[a-z]*\n/, "");
        var box = el("div", "code");
        box.appendChild(document.createTextNode(codeText));
        var cp = el("button", "cp", "copy");
        cp.type = "button";
        (function (t, b) {
          b.addEventListener("click", function () {
            try {
              navigator.clipboard.writeText(t);
              b.textContent = "copied";
              setTimeout(function () { b.textContent = "copy"; }, 1200);
            } catch (e) {}
          });
        })(codeText, cp);
        box.appendChild(cp);
        parent.appendChild(box);
      } else {
        var lines = parts[i].split("\n");
        var ul = null;
        for (var j = 0; j < lines.length; j++) {
          var ln = lines[j];
          if (/^\s*[-•] /.test(ln)) {
            if (!ul) { ul = el("ul"); parent.appendChild(ul); }
            var li = el("li");
            inline(li, ln.replace(/^\s*[-•] /, ""));
            ul.appendChild(li);
          } else {
            ul = null;
            inline(parent, ln);
            if (j < lines.length - 1) parent.appendChild(document.createTextNode("\n"));
          }
        }
      }
    }
  }
  function key(it) {
    return it.via === "mail" ? "m|" + it.ts + "|" + it.role + "|" + it.text
                             : "s|" + it.i;
  }
  function bubble(cls, meta, text) {
    var m = el("div", "msg " + cls);
    m.appendChild(el("div", "meta", meta.join(" · ")));
    var b = el("div", "body");
    renderBody(b, text);
    m.appendChild(b);
    return m;
  }
  function build(it) {
    if (it.via === "mail") {
      if (it.role === "user") {
        var meta = ["you", ago(it.ts)];
        if (it.routed === "live") meta.push("delivered live");
        else if (it.routed === "inbox") meta.push("queued — delivers on wake");
        else if (it.routed) meta.push(it.routed);
        return bubble("out", meta, it.text);
      }
      return bubble("in", ["✉ " + (it.who || target), ago(it.ts)], it.text);
    }
    var n;
    if (it.role === "tool") n = el("div", "trn-tool sess", it.text);
    else if (it.role === "mark") n = el("div", "trn-mark sess", it.text);
    else if (it.role === "reply") {
      // A kept reply TO the viewer has no mailbox copy (refused, or aged
      // out of the tail): show its content honestly, marked unconfirmed.
      if (it.to === handle) {
        n = bubble("in", [target, ago(it.ts), "unconfirmed"], it.text);
        n.className += " sess";
      } else n = el("div", "trn-tool sess", "⟶ send " + (it.to || "?"));
    }
    else if (it.role === "user") {
      // A foreign sender renders under its own name, never as "you".
      n = (it.who && it.who !== handle)
        ? bubble("in", [it.who, ago(it.ts)], it.text)
        : bubble("out", ["you · term", ago(it.ts)], it.text);
      n.className += " sess";
    } else {
      n = bubble("in", [target, ago(it.ts)], it.text);
      n.className += " sess";
    }
    return n;
  }
  function trackFirstI(it) {
    if (it.via === "session" && typeof it.i === "number"
        && (firstI === null || it.i < firstI)) firstI = it.i;
  }
  function addItem(it, front) {
    var k = key(it);
    if (seen[k]) return false;
    seen[k] = 1;
    // A voice turn is answered out loud. Only fresh MAIL from the agent —
    // never our own message, never session receipts, and never history
    // being paged in behind us.
    if (window.__voice && !front && it.via === "mail"
        && it.role !== "user" && it.text) {
      window.__voice.speak(it.text);
    }
    trackFirstI(it);
    // The work ledger: consecutive receipts (tool lines, sends to third
    // parties) fold into one collapsible record; anything else closes it.
    var isReceipt = it.via === "session"
      && (it.role === "tool" || (it.role === "reply" && it.to !== handle));
    if (!front && isReceipt) {
      if (!curLedger) {
        curLedger = el("details", "ledger sess");
        curLedger.appendChild(el("summary", null, ""));
        curLedgerN = 0;
        log.appendChild(curLedger);
      }
      curLedgerN++;
      curLedger.children[0].textContent =
        "⚙ worked · " + curLedgerN + " step" + (curLedgerN === 1 ? "" : "s");
      curLedger.appendChild(el("div", "trn-tool",
        it.role === "tool" ? it.text : "⟶ send " + (it.to || "?")));
      return true;
    }
    if (!front) {
      curLedger = null;
      if (it.ts && it.ts - lastDivTs > 1800) {
        log.appendChild(el("div", "trn-time", fmtClock(it.ts)));
        lastDivTs = it.ts;
      }
    }
    var n = build(it);
    if (front && older) log.insertBefore(n, older.nextSibling);
    else log.appendChild(n);
    return true;
  }
  function setNote(text, kind) {
    if (text) { note.textContent = text; note.className = kind || ""; note.hidden = false; }
    else note.hidden = true;
  }
  function reset(markText) {
    log.textContent = "";
    seen = {}; tCur = null; firstI = null; older = null;
    curLedger = null; lastDivTs = 0;
    tsCur = 0;   // replay the WHOLE mail thread — a restart must never eat it
    if (markText) log.appendChild(el("div", "trn-mark", markText));
  }
  function tlFetch(qs, onDone) {
    // onDone ALWAYS runs (null on failure) so busy-guards release.
    fetch("/api/timeline/" + encodeURIComponent(target) + qs,
          { headers: { "X-Homi-Token": token }, cache: "no-store" })
      .then(function (r) {
        if (r.status === 403) { stat.textContent = "session expired — reload"; return null; }
        if (!r.ok) { stat.textContent = "server error " + r.status; return null; }
        return r.json();
      })
      .then(function (d) { onDone(d || null); },
            function () { stat.textContent = "disconnected"; onDone(null); });
  }
  function poll() {
    if (document.hidden) return;
    var qs = tCur === null ? "?ts_after=" + tsCur
                           : "?t_after=" + tCur + "&ts_after=" + tsCur;
    tlFetch(qs, function (d) {
      if (!d || !d.ok) return;
      if (d.sid && sSid && d.sid !== sSid) {
        // This response was fetched with PRE-restart cursors — applying any
        // of it (items OR cursors) would clobber the reset. Discard it,
        // rebuild the pane, and refetch the world from zero.
        reset("· session restarted ·");
        sSid = d.sid;
        poll();
        return;
      }
      if (d.sid) sSid = d.sid;
      dot.className = d.live ? "live" : (d.present ? "asleep" : "");
      if (!d.present) setNote("no session on this device — messages only", "");
      else if (!d.live) setNote("asleep — messages queue and deliver on wake", "warn");
      else setNote(null);
      var atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
      var firstLoad = (tCur === null);
      if (!firstLoad) {
        for (var i = 0; i < d.items.length; i++) {
          var it = d.items[i];
          if (it.via === "session" && typeof it.i === "number") {
            if (it.i > tCur + 1)
              log.appendChild(el("div", "trn-mark", "· gap — turns evicted ·"));
            break;
          }
        }
      }
      var had = false;
      d.items.forEach(function (it) { if (addItem(it, false)) had = true; });
      if (typeof d.t_cursor === "number" && (tCur === null || d.t_cursor > tCur))
        tCur = d.t_cursor;
      if (typeof d.ts_cursor === "number" && d.ts_cursor > tsCur)
        tsCur = d.ts_cursor;
      if (older === null && firstI !== null && (firstI > 0 || d.truncated)) {
        older = el("button", null, "earlier");
        older.id = "older"; older.type = "button";
        older.addEventListener("click", loadOlder);
        log.insertBefore(older, log.firstChild);
      }
      stat.textContent = "";
      if (atBottom || firstLoad) { log.scrollTop = log.scrollHeight; pill.hidden = true; }
      else if (had) pill.hidden = false;
    });
  }
  function loadOlder() {
    if (olderBusy || firstI === null || firstI <= 0) return;
    olderBusy = true;
    tlFetch("?t_before=" + firstI, function (d) {
      olderBusy = false;
      if (!d || !d.ok) return;
      var h0 = log.scrollHeight;
      (d.items || []).slice().reverse().forEach(function (it) { addItem(it, true); });
      // Step by the RAW window (t_min), not rendered items — an all-dropped
      // batch must keep paging, not lie "start".
      if (typeof d.t_min === "number" && d.t_min !== null
          && (firstI === null || d.t_min < firstI)) firstI = d.t_min;
      log.scrollTop += log.scrollHeight - h0;   // keep your place
      if (firstI <= 0 || d.t_min === null) {
        older.textContent = d.truncated
          ? "· earlier history trimmed ·" : "· start ·";
        older.disabled = true;
      }
    });
  }
  filt.addEventListener("click", function () {
    document.body.classList.toggle("msgs");
    filt.classList.toggle("on");
    log.scrollTop = log.scrollHeight;
  });
  pill.addEventListener("click", function () {
    log.scrollTop = log.scrollHeight;
    pill.hidden = true;
  });
  log.addEventListener("scroll", function () {
    if (log.scrollHeight - log.scrollTop - log.clientHeight < 40) pill.hidden = true;
  });

  document.getElementById("composer").addEventListener("submit", function (ev) {
    ev.preventDefault();
    if (inFlight) return;   // guard the keydown path too, not just the button
    var text = inp.value.trim();
    if (!text) return;
    inFlight = true; snd.disabled = true; stat.textContent = "";
    fetch("/api/send", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Homi": "1",
                 "X-Homi-Token": token },
      // The ear being on is the ONLY signal that this turn is spoken rather
      // than typed, and the agent never sees the URL. Pass it along so the
      // answer comes back shaped for listening: short, no markdown, answer
      // first. The server, not the page, does the marking.
      body: JSON.stringify({ to: target, text: text,
                             voice: !!(window.__voice && window.__voice.earOn) }),
    }).then(function (r) {
        return r.json().then(function (d) { return { ok: r.ok, d: d }; },
                             function () { return { ok: r.ok, d: null }; });
      })
      .then(function (res) {
        if (res.ok && res.d && res.d.entry) {
          var e = res.d.entry;
          addItem({ via: "mail", role: "user", ts: e.ts, text: e.text,
                    routed: e.routed });
          inp.value = "";
          log.scrollTop = log.scrollHeight;
        }
        else stat.textContent = (res.d && res.d.err) ? res.d.err : "send failed";
      })
      .catch(function () { stat.textContent = "send failed"; })
      .then(function () { inFlight = false; snd.disabled = false; inp.focus(); });
  });
  inp.addEventListener("keydown", function (ev) {
    if (ev.key === "Enter" && !ev.shiftKey) {
      ev.preventDefault();
      document.getElementById("composer").dispatchEvent(new Event("submit", { cancelable: true }));
    }
  });

  poll();
  window.setInterval(poll, 2500);
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) poll();   // reload-on-foreground: iOS froze us
  });
})();

// Register the service worker so Chrome offers "install to home screen".
// The worker caches nothing — this page is a live view of a live fabric —
// it exists to satisfy the install criteria and to carry push later.
if ("serviceWorker" in navigator) {
  window.addEventListener("load", function () {
    navigator.serviceWorker.register("/sw.js").catch(function () {
      /* not installable here (http origin, or worker blocked) — the page
         still works exactly as before; installation is a bonus, not a
         dependency. */
    });
  });
}

// ---------------------------------------------------------------- voice
// The console answers out loud when asked out loud. This is the whole of
// homi_voice.py's working half, folded into the one surface that already
// has history, paging, the work fold and the restart guard — rather than a
// second page that re-implemented the timeline and lost all four.
(function () {
  "use strict";
  var mic = document.getElementById("mic");
  var inp = document.getElementById("inp");
  if (!mic) return;

  var SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  var synthOK = "speechSynthesis" in window;
  // Chrome silently truncates an utterance at roughly 15 seconds, so a long
  // reply must be broken into sentence-sized pieces and queued; otherwise
  // the agent is cut off mid-thought with no error anywhere.
  var CHUNK_MAX = 170;
  var gen = 0;                 // bumps on every interrupt or new turn
  var earOn = /[?&]v=1\b/.test(location.search);
  var rec = null, level = null, audioCtx = null, stream = null;

  function setState(st) {
    mic.setAttribute("data-state", st);
    mic.textContent = st === "listening" ? "listening"
                    : st === "thinking" ? "thinking"
                    : st === "speaking" ? "speaking" : "talk";
  }
  setState("idle");
  if (!SR) { mic.disabled = true; mic.title = "no speech recognition here"; }

  function chunkText(text) {
    var raw = String(text || "").trim();
    if (!raw) return [];
    var sentences = raw.match(/[^.!?]+[.!?]+(\s+|$)|[^.!?]+$/g) || [raw];
    var out = [];
    sentences.forEach(function (sen) {
      sen = sen.trim();
      if (!sen) return;
      if (sen.length <= CHUNK_MAX) { out.push(sen); return; }
      var parts = sen.split(/,\s+/), buf = "";
      parts.forEach(function (p, i) {
        var piece = p + (i < parts.length - 1 ? "," : "");
        if ((buf + " " + piece).trim().length > CHUNK_MAX && buf) {
          out.push(buf.trim()); buf = piece;
        } else { buf = (buf + " " + piece).trim(); }
      });
      if (buf) {
        while (buf.length > CHUNK_MAX) {
          var cut = buf.lastIndexOf(" ", CHUNK_MAX);
          if (cut <= 0) cut = CHUNK_MAX;
          out.push(buf.slice(0, cut).trim());
          buf = buf.slice(cut).trim();
        }
        if (buf) out.push(buf);
      }
    });
    return out;
  }

  function speakChunks(chunks, myGen) {
    if (!synthOK || !chunks.length) { setState("idle"); return; }
    setState("speaking");
    var i = 0;
    (function next() {
      if (myGen !== gen) return;              // interrupted, or a newer turn
      if (i >= chunks.length) { setState("idle"); return; }
      var u = new SpeechSynthesisUtterance(chunks[i]);
      u.lang = "en-US";                        // Android reports en_US; be explicit
      u.onend = function () { i++; next(); };
      // One bad chunk must not strand the rest of the answer unsaid.
      u.onerror = function () { i++; next(); };
      window.speechSynthesis.speak(u);
    })();
  }

  function mapSpeechError(code) {
    switch (code) {
      case "no-speech": return "no speech detected — tap talk to retry";
      case "aborted": return "listening stopped";
      case "audio-capture": return "no microphone found";
      case "not-allowed": return "microphone permission denied";
      case "network": return "speech service unreachable";
      case "service-not-allowed": return "speech service blocked";
      default: return "speech error: " + code;
    }
  }

  function stopMeter() {
    if (level) { level.parentNode && level.parentNode.remove(); level = null; }
    if (stream) { stream.getTracks().forEach(function (t) { t.stop(); }); stream = null; }
    if (audioCtx) { try { audioCtx.close(); } catch (e) {} audioCtx = null; }
  }

  function startMeter() {
    // Real amplitude, best-effort. If permission is refused the bar simply
    // never moves, which is the honest picture of a mic that is not working.
    if (!navigator.mediaDevices || !window.AudioContext) return;
    navigator.mediaDevices.getUserMedia({ audio: true }).then(function (st) {
      stream = st;
      var wrap = document.createElement("div");
      wrap.className = "miclvl";
      level = document.createElement("i");
      wrap.appendChild(level);
      inp.parentNode.insertBefore(wrap, inp.parentNode.firstChild);
      audioCtx = new AudioContext();
      var an = audioCtx.createAnalyser();
      an.fftSize = 512;
      audioCtx.createMediaStreamSource(st).connect(an);
      var buf = new Uint8Array(an.frequencyBinCount);
      (function tick() {
        if (!level) return;
        an.getByteTimeDomainData(buf);
        var sum = 0;
        for (var j = 0; j < buf.length; j++) { var v = (buf[j] - 128) / 128; sum += v * v; }
        var rms = Math.sqrt(sum / buf.length);
        level.style.width = Math.min(100, Math.round(rms * 320)) + "%";
        requestAnimationFrame(tick);
      })();
    }).catch(function () { /* no meter; recognition may still work */ });
  }

  function note(msg) {
    var n = document.getElementById("note") || document.getElementById("stat");
    if (n) n.textContent = msg || "";
  }

  function listen() {
    if (!SR) return;
    gen++;                                   // any new turn cancels speech
    if (synthOK) window.speechSynthesis.cancel();
    rec = new SR();
    rec.lang = "en-US";
    rec.interimResults = true;
    // continuous is unreliable on Android Chrome; one utterance per press.
    rec.continuous = false;
    var finalText = "";
    setState("listening");
    startMeter();
    rec.onresult = function (e) {
      var interim = "";
      for (var i = e.resultIndex; i < e.results.length; i++) {
        var t = e.results[i][0].transcript;
        if (e.results[i].isFinal) finalText += t; else interim += t;
      }
      inp.value = (finalText + interim).trim();
    };
    rec.onerror = function (e) { note(mapSpeechError(e.error)); };
    rec.onend = function () {
      stopMeter();
      setState("idle");
      rec = null;
      var text = inp.value.trim();
      if (!text) return;
      earOn = true;                          // spoke to it, so answer aloud
      setState("thinking");
      // Submit through the page's own send path so the message, its routing
      // tag and the ledger all behave exactly as a typed one.
      var f = document.getElementById("composer");
      if (f) f.dispatchEvent(new Event("submit", { cancelable: true, bubbles: true }));
    };
    try { rec.start(); } catch (e) { setState("idle"); note("could not start listening"); }
  }

  mic.addEventListener("click", function () {
    if (rec) { try { rec.stop(); } catch (e) {} return; }
    if (window.speechSynthesis && window.speechSynthesis.speaking) {
      // Tapping while it talks is the interrupt.
      gen++;
      window.speechSynthesis.cancel();
      setState("idle");
      return;
    }
    if (NATIVE) {
      // On-device. The audio never leaves the phone, and there is no
      // network leg to fail.
      setState("listening");
      window.__homiHeard = function (text) {
        window.__homiHeard = null;
        if (!text) { setState("idle"); note("didn\'t catch that"); return; }
        inp.value = text;
        earOn = true;                        // spoken to, so answer aloud
        setState("thinking");
        var f = document.getElementById("composer");
        if (f) f.dispatchEvent(new Event("submit",
                               { cancelable: true, bubbles: true }));
      };
      window.homi.listen(20);
      return;
    }
    listen();                                 // start() inside a tap handler
  });

  // Running inside the homi app? Then speech is NATIVE, and the difference
  // is not cosmetic: Chrome on Android has no on-device recogniser at all
  // (its on-device API excludes Android), a ~3-5s silence cutoff, and a
  // documented-broken continuous mode; speechSynthesis buffers the whole
  // utterance before making a sound where a held engine starts in ~11ms.
  // Same page, same board — it just stops going through the browser for the
  // two things the browser is worst at.
  var NATIVE = !!(window.homi && window.homi.available
                  && window.homi.available());

  window.__voice = {
    // The send block lives in a DIFFERENT closure and cannot see `earOn`.
    // Publishing it here is the contract between them: a bare `earOn` over
    // there compiles fine and throws ReferenceError the first time anyone
    // speaks, which is the worst possible moment to find out.
    //
    // A GETTER, not `earOn: earOn`. The ear does not only come from ?v=1 —
    // pressing the mic on an ordinary talk page turns it on mid-life (see
    // rec.onend). A plain property copies the boolean once at page load, so
    // that path would send voice:false, get a markdown answer back, and then
    // read the asterisks aloud, which is the exact symptom this whole flag
    // exists to prevent. Reading through a getter cannot go stale.
    get earOn() { return earOn; },
    native: NATIVE,
    speak: function (text) {
      if (!earOn) return;
      if (NATIVE) {
        // No chunking. chunkText exists because Chrome truncates around 15
        // seconds; a real engine has no such limit, and splitting a sentence
        // introduces pauses a person hears as hesitation.
        gen++;
        try { window.homi.stopSpeaking(); } catch (e) {}
        window.homi.say(text);
        return;
      }
      if (!synthOK) return;
      gen++;
      window.speechSynthesis.cancel();
      speakChunks(chunkText(text), gen);
    },
    chunkText: chunkText
  };
})();
</script>
</body>
</html>
"""
