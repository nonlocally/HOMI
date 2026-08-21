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
    filename- or path-shaped sneaks in."""
    base, _, qual = (target or "").partition("@")
    if not homi.Homi._NAME_RE.match(base):
        return False
    return not qual or bool(homi.Homi._DEV_RE.match(qual))


def _talk_dir():
    import homi_board
    return os.path.join(homi_board.board_dir(), "talk")


def _journal_path(target):
    return os.path.join(_talk_dir(), target.replace("@", "+") + ".jsonl")


def journal_append(target, entry):
    d = _talk_dir()
    homi.ensure_dir_0700(d)
    p = _journal_path(target)
    with open(p, "a") as f:
        f.write(json.dumps(entry) + "\n")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def send(handle, target, text):
    """One daemon send + a journal line. Returns the daemon's honest routed
    verdict (live / inbox / link:<dev>) or raises ValueError on refusal."""
    r = homi._call({"op": "send", "to": target, "text": text, "from": handle})
    if not r.get("ok"):
        raise ValueError(r.get("err") or "send failed")
    entry = {"ts": time.time(), "dir": "out", "text": text,
             "routed": r.get("routed")}
    journal_append(target, entry)
    return entry


def conversation(handle, target, since=0.0):
    """Merged, time-ordered view: journal (out) + the human's inbox filtered
    by correspondent (in). Bare targets match any arrival of that base name;
    qualified targets additionally require the arrival device."""
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
        r = homi._call({"op": "inbox", "name": handle, "tail": 500})
    except (Exception, SystemExit):
        r = {}
    for m in (r.get("messages") or []):
        fn = (m.get("from_name") or "").partition("@")[0]
        if fn != base:
            continue
        if qual and m.get("via") != qual:
            continue
        ts = m.get("ts", 0)
        if ts > since:
            entries.append({"ts": ts, "dir": "in", "text": m.get("text", ""),
                            "from": m.get("from_name")})
    entries.sort(key=lambda e: e.get("ts", 0))
    return entries


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

  #log {
    flex: 1; overflow-y: auto;
    padding: 14px 16px;
    display: flex; flex-direction: column; gap: 10px;
  }
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
  <span class="tag" id="stat"></span>
</header>
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
  var lastTs = 0;
  var seen = {};   // ts+dir+text de-dup across polls

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
  // the fabric can become markup.
  function renderBody(parent, text) {
    var parts = String(text).split("```");
    for (var i = 0; i < parts.length; i++) {
      if (!parts[i]) continue;
      if (i % 2 === 1) parent.appendChild(el("div", "code", parts[i].replace(/^[a-z]*\n/, "")));
      else parent.appendChild(document.createTextNode(parts[i]));
    }
  }
  function addMsg(e) {
    var key = e.ts + "|" + e.dir + "|" + e.text;
    if (seen[key]) return;
    seen[key] = 1;
    var m = el("div", "msg " + (e.dir === "out" ? "out" : "in"));
    var meta = [];
    meta.push(e.dir === "out" ? "you" : (e.from || target));
    meta.push(ago(e.ts));
    if (e.dir === "out" && e.routed) meta.push(e.routed === "live" ? "delivered live" : e.routed);
    m.appendChild(el("div", "meta", meta.join(" · ")));
    var b = el("div", "body");
    renderBody(b, e.text);
    m.appendChild(b);
    log.appendChild(m);
    if (e.ts > lastTs) lastTs = e.ts;
    log.scrollTop = log.scrollHeight;
  }

  function poll() {
    if (document.hidden) return;
    fetch("/api/conv/" + encodeURIComponent(target) + "?since=" + lastTs,
          { headers: { "X-Homi-Token": token }, cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d) return;
        (d.entries || []).forEach(addMsg);
        stat.textContent = "";
      })
      .catch(function () { stat.textContent = "disconnected"; });
  }

  document.getElementById("composer").addEventListener("submit", function (ev) {
    ev.preventDefault();
    var text = inp.value.trim();
    if (!text) return;
    snd.disabled = true;
    fetch("/api/send", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Homi": "1",
                 "X-Homi-Token": token },
      body: JSON.stringify({ to: target, text: text }),
    }).then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (d && d.entry) { addMsg(d.entry); inp.value = ""; }
        else stat.textContent = "send failed";
      })
      .catch(function () { stat.textContent = "send failed"; })
      .then(function () { snd.disabled = false; inp.focus(); });
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
