"""Voice: the talk page as something you speak to.

Web Speech in, speech synthesis out, over the same
authenticated endpoints the typed talk page already uses.
The page carries the per-serve token like every other
read of fabric content, and nothing else.
"""


def render_voice(handle, target, token):
    return (VOICE_TEMPLATE
            .replace("__TOKEN__", token)
            .replace("__TARGET__", target))


VOICE_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="theme-color" content="#090909">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; form-action 'none'">
<meta name="referrer" content="no-referrer">
<title>__TARGET__ · voice</title>
<style>
  /* ── design tokens — lifted verbatim from lib/homi_talk.py TALK_TEMPLATE ──
     (same values also appear in lib/homi_board.py). Not re-derived: this
     prototype is meant to sit in the same visual family as talk/board. */
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
  html, body { height: 100%; }
  html { background: var(--plane); }
  body {
    background: var(--plane); color: var(--ink);
    font-family: var(--font-sans);
    font-size: 14px; line-height: 1.45; letter-spacing: -0.006em;
    -webkit-font-smoothing: antialiased;
    display: flex; justify-content: center;
  }
  :focus-visible { outline: 2px solid var(--ink); outline-offset: 2px; }

  /* phone-first column; centered + framed on a wide (desktop) viewport so
     the prototype still reads as deliberate outside a real handset. */
  .frame {
    width: 100%; max-width: 480px;
    height: 100vh; height: 100dvh;
    display: flex; flex-direction: column;
    background: var(--plane);
    position: relative;
    border-inline: 1px solid var(--grid);
  }

  header {
    flex: none;
    display: flex; align-items: center; gap: 10px;
    padding: 14px 16px 10px;
    border-bottom: 1px solid var(--grid);
    padding-top: calc(14px + env(safe-area-inset-top));
  }
  #dot { width: 8px; height: 8px; border-radius: 50%; flex: none;
         background: var(--baseline); transition: background .15s ease; }
  #dot.active { background: var(--status-good); }
  #dot.busy { background: var(--status-warn); }
  #dot.err { background: var(--status-critical); }
  header .who { font-size: 16px; font-weight: 500; letter-spacing: -0.02em; }
  header .tag { margin-left: auto; font-family: var(--font-mono);
                font-size: 10.5px; letter-spacing: 0.12em;
                text-transform: uppercase; color: var(--muted);
                font-variant-numeric: tabular-nums; }

  /* ── notifications: compact, always-visible, capped height ── */
  #notifSec { flex: none; border-bottom: 1px solid var(--grid); }
  details.ledger { margin: 0; }
  details.ledger summary {
    cursor: pointer; list-style: none; -webkit-tap-highlight-color: transparent;
    color: var(--muted); font-family: var(--font-mono); font-size: 10.5px;
    letter-spacing: 0.12em; text-transform: uppercase;
    padding: 8px 16px; display: flex; align-items: center; gap: 8px;
    font-variant-numeric: tabular-nums;
  }
  details.ledger summary::-webkit-details-marker { display: none; }
  details.ledger summary::before { content: "\25B8"; font-size: 9px; color: var(--baseline); }
  details.ledger[open] summary::before { content: "\25BE"; }
  #notifSec .n { margin-left: auto; color: var(--ink-2); }
  .notif-scroll { max-height: 132px; overflow-y: auto; padding: 0 16px 10px; }
  .nrow { padding: 7px 0; border-top: 1px solid var(--grid); }
  .nrow:first-child { border-top: none; }
  .nmeta { font-family: var(--font-mono); font-size: 10px; letter-spacing: 0.08em;
           text-transform: uppercase; color: var(--muted);
           font-variant-numeric: tabular-nums; }
  .ntitle { font-size: 13px; color: var(--ink); font-weight: 500; margin-top: 2px; }
  .nbody { font-size: 12.5px; color: var(--ink-2); margin-top: 1px;
           overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .empty { color: var(--muted); font-size: 12.5px; padding: 0 16px 10px; }

  /* ── transient status note (errors, hints) — same idiom as talk's #note ── */
  #note { display: flex; gap: 8px; align-items: center;
          padding: 8px 16px; color: var(--muted);
          font-family: var(--font-mono); font-size: 11px;
          letter-spacing: 0.06em; border-bottom: 1px solid var(--grid); flex: none; }
  #note::before { content: ""; width: 7px; height: 7px; border-radius: 50%;
                  background: var(--baseline); flex: none; }
  #note.warn::before { background: var(--status-warn); }
  #note.crit { color: var(--status-critical); }
  #note.crit::before { background: var(--status-critical); }
  #note[hidden] { display: none; }

  /* ── transcript ── */
  #log { flex: 1 1 auto; min-height: 0; overflow-y: auto;
         padding: 14px 16px; display: flex; flex-direction: column; gap: 10px; }
  .msg { max-width: 88%; }
  .msg .meta { font-family: var(--font-mono); font-size: 10px;
               letter-spacing: 0.12em; text-transform: uppercase;
               color: var(--muted); margin-bottom: 4px;
               font-variant-numeric: tabular-nums; }
  .msg .body { background: var(--surface); border: 1px solid var(--border);
               border-radius: 7px; padding: 9px 12px;
               white-space: pre-wrap; word-break: break-word; }
  .msg.out { align-self: flex-end; }
  .msg.out .meta { text-align: right; }
  .msg.out .body { background: var(--surface-2); }
  .msg.out.pending .body { border-style: dashed; border-color: var(--baseline); }
  .msg.out .interim { color: var(--muted); }
  .msg.in .body { color: var(--ink); }
  .sent { transition: background .12s ease, padding .12s ease; }
  .sent.active { background: var(--surface-2); border-left: 2px solid var(--ink);
                 padding: 0 0 0 6px; margin-left: -8px; border-radius: 2px; }
  .trn-mark { align-self: center; color: var(--muted);
              font-family: var(--font-mono); font-size: 10.5px;
              letter-spacing: 0.12em; text-transform: uppercase; padding: 4px 0; }
  /* the aria-live announcer is visually hidden, standard clip pattern */
  .vh { position: absolute; width: 1px; height: 1px; overflow: hidden;
        clip: rect(0 0 0 0); white-space: nowrap; }

  /* ── control dock — the composer-equivalent; pinned above the home
     indicator per spec item 6 ── */
  #dock { flex: none; border-top: 1px solid var(--grid);
          padding: 14px 16px calc(16px + env(safe-area-inset-bottom));
          display: flex; flex-direction: column; align-items: center; gap: 10px; }

  .dial-wrap { display: flex; flex-direction: column; align-items: center; gap: 10px; }
  .dial { position: relative; width: 208px; height: 208px; flex: none; }
  .dial-bezel { position: absolute; inset: 2px; border: 1px solid var(--grid);
                border-radius: 50%; }
  .tick { position: absolute; inset: 0; }
  .tick .mark { position: absolute; top: 4px; left: 50%; width: 2px; height: 10px;
                margin-left: -1px; background: var(--baseline); border-radius: 1px; }
  .dial-core {
    position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%);
    width: 140px; height: 140px; border-radius: 50%;
    background: var(--surface); border: 1px solid var(--border);
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    gap: 8px; cursor: pointer; -webkit-tap-highlight-color: transparent;
    transition: border-color .15s ease, background .15s ease;
    font-family: inherit; color: inherit;
  }
  .dial-core:hover:not(:disabled) { background: var(--surface-2); }
  .dial-core:active:not(:disabled) { background: var(--surface-2); }
  .dial-core:disabled { cursor: default; opacity: .45; }
  .dial[data-state="listening"] .dial-core { border-color: var(--status-good); }
  .dial[data-state="speaking"] .dial-core { border-color: var(--ink-2); }
  .dial-icon { width: 32px; height: 32px; color: var(--ink-2); }
  .dial-icon svg { width: 100%; height: 100%; display: block; }
  .dial[data-state="listening"] .dial-icon,
  .dial[data-state="speaking"] .dial-icon { color: var(--ink); }
  .dial[data-state="thinking"] .dial-icon { color: var(--muted); }
  .dial-label { font-family: var(--font-mono); font-size: 11px;
                letter-spacing: 0.12em; text-transform: uppercase; color: var(--ink); }
  .dial-hint { font-family: var(--font-mono); font-size: 10.5px;
               letter-spacing: 0.08em; text-transform: uppercase;
               color: var(--muted); min-height: 14px; text-align: center; }
  .seam { font-family: var(--font-mono); font-size: 9.5px; letter-spacing: 0.1em;
          text-transform: uppercase; color: var(--baseline); text-align: center; }

  @media (prefers-reduced-motion: reduce) {
    .tick .mark { transition: none; }
  }
