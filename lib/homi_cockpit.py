"""The cockpit: a phone rendered as a surface you can operate.

The board shows the fabric; talk lets you speak to an agent; this shows the
DEVICE — its screen, its inbox, and the record of what the agent has done to
it — and lets you reach in and touch it.

Three panes, in the order you actually need them:

  screen   the phone's display, tappable. Tapping here taps there, and the
           screen re-reads itself afterwards, so what you see is what is,
           not what you hoped happened.
  inbox    every app's notifications. This works even when the screen pane
           does not: reading the inbox needs only termux-api, while seeing
           and touching need adb, which Android switches off on a whim. The
           page says which tier is alive rather than pretending.
  ledger   what the agent did. Full two-way agency is safe because it is
           accountable, and this is where the account is rendered.
"""


def render_cockpit(device, token):
    return (COCKPIT_TEMPLATE
            .replace("__TOKEN__", token)
            .replace("__DEVICE__", device))


COCKPIT_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#090909">
<link rel="manifest" href="/manifest.webmanifest">
<link rel="apple-touch-icon" href="/icon-192.png">
<meta name="mobile-web-app-capable" content="yes">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; manifest-src 'self'; worker-src 'self'; base-uri 'none'; form-action 'none'">
<meta name="referrer" content="no-referrer">
<title>__DEVICE__ · cockpit</title>
<style>
:root{
  --plane:#090909; --surface:#131312; --surface-2:#1b1b19;
  --ink:#f6f5f1; --ink-2:#bab8b0; --muted:#898781;
  --grid:#262624; --baseline:#36352f; --border:rgba(255,255,255,.075);
  --good:#0ca30c; --warn:#fab219; --crit:#d03b3b; --accent:#7aa2f7;
  --mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace;
  --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Inter,system-ui,sans-serif;
}
*{box-sizing:border-box}
html,body{margin:0;padding:0;background:var(--plane);color:var(--ink)}
body{
  font-family:var(--sans); font-size:14px; line-height:1.45;
  letter-spacing:-.006em;
  padding:calc(10px + env(safe-area-inset-top)) 12px
          calc(14px + env(safe-area-inset-bottom));
  max-width:520px; margin:0 auto;
}
header{
  display:flex; align-items:baseline; gap:8px;
  border-bottom:1px solid var(--baseline); padding-bottom:8px; margin-bottom:12px;
}
h1{font:600 13px/1.2 var(--mono); letter-spacing:.02em; margin:0; text-transform:uppercase}
.tag{font:400 11px/1.2 var(--mono); color:var(--muted); margin-left:auto}
.dot{width:6px;height:6px;border-radius:50%;background:var(--baseline);display:inline-block}
.dot.on{background:var(--good)} .dot.off{background:var(--crit)}
section{margin-bottom:16px}
.lbl{
  font:500 10px/1 var(--mono); letter-spacing:.09em; text-transform:uppercase;
  color:var(--muted); display:flex; align-items:center; gap:6px; margin-bottom:6px;
}
.lbl .rule{flex:1;height:1px;background:var(--grid)}
.screenwrap{
  position:relative; background:var(--surface); border:1px solid var(--border);
  min-height:120px; display:flex; align-items:center; justify-content:center;
}
#shot{display:block; width:100%; height:auto; cursor:crosshair}
#shot.stale{opacity:.45}
.empty{font:400 12px/1.5 var(--mono); color:var(--muted); padding:26px 16px; text-align:center}
.bar{display:flex; gap:6px; margin-top:8px; flex-wrap:wrap}
button{
  font:500 11px/1 var(--mono); letter-spacing:.05em; text-transform:uppercase;
  color:var(--ink-2); background:var(--surface); border:1px solid var(--border);
  padding:9px 12px; cursor:pointer; -webkit-tap-highlight-color:transparent;
}
button:hover{background:var(--surface-2); color:var(--ink)}
button:active{background:var(--baseline)}
button[disabled]{opacity:.4; cursor:default}
.row{
  display:grid; grid-template-columns:42px 74px 1fr; gap:8px;
  padding:6px 0; border-bottom:1px solid var(--grid);
  font-size:13px; align-items:baseline;
}
.row:last-child{border-bottom:0}
.row .t{font:400 11px/1.4 var(--mono); color:var(--muted)}
.row .a{font:400 11px/1.4 var(--mono); color:var(--ink-2); overflow:hidden; text-overflow:ellipsis}
.row .b{color:var(--ink-2); overflow:hidden}
.row .b b{color:var(--ink); font-weight:600}
.row.act .t::after{content:" *"; color:var(--accent)}
#note{font:400 11px/1.4 var(--mono); color:var(--muted); min-height:16px; margin-top:6px}
#note.warn{color:var(--warn)} #note.crit{color:var(--crit)}
.ping{position:absolute; width:16px; height:16px; margin:-8px 0 0 -8px;
      border:1px solid var(--accent); border-radius:50%; pointer-events:none;
      animation:ping .5s ease-out forwards}
