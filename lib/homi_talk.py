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
    total = 0
    truncated = False
    if not qual:
        d = homi_transcript.turns_for(base, after=t_after, before=t_before,
                                      n=n)
        live, present = d.get("live", False), d.get("present", False)
        sid = d.get("sid") or ""
        total = d.get("total") or 0
        truncated = bool(d.get("truncated"))
        last_ts = 0.0
        for t in d.get("turns") or []:
            if t.get("ts"):
                last_ts = t["ts"]
            if t["i"] > t_cursor:
                t_cursor = t["i"]
            if t["role"] == "user" and t.get("who") == handle:
                continue   # the mail thread carries this
            if t["role"] == "reply" and t.get("to") == handle:
                continue   # the mail thread carries this
            it = dict(t)
            it["via"] = "session"
            if not it.get("ts"):
                it["ts"] = last_ts
            items.append(it)
    ts_cursor = ts_after
    if t_before is None:
        for e in conversation(handle, target, since=ts_after, inbox=inbox):
            items.append({"via": "mail",
                          "role": "user" if e.get("dir") == "out" else "in",
                          "ts": e.get("ts") or 0, "text": e.get("text", ""),
                          "routed": e.get("routed"), "who": e.get("from")})
            if (e.get("ts") or 0) > ts_cursor:
                ts_cursor = e["ts"]
    items.sort(key=lambda i: i.get("ts") or 0)
    return {"ok": True, "live": live, "present": present, "sid": sid,
            "items": items, "t_cursor": t_cursor, "ts_cursor": ts_cursor,
            "total": total, "truncated": truncated}


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
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; form-action 'none'">
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
    font-size: 15px; line-height: 1.45; letter-spacing: -0.006em;
    -webkit-font-smoothing: antialiased;
    display: flex; flex-direction: column;
    height: 100vh;   /* fallback for browsers without dvh */
    height: 100dvh;
  }
  :focus-visible { outline: 2px solid var(--ink); outline-offset: 2px; }

  header {
    display: flex; align-items: baseline; gap: 12px;
    padding: 14px 16px 10px;
    border-bottom: 1px solid var(--grid);
    padding-top: calc(14px + env(safe-area-inset-top));
  }
  header a { color: var(--muted); text-decoration: none; font-size: 13px; }
  header a:hover { color: var(--ink); }
  header .who { font-weight: 500; letter-spacing: -0.012em; }
  header .tag { margin-left: auto; font-size: 10.5px; letter-spacing: 0.06em;
                text-transform: uppercase; color: var(--muted); }

  #log, #sess {
    flex: 1; overflow-y: auto;
    padding: 14px 16px;
    display: flex; flex-direction: column; gap: 10px;
  }
  /* The author flex rule above outranks the UA's [hidden] { display:none }
     — without this guard the tabs render both panes stacked, always. */
  #log[hidden], #sess[hidden] { display: none; }
  #tabs { display: flex; gap: 2px; padding: 0 12px;
          border-bottom: 1px solid var(--grid); }
  .tab { background: none; border: none; color: var(--muted); font: inherit;
         font-size: 12px; letter-spacing: 0.06em; text-transform: uppercase;
         padding: 8px 10px; cursor: pointer;
         border-bottom: 2px solid transparent; }
  .tab.on { color: var(--ink); border-bottom-color: var(--ink); }
  .trn-tool { font-family: var(--font-mono); font-size: 12px; color: var(--muted);
              padding: 1px 2px; white-space: pre-wrap; word-break: break-word; }
  .trn-mark { align-self: center; color: var(--muted); font-size: 11px;
              letter-spacing: 0.08em; text-transform: uppercase; padding: 6px 0; }
  #older { align-self: center; background: var(--surface); color: var(--muted);
           border: 1px solid var(--border); border-radius: 8px; padding: 6px 12px;
           font: inherit; font-size: 12px; cursor: pointer; }
  .msg { max-width: 86%; }
  .msg .meta { font-size: 10.5px; letter-spacing: 0.06em; text-transform: uppercase;
               color: var(--muted); margin-bottom: 3px; font-variant-numeric: tabular-nums; }
  .msg .body {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 9px 12px;
    white-space: pre-wrap; word-break: break-word;
  }
  .msg.out { align-self: flex-end; }
  .msg.out .meta { text-align: right; }
  .msg.out .body { background: var(--surface-2); }
  .msg .code {
    font-family: var(--font-mono); font-size: 12.5px;
    background: var(--plane); border: 1px solid var(--grid); border-radius: 6px;
    padding: 8px 10px; margin: 6px 0 2px; overflow-x: auto; white-space: pre;
  }
  .empty { color: var(--muted); font-size: 13px; padding: 8px 2px; }

  form {
    display: flex; gap: 8px;
    padding: 10px 12px calc(10px + env(safe-area-inset-bottom));
    border-top: 1px solid var(--grid);
    background: var(--plane);
  }
  #inp {
    flex: 1; resize: none;
    background: var(--surface); color: var(--ink);
    border: 1px solid var(--border); border-radius: 10px;
    padding: 10px 12px; font: inherit; font-size: 16px; /* 16px: no iOS zoom */
    max-height: 30dvh;
  }
  #snd {
    align-self: flex-end;
    background: var(--surface-2); color: var(--ink);
    border: 1px solid var(--border); border-radius: 8px;
    padding: 10px 16px; font: inherit; font-size: 13px; font-weight: 500;
    cursor: pointer;
  }
  #snd:disabled { color: var(--muted); }