</style>
</head>
<body>
<div class="frame">
  <header>
    <span id="dot"></span>
    <span class="who">voice</span>
    <span class="tag" id="statTag">READY</span>
  </header>

  <section id="notifSec">
    <details class="ledger" open>
      <summary>notifications <span class="n" id="notifCount"></span></summary>
      <div class="notif-scroll" id="notifList"></div>
    </details>
  </section>

  <div id="note" hidden></div>

  <div id="log" aria-hidden="false"></div>
  <div class="vh" id="live" aria-live="polite"></div>

  <div id="dock">
    <div class="dial-wrap">
      <div class="dial" id="dial" data-state="idle">
        <div class="dial-bezel"></div>
        <!-- tick marks injected by JS -->
        <button id="talkBtn" class="dial-core" type="button"
                aria-pressed="false" aria-label="Press to talk">
          <span class="dial-icon" id="dialIcon"></span>
          <span class="dial-label" id="dialLabel">TAP TO TALK</span>
        </button>
      </div>
      <div class="dial-hint" id="dialHint">idle</div>
    </div>
    <div class="seam">mock backend — sendToAgent()</div>
  </div>
</div>

<script>
  var TOKEN = "__TOKEN__";
  var TARGET = "__TARGET__";
</script>
<script>
(function () {
  "use strict";

  // ────────────────────────────────────────────────────────────────────
  // BACKEND SEAM — the only function that should change to go from
  // prototype to real. It takes the finalized user utterance and must
  // resolve with the agent's reply text. Swap the body for a real call,
  // e.g.:
  //
  //   // ── the real brain: the fabric ──────────────────────────────────
  // Send on the same authenticated write path the talk page uses, then wait
  // for the agent's reply to appear on the shared timeline. The reply is not
  // the POST's response: the agent may think for many seconds, and on a
  // phone-resident agent it often does. So we send, then watch.
  var replyCursor = 0;          // only consider messages newer than our send

  function postTurn(text) {
    return fetch("/api/send", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Homi": "1",
                 "X-Homi-Token": TOKEN },
      body: JSON.stringify({ to: TARGET, text: text })
    }).then(function (r) {
      if (r.status === 403) throw new Error("session expired — reload");
      if (!r.ok) throw new Error("send failed (" + r.status + ")");
      return r.json();
    }).then(function (d) {
      if (d && d.entry && d.entry.ts) replyCursor = d.entry.ts;
      return d;
    });
  }

  function pollForReply(deadline) {
    return fetch("/api/timeline/" + encodeURIComponent(TARGET)
                 + "?ts_after=" + replyCursor,
                 { headers: { "X-Homi-Token": TOKEN }, cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        var items = (d && d.items) || [];
        for (var i = 0; i < items.length; i++) {
          var it = items[i];
          // Ours went out as role "user"; anything else on the mail plane is
          // the agent answering.
          if (it.via === "mail" && it.role !== "user" && it.text) {
            return it.text;
          }
          if (it.ts && it.ts > replyCursor) replyCursor = it.ts;
        }
        if (Date.now() > deadline) {
          throw new Error("no answer yet — it may still be thinking");
        }
        return new Promise(function (res) {
          setTimeout(function () { res(pollForReply(deadline)); }, 1500);
        });
      });
  }

  function sendToAgent(text) {
    return postTurn(text).then(function () {
      return pollForReply(Date.now() + 180000);   // agents on a phone are slow
    });
  }

  // ── notifications: fetched live from the device ──
  var MOCK_NOTIFICATIONS = [];


  function ago(ts) {
    var d = Math.max(0, Math.floor((Date.now() - ts) / 1000));
    if (d < 8) return "just now";
    if (d < 60) return d + "s ago";
    if (d < 3600) return Math.floor(d / 60) + "m ago";
    if (d < 86400) return Math.floor(d / 3600) + "h ago";
    return Math.floor(d / 86400) + "d ago";
  }

  function el(t, c, x) {
    var n = document.createElement(t);
    if (c) n.className = c;
    if (x != null) n.textContent = x;
    return n;
  }

  function renderNotifications() {
    var host = document.getElementById("notifList");
    var count = document.getElementById("notifCount");
    host.textContent = "";
    if (!MOCK_NOTIFICATIONS.length) {
      host.appendChild(el("div", "empty", "no recent notifications"));
      count.textContent = "0";
      return;
    }
    count.textContent = MOCK_NOTIFICATIONS.length;
    MOCK_NOTIFICATIONS.forEach(function (n) {
      var row = el("div", "nrow");
      row.appendChild(el("div", "nmeta", n.app.toUpperCase() + " · " + ago(n.when)));
      row.appendChild(el("div", "ntitle", n.title));
      row.appendChild(el("div", "nbody", n.body));
      host.appendChild(row);
    });
  }
  renderNotifications();

  // ── icons (inline SVG line-art, no emoji) ────────────────────────
  var ICONS = {
    mic: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="3" width="6" height="11" rx="3"></rect><path d="M5 11a7 7 0 0 0 14 0"></path><path d="M12 18v3"></path><path d="M8.5 21h7"></path></svg>',
    micLive: '<svg viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="3" width="6" height="11" rx="3"></rect><path d="M5 11a7 7 0 0 0 14 0" fill="none"></path><path d="M12 18v3"></path><path d="M8.5 21h7"></path></svg>',
    dots: '<svg viewBox="0 0 24 24" fill="currentColor"><circle cx="6" cy="12" r="1.7"></circle><circle cx="12" cy="12" r="1.7"></circle><circle cx="18" cy="12" r="1.7"></circle></svg>',
    bars: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><path d="M5 10v4"></path><path d="M9 7v10"></path><path d="M13 4v16"></path><path d="M17 8v8"></path><path d="M21 11v2"></path></svg>',
    warn: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"></circle><path d="M12 8v5"></path><circle cx="12" cy="16.2" r="0.9" fill="currentColor" stroke="none"></circle></svg>'
  };

  // ── dial: a ring of tick marks (radial VU-style gauge) around the
  // press-to-talk core. Ticks are painted every animation frame based on
  // the current state; see paint*() below. ──────────────────────────
  var TICKS = 40;
  var dial = document.getElementById("dial");
  var ticks = [];
  (function buildTicks() {
    for (var i = 0; i < TICKS; i++) {
      var t = el("span", "tick");
      t.style.transform = "rotate(" + (i * 360 / TICKS) + "deg)";
      var mark = el("span", "mark");
      t.appendChild(mark);
      dial.insertBefore(t, dial.querySelector(".dial-core"));
      ticks.push(mark);
    }
  })();

  var talkBtn = document.getElementById("talkBtn");
  var dialIcon = document.getElementById("dialIcon");
  var dialLabel = document.getElementById("dialLabel");
  var dialHint = document.getElementById("dialHint");
  var statTag = document.getElementById("statTag");
  var dot = document.getElementById("dot");
  var log = document.getElementById("log");
  var note = document.getElementById("note");
  var liveRegion = document.getElementById("live");

  // ── feature detection — every degraded path is shown honestly, never
  // a silent dead button. ───────────────────────────────────────────
  var SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  var recognitionSupported = !!SR;
  var synthSupported = "speechSynthesis" in window;
  var secureOk = window.isSecureContext !== false;

  var recognition = null;
  if (recognitionSupported) {
    recognition = new SR();
    recognition.lang = "en-US";
    recognition.continuous = false;
    recognition.interimResults = true;
    recognition.maxAlternatives = 1;
  }

  var state = "idle";          // idle | listening | thinking | speaking
  var genCounter = 0;          // invalidated on interrupt / new turn
  var finalText = "";
  var liveUserEl = null;       // in-progress "you" bubble while listening

  function announce(text) { liveRegion.textContent = text; }

  function noteTransient(text, kind) {
    note.className = kind || "";
    note.textContent = text;
    note.hidden = false;
    window.clearTimeout(noteTransient._t);
    noteTransient._t = window.setTimeout(function () {
      note.hidden = true;
    }, 4200);
  }

  function scrollLog() { log.scrollTop = log.scrollHeight; }

  function buildTurn(cls, metaText) {
    var wrap = el("div", "msg " + cls);
    wrap.appendChild(el("div", "meta", metaText));
    var body = el("div", "body");
    wrap.appendChild(body);
    return { wrap: wrap, body: body };
  }

  // user speech in progress: finalized words solid, trailing interim
  // words greyed, updated in place (spec item 2).
  function renderUserTurn(finalPart, interimPart) {
    if (!liveUserEl) {
      var t = buildTurn("out pending", "YOU · listening");
      var fin = el("span", "final");
      var intr = el("span", "interim");
      t.body.appendChild(fin);
      t.body.appendChild(intr);
      liveUserEl = { wrap: t.wrap, meta: t.wrap.querySelector(".meta"), final: fin, interim: intr };
      log.appendChild(liveUserEl.wrap);
    }
    liveUserEl.final.textContent = finalPart;
    liveUserEl.interim.textContent = interimPart ? (finalPart ? " " : "") + interimPart : "";
    scrollLog();
  }

  function commitUserTurn(text) {
    if (liveUserEl) {
      liveUserEl.final.textContent = text;
      liveUserEl.interim.textContent = "";
      liveUserEl.meta.textContent = "YOU · " + clock();
      liveUserEl.wrap.className = "msg out";
      liveUserEl = null;
    } else {
      var t = buildTurn("out", "YOU · " + clock());
      t.body.textContent = text;
      log.appendChild(t.wrap);
    }
    scrollLog();
  }

  function discardLiveUserTurn() {
    if (liveUserEl) { liveUserEl.wrap.remove(); liveUserEl = null; }
  }

  var sentenceEls = [];
  function renderAgentTurn(text, chunks) {
    var t = buildTurn("in", "AGENT · " + clock());
    sentenceEls = [];
    chunks.forEach(function (c, i) {
      var s = el("span", "sent", c + (i < chunks.length - 1 ? " " : ""));
      t.body.appendChild(s);
      sentenceEls.push(s);
    });
    log.appendChild(t.wrap);
    scrollLog();
  }

  function highlightSentence(i) {
    sentenceEls.forEach(function (s, idx) {
      if (idx === i) s.classList.add("active");
      else s.classList.remove("active");
    });
  }
  function clearHighlight() {
    sentenceEls.forEach(function (s) { s.classList.remove("active"); });
  }

  function clock() {
    var d = new Date();
    return ("0" + d.getHours()).slice(-2) + ":" + ("0" + d.getMinutes()).slice(-2);
  }

  // ── state machine ──────────────────────────────────────────────
  function setState(s) {
    state = s;
    dial.dataset.state = s;
    talkBtn.setAttribute("aria-pressed", s === "listening" ? "true" : "false");
    var label, hint, tag, dotCls, iconKey, live;
    switch (s) {
      case "listening":
        label = "LISTENING…"; hint = "tap to stop"; tag = "LISTENING";
        dotCls = "active"; iconKey = "micLive"; live = "listening.";
        break;
      case "thinking":
        label = "THINKING…"; hint = "working"; tag = "THINKING";
        dotCls = "busy"; iconKey = "dots"; live = "thinking.";
        break;
      case "speaking":
        label = "SPEAKING…"; hint = "tap to interrupt"; tag = "SPEAKING";
        dotCls = "active"; iconKey = "bars"; live = "speaking reply.";
        break;
      default:
        s = "idle";
        label = "TAP TO TALK"; hint = "idle"; tag = "READY";
        dotCls = ""; iconKey = "mic"; live = "ready.";
    }
    dialLabel.textContent = label;
    dialHint.textContent = hint;
    statTag.textContent = tag;
    dot.className = dotCls;
    dialIcon.innerHTML = ICONS[iconKey];
    announce(live);
  }

  function fatal(msg) {
    talkBtn.disabled = true;
    dialLabel.textContent = "UNAVAILABLE";
    dialHint.textContent = "voice input disabled";
    dialIcon.innerHTML = ICONS.warn;
    noteTransient(msg, "crit");
  }

  if (!recognitionSupported) {
    fatal("speech recognition isn't supported in this browser.");
  } else if (!secureOk) {
    fatal("voice input needs HTTPS (or localhost).");
  }

  // ── mic level metering for the listening ring. Real amplitude via
  // getUserMedia + AnalyserNode when permitted; a gentle synthetic
  // breathing pattern otherwise so "listening" never looks dead. This is
  // ENTIRELY separate from speech recognition — it exists only to drive
  // the visual, and recognition still works even if this fails. ──────
  var micStream = null, audioCtx = null, analyser = null, dataArr = null;
  var meterReal = false;

  function startMic() {
    if (!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia)) return;
    navigator.mediaDevices.getUserMedia({ audio: true }).then(function (stream) {
      if (state !== "listening") { stream.getTracks().forEach(function (tr) { tr.stop(); }); return; }
      micStream = stream;
      var Ctx = window.AudioContext || window.webkitAudioContext;
      audioCtx = new Ctx();
      var src = audioCtx.createMediaStreamSource(stream);
      analyser = audioCtx.createAnalyser();
      analyser.fftSize = 256;
      src.connect(analyser);
      dataArr = new Uint8Array(analyser.fftSize);
      meterReal = true;
    }).catch(function () {
      meterReal = false; // permission denied or no device — synthetic fallback carries on
    });
  }

  function stopMic() {
    meterReal = false;
    if (micStream) { micStream.getTracks().forEach(function (tr) { tr.stop(); }); micStream = null; }
    if (audioCtx) { try { audioCtx.close(); } catch (e) {} audioCtx = null; }
    analyser = null; dataArr = null;
  }

  // ── ring painters — one visual language per state, so the control is
  // legible without relying on color: idle is static, listening reacts
  // to real audio, thinking chases, speaking waves. ───────────────────
  function resetTicks() {
    for (var i = 0; i < ticks.length; i++) { ticks[i].style.background = ""; ticks[i].style.opacity = ""; }
  }
  function paintIdle() { resetTicks(); }

  var smoothLevel = 0;
  function paintListening(now) {
    var level;
    if (meterReal && analyser) {
      analyser.getByteTimeDomainData(dataArr);
      var sum = 0;
      for (var i = 0; i < dataArr.length; i++) { var v = (dataArr[i] - 128) / 128; sum += v * v; }
      level = Math.min(1, Math.sqrt(sum / dataArr.length) * 5);
    } else {
      level = 0.16 + 0.09 * Math.sin(now / 260); // no mic access: honest, gentle placeholder motion
    }
    smoothLevel = smoothLevel * 0.72 + level * 0.28;
    var lit = Math.round(smoothLevel * TICKS);
    resetTicks();
    for (var j = 0; j < lit; j++) {
      ticks[j].style.background = "var(--status-good)";
    }
  }

  function paintThinking(now) {
    var head = Math.floor(now / 70) % TICKS;
    resetTicks();
    for (var i = 0; i < TICKS; i++) {
      var d1 = Math.abs(i - head);
      var dist = Math.min(d1, TICKS - d1);
      if (dist <= 3) {
        ticks[i].style.background = "var(--ink)";
        ticks[i].style.opacity = String(1 - dist / 4.5);
      }
    }
  }

  function paintSpeaking(now) {
    resetTicks();
    for (var i = 0; i < TICKS; i++) {
      var phase = (i / TICKS) * Math.PI * 2;
      var val = (Math.sin(phase * 3 - now * 0.004) + 1) / 2;
      if (val > 0.55) {
        ticks[i].style.background = "var(--ink-2)";
        ticks[i].style.opacity = String(val);
      }
    }
  }

  function frame(now) {
    if (state === "listening") paintListening(now);
    else if (state === "thinking") paintThinking(now);
    else if (state === "speaking") paintSpeaking(now);
    else paintIdle();
    window.requestAnimationFrame(frame);
  }
  window.requestAnimationFrame(frame);

  // ── speech recognition wiring ──────────────────────────────────
  function mapSpeechError(code) {
    switch (code) {
      case "no-speech": return "no speech detected — tap to try again";
      case "aborted": return "listening stopped";
      case "audio-capture": return "no microphone found";
      case "not-allowed": return "microphone permission denied";
      case "network": return "speech service unreachable — check connection";
      case "service-not-allowed": return "speech service blocked";
      default: return "speech error: " + code;
    }
  }

  if (recognition) {
    recognition.onresult = function (ev) {
      var interim = "", fin = "";
      for (var i = ev.resultIndex; i < ev.results.length; i++) {
        var r = ev.results[i];
        if (r.isFinal) fin += r[0].transcript;
        else interim += r[0].transcript;
      }
      renderUserTurn(fin, interim);
      if (fin) finalText = fin.trim();
    };
    recognition.onerror = function (ev) {
      // Known noisy Android/Chrome codes are normal control flow, not a
      // silent failure — always surface something honest on screen.
      // onend still fires after onerror per spec, so the state
      // transition happens there uniformly.
      noteTransient(mapSpeechError(ev.error),
        (ev.error === "not-allowed" || ev.error === "network") ? "crit" : "warn");
    };
    recognition.onend = function () {
      stopMic();
      if (state !== "listening") return; // already moved on elsewhere
      if (finalText) {
        var text = finalText;
        finalText = "";
        commitUserTurn(text);
        askAgent(text);
      } else {
        discardLiveUserTurn();
        setState("idle");
      }
    };
  }

  function beginListening() {
    if (!recognitionSupported || !secureOk) return;
    try { recognition.start(); }        // must be called from a real tap handler
    catch (e) { return; }               // already running — ignore double-taps
    finalText = "";
    setState("listening");
    startMic();
  }

  function stopListening() {
    try { recognition.stop(); } catch (e) {}
    // onend fires naturally and decides idle vs. thinking from finalText.
  }

  // ── reply chunking + queued speechSynthesis playback ────────────
  // Chrome silently truncates a single SpeechSynthesisUtterance at
  // roughly 15s. We never find that out from an event — the audio just
  // stops — so instead of trusting duration we bound every utterance by
  // a conservative character budget and chain them with onend, which
  // keeps each individual utterance safely under the cutoff regardless
  // of voice/rate and reproduces the appearance of one continuous reply.
  var CHUNK_MAX = 170;
  function chunkText(text) {
    var raw = String(text || "").trim();
    if (!raw) return [];
    var sentences = raw.match(/[^.!?]+[.!?]+(\s+|$)|[^.!?]+$/g) || [raw];
    var chunks = [];
    sentences.forEach(function (s) {
      s = s.trim();
      if (!s) return;
      if (s.length <= CHUNK_MAX) { chunks.push(s); return; }
      // Long sentence with no usable punctuation: fall back to clause
      // (comma) splits, then a hard word-boundary wrap as a last resort.
      var parts = s.split(/,\s+/), buf = "";
      parts.forEach(function (p, i) {
        var piece = p + (i < parts.length - 1 ? "," : "");
        if ((buf + " " + piece).trim().length > CHUNK_MAX && buf) {
          chunks.push(buf.trim()); buf = piece;
        } else buf = (buf ? buf + " " : "") + piece;
      });
      if (buf.trim()) {
        if (buf.trim().length > CHUNK_MAX) {
          var words = buf.trim().split(/\s+/), line = "";
          words.forEach(function (w) {
            if ((line + " " + w).trim().length > CHUNK_MAX && line) {
              chunks.push(line.trim()); line = w;
            } else line = (line ? line + " " : "") + w;
          });
          if (line.trim()) chunks.push(line.trim());
        } else chunks.push(buf.trim());
      }
    });
    return chunks;
  }

  function askAgent(text) {
    setState("thinking");
    var myGen = ++genCounter;
    sendToAgent(text).then(function (reply) {
      if (myGen !== genCounter) return; // interrupted / superseded
      var chunks = chunkText(reply);
      renderAgentTurn(reply, chunks);
      speakChunks(chunks, myGen);
    }).catch(function () {
      if (myGen !== genCounter) return;
      noteTransient("agent error — try again", "crit");
      setState("idle");
    });
  }

  function speakChunks(chunks, myGen) {
    if (!synthSupported || !chunks.length) { setState("idle"); return; }
    setState("speaking");
    var idx = 0;
    function next() {
      if (myGen !== genCounter) return; // cancelled by interrupt or a new turn
      if (idx >= chunks.length) { clearHighlight(); setState("idle"); return; }
      var u = new SpeechSynthesisUtterance(chunks[idx]);
      u.rate = 1; u.pitch = 1;
      var mine = idx;
      u.onstart = function () { if (myGen === genCounter) highlightSentence(mine); };
      u.onend = function () { idx++; next(); };
      u.onerror = function () { idx++; next(); }; // one bad chunk shouldn't stall the reply
      window.speechSynthesis.speak(u);
    }
    next();
  }

  function interruptSpeaking() {
    genCounter++; // invalidate any in-flight onend/askAgent callbacks
    clearHighlight();
    try { window.speechSynthesis.cancel(); } catch (e) {}
    setState("idle");
  }

  // ── the one control, four states, one tap handler ───────────────
  talkBtn.addEventListener("click", function () {
    if (state === "idle") beginListening();
    else if (state === "listening") stopListening();
    else if (state === "speaking") interruptSpeaking();
    else if (state === "thinking") noteTransient("still thinking — one moment", "");
  });

  setState("idle");
})();
</script>
</body>
</html>
"""
