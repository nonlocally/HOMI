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
  p.ids.pill.hidden = true;   // mirrors the real markup's `hidden` attribute
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

  // ---- the work ledger: consecutive receipts group into one collapsible
  call = 10;   // route future fetches to the echo branch
  installFetch((url) => {
    const q2 = qs(url);
    const tsA = parseFloat(q2.ts_after || "0");
    return { ok: true, live: true, present: true, sid: "sid-B", truncated: false,
             t_cursor: 20, ts_cursor: tsA, t_min: 3,
             items: [
               { via: "session", role: "tool", i: 10, ts: 1100, text: "$ step one" },
               { via: "session", role: "tool", i: 11, ts: 1101, text: "$ step two" },
               { via: "session", role: "reply", i: 12, ts: 1102, to: "bob", text: "" },
               { via: "session", role: "assistant", i: 13, ts: 1103, text: "prose after work" },
               { via: "session", role: "tool", i: 14, ts: 1104, text: "$ later step" },
             ] };
  }, urls);
  p.st.poll(); await flush();
  const ledgers = p.ids.log.children.filter(c => c.tagName === "details");
  if (ledgers.length === 2
      && ledgers[0].children.filter(c => /trn-tool/.test(c.className)).length === 3
      && ledgers[0].children.some(c => c.tagName === "summary"
                                       && /3 steps/.test(c.textContent))
      && ledgers[1].children.some(c => c.tagName === "summary"
                                       && /1 step/.test(c.textContent))) {
    ok("consecutive receipts fold into ledgers; prose breaks the group");
  } else bad("ledger grouping (details nodes: " + ledgers.length + ")");

  // ---- the new-messages pill: arrivals while scrolled up
  p.ids.log.scrollTop = 0;   // reader is up in history
  installFetch((url) => {
    const q3 = qs(url);
    return { ok: true, live: true, present: true, sid: "sid-B", truncated: false,
             t_cursor: 21, ts_cursor: parseFloat(q3.ts_after || "0") + 1,
             t_min: 3,
             items: [{ via: "mail", role: "in", ts: 99999, text: "psst", who: "scout" }] };
  }, urls);
  p.st.poll(); await flush();
  if (p.ids.pill && p.ids.pill.hidden === false) {
    ok("new-message pill appears when scrolled away");
  } else bad("pill (hidden=" + (p.ids.pill && p.ids.pill.hidden) + ")");
  p.ids.pill.dispatchEvent({ type: "click" });
  if (p.ids.pill.hidden === true) ok("pill click returns to the tail and hides");
  else bad("pill dismiss");

  // ---- markdown-lite: inline code, bold, and lists build as real nodes
  const probe = p.ids.log.children[p.ids.log.children.length - 1];
  installFetch((url) => {
    const q4 = qs(url);
    return { ok: true, live: true, present: true, sid: "sid-B", truncated: false,
             t_cursor: 22, ts_cursor: parseFloat(q4.ts_after || "0") + 1,
             t_min: 3,
             items: [{ via: "mail", role: "in", ts: 199999, who: "scout",
                       text: "use `homi send` and **never** guess:\n- one\n- two" }] };
  }, urls);
  p.st.poll(); await flush();
  const last = p.ids.log.children[p.ids.log.children.length - 1];
  const bodyN = last.children.find(c => /body/.test(c.className));
  const kinds = [];
  (function walk(n) { kinds.push(n.tagName); (n.children || []).forEach(walk); })(bodyN);
  if (kinds.includes("code") && kinds.includes("strong")
      && kinds.includes("ul") && kinds.filter(k => k === "li").length === 2) {
    ok("markdown-lite builds code/strong/ul-li as nodes (textContent only)");
  } else bad("markdown-lite (kinds: " + kinds.join(",") + ")");

  console.log("\npass=" + pass + " fail=" + fail);
  process.exit(fail ? 1 : 0);
})();