</style>
</head>
<body>
<script id="talk-boot" type="application/json">__BOOT__</script>
<header>
  <a href="/" id="back">&#9666; board</a>
  <span class="who" id="who"></span>
  <button id="filt" class="msgs-only" type="button" title="messages only">&#9993;</button>
  <span class="tag" id="stat"></span>
</header>
<div id="note" hidden></div>
<div id="log" class="composer-log"></div>
<form id="composer" class="composer">
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
  // Composite cursor: tCur is the transcript turn index, tsCur the mail
  // timestamp. Both advance ONLY from server responses.
  var tCur = null, tsCur = 0;
  var sSid = null;            // transcript identity — a change resets the pane
  var seen = {};
  var firstI = null, older = null, olderBusy = false;
  var inFlight = false;

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
  // Fenced code renders mono; everything else is textContent — nothing from
  // the fabric or a transcript can become markup.
  function renderBody(parent, text) {
    var parts = String(text).split("```");
    for (var i = 0; i < parts.length; i++) {
      if (!parts[i]) continue;
      if (i % 2 === 1) parent.appendChild(el("div", "code", parts[i].replace(/^[a-z]*\n/, "")));
      else parent.appendChild(document.createTextNode(parts[i]));
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
    // session items — the agent's working life
    var n;
    if (it.role === "tool") n = el("div", "trn-tool sess", it.text);
    else if (it.role === "mark") n = el("div", "trn-mark sess", it.text);
    else if (it.role === "reply")
      n = el("div", "trn-tool sess", "⟶ send " + (it.to || "?"));
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
  function addItem(it, front) {
    var k = key(it);
    if (seen[k]) return;
    seen[k] = 1;
    var n = build(it);
    if (front && older) log.insertBefore(n, older.nextSibling);
    else log.appendChild(n);
    if (it.via === "session" && typeof it.i === "number"
        && (firstI === null || it.i < firstI)) firstI = it.i;
  }
  function setNote(text) {
    if (text) { note.textContent = text; note.hidden = false; }
    else note.hidden = true;
  }
  function reset(markText) {
    log.textContent = "";
    seen = {}; tCur = null; firstI = null; older = null;
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
      if (d.sid && sSid && d.sid !== sSid) reset("· session restarted ·");
      if (d.sid) sSid = d.sid;
      if (!d.present) setNote("no session on this device — messages only");
      else if (!d.live) setNote("asleep — messages queue and deliver on wake");
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
      d.items.forEach(function (it) { addItem(it, false); });
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
      if (atBottom || firstLoad) log.scrollTop = log.scrollHeight;
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
      log.scrollTop += log.scrollHeight - h0;   // keep your place
      if (firstI <= 0 || !(d.items || []).length) {
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
      body: JSON.stringify({ to: target, text: text }),
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
</script>
</body>
</html>
"""
