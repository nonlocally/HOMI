// Behavioral test: the talk page's restart-replay contract, executed for
// real. A session restart (sid change) must rebuild the WHOLE pane —
// including the mail thread — which requires the next request to go out
// with ts_after=0. A source-grep cannot see this; running the page can.
const { talkJS, newPage, installFetch, flush } = require("./timeline-dom-harness.js");

let pass = 0, fail = 0;
const ok = m => { pass++; console.log("ok   " + m); };
const bad = m => { fail++; console.log("FAIL " + m); };

function qs(url) {
  const o = {};
  for (const kv of (url.split("?")[1] || "").split("&")) {
    const [k, v] = kv.split("=");
    if (k) o[k] = decodeURIComponent(v || "");
  }
  return o;
}

(async () => {
  const p = newPage({ handle: "alice", target: "scout", token: "T" });
  const urls = [];
  const HISTORY = [
    { via: "mail", role: "user", ts: 900.25, text: "hello agent", routed: "live" },
    { via: "session", role: "assistant", i: 3, ts: 950, text: "on it" },
    { via: "mail", role: "in", ts: 1000.5, text: "done", who: "scout" },
  ];
  let call = 0;
  installFetch((url) => {
    call++;
    const q = qs(url);
    const tsAfter = parseFloat(q.ts_after || "0");
    if (call === 1) {
      return { ok: true, live: true, present: true, sid: "sid-A", truncated: false,
               t_cursor: 4, ts_cursor: 1000.5, t_min: 3, items: HISTORY };
    }
    if (call === 2) {
      // Restart: the request still carries OLD cursors; honest server echoes
      // ts_cursor from the ts_after it was actually asked with.
      return { ok: true, live: true, present: true, sid: "sid-B", truncated: false,
               t_cursor: parseInt(q.t_after || "-1", 10), ts_cursor: tsAfter,
               t_min: null, items: [] };
    }
    // Any later request: honest replay — full history only if asked from 0.
    const items = HISTORY.filter(it => (it.via === "mail" ? it.ts > tsAfter
                                        : q.t_after === undefined));
    return { ok: true, live: true, present: true, sid: "sid-B", truncated: false,
             t_cursor: 4, ts_cursor: Math.max(tsAfter, 1000.5), t_min: 3, items };
  }, urls);

  new Function(talkJS)();
  await flush();                       // poll #1: initial load
  const bubbles = () => p.ids.log.children
    .filter(c => /(^|\s)msg(\s|$)/.test(" " + c.className + " "))
    .map(c => c.textContent);
  if (bubbles().some(t => t.includes("hello agent"))
      && bubbles().some(t => t.includes("done"))) {
    ok("initial load renders mail + session");
  } else bad("initial load (got: " + JSON.stringify(bubbles()) + ")");

  p.st.poll(); await flush();          // poll #2: sid change → reset+refetch
  await flush();

  const replayReq = urls.slice(2).find(u => qs(u).ts_after === "0"
                                            && qs(u).t_after === undefined);
  if (replayReq) ok("restart refetches from zero (ts_after=0, no t_after)");
  else bad("restart refetch (requests: " + JSON.stringify(urls) + ")");

  if (bubbles().some(t => t.includes("hello agent"))
      && bubbles().some(t => t.includes("done"))) {
    ok("pre-restart mail is back in the pane after the replay");
  } else bad("mail replay (pane: " + JSON.stringify(bubbles()) + ")");

  const marks = p.ids.log.children.filter(c => /trn-mark/.test(c.className));
  if (marks.some(m => m.textContent.includes("session restarted"))) {
    ok("the restart is marked honestly");
  } else bad("restart mark");

  console.log("\npass=" + pass + " fail=" + fail);
  process.exit(fail ? 1 : 0);
})();
