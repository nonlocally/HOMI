// Execute the REAL talk-page JS from TALK_TEMPLATE under a minimal DOM shim.
// Grep-tests on the page source have twice passed while the behavior was
// broken; this harness exists so client-side contracts are pinned by
// EXECUTION. (Adapted from the adversarial reviewer's probe harness.)
const fs = require("fs");
const path = require("path");
const SRC = path.join(__dirname, "..", "lib", "homi_talk.py");
const py = fs.readFileSync(SRC, "utf8");
const tpl = py.split('TALK_TEMPLATE = r"""')[1].split('"""')[0];
const scripts = [...tpl.matchAll(/<script(?:[^>]*)>([\s\S]*?)<\/script>/g)].map(m => m[1]);
const talkJS = scripts[1];
const styleBlock = tpl.split("<style>")[1].split("</style>")[0];

function makeNode(tag) {
  const n = {
    tagName: tag, className: "", id: "", children: [], _text: "", hidden: false,
    disabled: false, type: "", title: "", value: "", _listeners: {},
    scrollTop: 0, clientHeight: 100,
    get scrollHeight() { return 100 + this.children.length * 20; },
    classList: {
      _n: null,
      toggle(c) {
        const s = new Set((this._n.className || "").split(/\s+/).filter(Boolean));
        if (s.has(c)) s.delete(c); else s.add(c);
        this._n.className = [...s].join(" ");
      },
      contains(c) { return (this._n.className || "").split(/\s+/).includes(c); },
    },
    remove() { const p = this._parent; if (p) p.children.splice(p.children.indexOf(this), 1); },
    addEventListener(ev, fn) { (this._listeners[ev] = this._listeners[ev] || []).push(fn); },
    dispatchEvent(e) { (this._listeners[e.type] || []).forEach(f => f.call(this, e)); },
    focus() {},
    get firstChild() { return this.children[0] || null; },
    get nextSibling() {
      const p = this._parent; if (!p) return null;
      return p.children[p.children.indexOf(this) + 1] || null;
    },
    get textContent() {
      if (this.children.length === 0) return this._text;
      return this.children.map(c => c.textContent).join("");
    },
    set textContent(v) { this.children = []; this._text = String(v); },
  };
  n.classList._n = n;
  n.appendChild = function (c) { c._parent = n; n.children.push(c); return c; };
  n.insertBefore = function (c, ref) {
    c._parent = n;
    const i = ref === null || ref === undefined ? n.children.length : n.children.indexOf(ref);
    n.children.splice(i < 0 ? n.children.length : i, 0, c);
    return c;
  };
  return n;
}

function newPage(boot) {
  const ids = {};
  for (const id of ["talk-boot", "inp", "snd", "stat", "note", "filt", "log",
                    "composer", "who", "pill", "dot"]) {
    ids[id] = makeNode(id === "inp" ? "textarea" : "div");
    ids[id].id = id;
  }
  ids["talk-boot"]._text = JSON.stringify(boot);
  const body = makeNode("body");
  const st = { poll: null, vis: null };
  global.document = {
    title: "", body, hidden: false,
    getElementById: id => ids[id] || null,
    createElement: makeNode,
    createTextNode: t => { const n = makeNode("#text"); n._text = String(t); return n; },
    addEventListener(ev, fn) { if (ev === "visibilitychange") st.vis = fn; },
  };
  global.window = { setInterval: (fn) => { st.poll = fn; return 0; } };
  global.Event = function (t) { this.type = t; this.preventDefault = () => {}; };
  return { ids, body, st };
}

function installFetch(handler, log) {
  global.fetch = (url, opts) => {
    log.push(url);
    const r = handler(url, opts);
    if (r === null) return Promise.resolve({ ok: false, status: 500, json: () => Promise.reject(new Error("x")) });
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(r) });
  };
}
const flush = () => new Promise(r => setImmediate(() => setImmediate(() => setImmediate(() => setImmediate(r)))));
const dump = (node, ind = "    ") =>
  node.children.map(c => `${ind}[${c.className || c.tagName}] ${JSON.stringify(c.textContent).slice(0, 100)}`).join("\n") || `${ind}(empty)`;

module.exports = { talkJS, styleBlock, makeNode, newPage, installFetch, flush, dump };