@keyframes ping{from{transform:scale(.3);opacity:1}to{transform:scale(1.6);opacity:0}}
@media (prefers-reduced-motion:reduce){.ping{animation:none;opacity:0}}
</style>
</head>
<body>
<header>
  <h1>__DEVICE__</h1>
  <span class="tag"><span class="dot" id="tier"></span> <span id="tiertxt">checking</span></span>
</header>

<section>
  <div class="lbl">screen <span class="rule"></span><span id="shotage"></span></div>
  <div class="screenwrap">
    <img id="shot" alt="the device screen" hidden>
    <div class="empty" id="shotempty">no screen yet</div>
  </div>
  <div class="bar">
    <button id="refresh">refresh</button>
    <button id="back">back</button>
    <button id="home">home</button>
    <button id="auto">auto: off</button>
  </div>
  <div id="note"></div>
</section>

<section>
  <div class="lbl">inbox <span class="rule"></span><span id="ncount"></span></div>
  <div id="inbox"><div class="empty">—</div></div>
</section>

<section>
  <div class="lbl">ledger <span class="rule"></span>what the agent did</div>
  <div id="ledger"><div class="empty">—</div></div>
</section>

<script>
(function () {
  "use strict";
  var TOKEN = "__TOKEN__";
  var DEV = "__DEVICE__";
  var api = function (verb, qs) {
    return "/api/device/" + encodeURIComponent(DEV) + "/" + verb +
           (qs ? "?" + qs : "");
  };
  var H = { "X-Homi-Token": TOKEN };
  var shot = document.getElementById("shot");
  var shotEmpty = document.getElementById("shotempty");
  var note = document.getElementById("note");
  var tier = document.getElementById("tier");
  var tierTxt = document.getElementById("tiertxt");
  var shotAge = document.getElementById("shotage");
  var autoBtn = document.getElementById("auto");
  var autoOn = false, timer = null, lastShot = 0;

  function say(msg, cls) {
    note.textContent = msg || "";
    note.className = cls || "";
  }

  // The two tiers fail independently, and saying which one is down is the
  // difference between "broken" and "the phone turned adb off again".
  function setTier(screenOk, inboxOk) {
    if (screenOk) { tier.className = "dot on"; tierTxt.textContent = "screen + inbox"; }
    else if (inboxOk) { tier.className = "dot"; tierTxt.textContent = "inbox only (adb down)"; }
    else { tier.className = "dot off"; tierTxt.textContent = "unreachable"; }
  }

  function loadShot() {
    var url = api("screen", "t=" + Date.now());
    // Fetched rather than set as src so the token travels in a header and
    // never sits in a URL (or a browser history entry).
    return fetch(url, { headers: H, cache: "no-store" })
      .then(function (r) {
        if (!r.ok) throw new Error("screen " + r.status);
        return r.blob();
      })
      .then(function (b) {
        var old = shot.src;
        shot.src = URL.createObjectURL(b);
        shot.hidden = false;
        shot.classList.remove("stale");
        shotEmpty.hidden = true;
        if (old && old.indexOf("blob:") === 0) URL.revokeObjectURL(old);
        lastShot = Date.now();
        say("");
        return true;
      })
      .catch(function () {
        shot.classList.add("stale");
        if (!shot.src) { shotEmpty.hidden = false;
                         shotEmpty.textContent = "screen unavailable — adb is down"; }
        say("cannot see the screen (adb is off; the inbox still works)", "warn");
        return false;
      });
  }

  function tapAt(ev) {
    if (!shot.src) return;
    var r = shot.getBoundingClientRect();
    var sx = shot.naturalWidth / r.width;
    var sy = shot.naturalHeight / r.height;
    var x = Math.round((ev.clientX - r.left) * sx);
    var y = Math.round((ev.clientY - r.top) * sy);
    var p = document.createElement("div");
    p.className = "ping";
    p.style.left = (ev.clientX - r.left) + "px";
    p.style.top = (ev.clientY - r.top) + "px";
    shot.parentNode.appendChild(p);
    setTimeout(function () { p.remove(); }, 600);
    say("tap " + x + "," + y + " …");
    fetch(api("tap", "x=" + x + "&y=" + y), { headers: H })
      .then(function (r) { if (!r.ok) throw new Error(); })
      // Re-read after acting. Never assume the tap landed — believing an
      // action worked when it did not is the classic phone-agent failure.
      .then(function () { return new Promise(function (s) { setTimeout(s, 700); }); })
      .then(loadShot)
      .catch(function () { say("tap failed", "crit"); });
  }

  function key(code) {
    say(code.toLowerCase() + " …");
    fetch(api("key", code), { headers: H })
      .then(function () { return new Promise(function (s) { setTimeout(s, 600); }); })
      .then(loadShot)
      .catch(function () { say("key failed", "crit"); });
  }

  function esc(t) { var d = document.createElement("div"); d.textContent = t == null ? "" : t; return d.innerHTML; }

  function loadInbox() {
    return fetch(api("notifs", "limit=8"), { headers: H, cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (rows) {
        var host = document.getElementById("inbox");
        if (!rows || !rows.length) {
          host.innerHTML = '<div class="empty">nothing waiting</div>';
          document.getElementById("ncount").textContent = "";
          return !!rows;
        }
        document.getElementById("ncount").textContent = rows.length;
        host.innerHTML = rows.map(function (n) {
          var app = (n.packageName || "?").split(".").pop();
          var when = (n.when || "").slice(11, 16);
          var body = n.content || (n.lines || []).join(" / ") || "";
          return '<div class="row"><span class="t">' + esc(when) +
                 '</span><span class="a">' + esc(app) +
                 '</span><span class="b"><b>' + esc(n.title || "") + '</b> ' +
                 esc(body) + '</span></div>';
        }).join("");
        return true;
      })
      .catch(function () { return false; });
  }

  function loadLedger() {
    fetch(api("log", "limit=12"), { headers: H, cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (rows) {
        var host = document.getElementById("ledger");
        if (!rows || !rows.length) {
          host.innerHTML = '<div class="empty">nothing done yet</div>';
          return;
        }
        host.innerHTML = rows.map(function (r) {
          var d = new Date((r.ts || 0) * 1000);
          var when = ("0" + d.getHours()).slice(-2) + ":" +
                     ("0" + d.getMinutes()).slice(-2);
          var det = typeof r.detail === "string" ? r.detail
                                                 : JSON.stringify(r.detail);
          return '<div class="row ' + (r.kind === "act" ? "act" : "") +
                 '"><span class="t">' + esc(when) +
                 '</span><span class="a">' + esc(r.verb) +
                 '</span><span class="b">' + esc(det) +
                 (r.result ? ' <b>' + esc(r.result) + '</b>' : "") +
                 '</span></div>';
        }).join("");
      })
      .catch(function () {});
  }

  function refreshAll() {
    Promise.all([loadShot(), loadInbox()]).then(function (r) {
      setTier(r[0], r[1]);
    });
    loadLedger();
  }

  document.getElementById("refresh").addEventListener("click", refreshAll);
  document.getElementById("back").addEventListener("click", function () { key("BACK"); });
  document.getElementById("home").addEventListener("click", function () { key("HOME"); });
  shot.addEventListener("click", tapAt);
  autoBtn.addEventListener("click", function () {
    autoOn = !autoOn;
    autoBtn.textContent = "auto: " + (autoOn ? "on" : "off");
    if (timer) { clearInterval(timer); timer = null; }
    // Polling a phone costs its battery, so this is opt-in and slow.
    if (autoOn) timer = setInterval(function () {
      if (!document.hidden) refreshAll();
    }, 6000);
  });
  setInterval(function () {
    if (!lastShot) { shotAge.textContent = ""; return; }
    var s = Math.round((Date.now() - lastShot) / 1000);
    shotAge.textContent = s < 3 ? "just now" : s + "s ago";
    if (s > 25) shot.classList.add("stale");
  }, 1000);
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) refreshAll();
  });
  refreshAll();
})();
</script>
</body>
</html>
"""
