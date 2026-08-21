"""homi board — the fabric's front end.

One collector turns the daemon's measured ops (status, agents, grants,
link-check) plus a links.json read into a single snapshot dict; renderers are
pure functions of that snapshot (the viz law). The page is one self-contained
HTML file with the snapshot inlined — it works from file:// and upgrades
itself to live polling of the sibling state.json when served. Serving binds
the device's Tailscale IP (tailnet-only, the docs/site/serve.sh idiom).

Far rosters are fetched over ssh for DEVICE links only (same-operator full
trust), short-timeout, provenance-labeled; a fleet peer's roster is never
fetched — deny-by-default is theirs, we render only what we hold (grants,
the pinned key, the link's measured state).

Stdlib only. No daemon ops added, no wire surface — this is a VIEW.
"""
import json
import math
import os
import subprocess
import sys
import threading
import time

import homi  # fully loaded before this module is (lazy import in cli_call)


class _Handled(Exception):
    """The handler already wrote a complete response — unwind to _get without
    a second send. Explicit sentinel (a bare-string return was a trap: any
    future str body would silently read as 'handled')."""


def board_dir():
    d = os.environ.get("HOMI_BOARD_DIR")
    if d:
        return d
    return os.path.join(os.path.dirname(homi.state_root()), "board")


# ---- collect -----------------------------------------------------------------

def _fetch_far_roster(addr):
    """A second device of THIS user, asked for its roster over batch ssh.
    Kernel path first (works on a bare non-interactive PATH), then a repo
    install's `communicate`. Returns the agents payload dict, or None."""
    for cmd in ("python3 ~/.local/share/homi/daemon/current/homi.py call agents --json",
                "communicate homi agents --json"):
        rc, out = homi._pair_ssh(addr, cmd, timeout=8)
        if rc == 0 and out.lstrip().startswith("{"):
            try:
                return json.loads(out[out.index("{"):])
            except ValueError:
                pass
    return None


def collect(no_remote=False):
    """One snapshot of the whole fabric, every number measured or labeled."""
    st = homi._call({"op": "status"}, timeout=60)
    ag = homi._call({"op": "agents"}, timeout=60)
    gr = homi._call({"op": "grants"})
    if not st.get("ok"):
        raise RuntimeError(st.get("err") or "daemon unreachable")
    self_ = st.get("self") or {}
    user = self_.get("user")
    # status's link rows carry queue/dead but not key_fp/handle — merge the
    # persisted record (same uid, same file the daemon writes).
    persisted = homi._read_json(os.path.join(homi.state_root(), "links.json"), {})

    def _agent_row(a):
        return {
            "name": a.get("name"), "kind": a.get("kind"),
            "state": a.get("state"), "provenance": a.get("provenance"),
            "undelivered": a.get("undelivered", 0),
            "seat": a.get("seat"),
            # The pulse marks work genuinely in flight (a measured busy seat).
            "surface_state": (a.get("surface") or {}).get("state"),
            "card": (a.get("card") or {}).get("what"),
        }

    # A failed local call is a LABEL, never an empty roster rendered as
    # "no agents here yet" (a confident false statement).
    ag_ok = bool(ag.get("ok"))
    home_agents = [_agent_row(a) for a in (ag.get("agents") or [])] if ag_ok else []
    devices = [{
        "device": self_.get("device"), "self": True,
        "version": self_.get("version"),
        "reachability": {"state": "here"},
        "roster_provenance": ("probed" if ag_ok
                              else "roster unavailable (%s)" % (ag.get("err") or "?")),
        "agents": home_agents,
    }]
    grants_ok = bool(gr.get("ok"))

    def _link_work(dev, l, rec):
        """One link's whole measurement (check + optional far roster) — run
        in parallel so a wedged peer costs its own timeout, not the page's.
        Never raises: homi._call can sys.exit when the daemon vanishes."""
        row = {"device": dev, "kind": l.get("kind", "device"),
               "queue": l.get("queue", 0), "dead": l.get("dead", 0),
               "allow_seats": bool(l.get("allow_seats")),
               "rtt_ms": None, "far_version": None, "far_user": None,
               "err": None, "legacy": False}
        try:
            chk = homi._call({"op": "link-check", "device": dev}, timeout=10)
        except (Exception, SystemExit) as e:
            chk = {"ok": False, "err": "check failed: %s" % e}
        if chk.get("ok") and chk.get("rtt_ms") is not None:
            row["rtt_ms"] = chk["rtt_ms"]
            row["far_version"] = chk.get("far_version")
            row["far_user"] = chk.get("far_user")
            row["legacy"] = bool(chk.get("legacy_peer"))
        else:
            row["err"] = chk.get("err") or "unreachable"

        # Deny-by-default: only an explicit DEVICE link (same operator) is
        # ever ssh'd for its roster; fleet AND unknown kinds render as people.
        if row["kind"] != "device":
            person = {
                "handle": rec.get("handle") or dev,
                "fp": rec.get("key_fp"),
                "granted": (sorted((gr.get("grants") or {}).get(dev, []))
                            if grants_ok else None),
                "auto": ((gr.get("auto") or {}).get(dev, {}) if grants_ok else {}),
                "grants_ok": grants_ok,
                "link": {"rtt_ms": row["rtt_ms"], "queue": row["queue"],
                         "dead": row["dead"], "err": row["err"]},
            }
            return row, None, person

        far = {"device": dev, "self": False,
               "version": row["far_version"],
               "reachability": ({"state": "up", "rtt_ms": row["rtt_ms"]}
                                if row["rtt_ms"] is not None else
                                {"state": "unreachable", "queued": row["queue"]}),
               "agents": []}
        if no_remote:
            far["roster_provenance"] = "not fetched (--no-remote)"
        elif not rec.get("addr"):
            far["roster_provenance"] = "not fetched (direct-socket link)"
        elif row["rtt_ms"] is None:
            far["roster_provenance"] = ("unreachable — mail queues (%d pending)"
                                        % row["queue"])
        else:
            try:
                roster = _fetch_far_roster(rec["addr"])
            except (Exception, SystemExit):
                roster = None
            if roster and roster.get("ok"):
                far["roster_provenance"] = "fetched over ssh"
                far["agents"] = [_agent_row(a) for a in (roster.get("agents") or [])]
            else:
                far["roster_provenance"] = "roster fetch failed (ssh)"
        return row, far, None

    items = [(dev, l, persisted.get(dev) or {})
             for dev, l in sorted((st.get("links") or {}).items())]
    links = []
    people = []
    if items:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(
                max_workers=min(8, len(items))) as ex:
            results = list(ex.map(lambda t: _link_work(*t), items))
        for row, far, person in results:
            links.append(row)
            if far is not None:
                devices.append(far)
            if person is not None:
                people.append(person)

    live = sum(1 for d in devices for a in d["agents"] if a.get("state") == "live")
    agents_n = sum(len(d["agents"]) for d in devices)
    undelivered = sum(a.get("undelivered", 0) for d in devices for a in d["agents"])
    queued = sum(l.get("queue", 0) for l in links)
    return {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "generated_ts": time.time(),
        "user": user,
        "device": self_.get("device"),
        "version": self_.get("version"),
        "totals": {"devices": len(devices), "people": len(people),
                   "agents": agents_n, "live": live,
                   "undelivered": undelivered, "queued": queued},
        "devices": devices,
        "links": links,
        "people": people,
    }


# ---- render ------------------------------------------------------------------

def render_html(snap):
    """Inject the snapshot at __HOMI__ with EVERY `<` escaped to \\u003c —
    lossless in JSON, and no fabric string (a granted peer chooses its own
    from-names) can reach the HTML tokenizer at all. Escaping only `</` was
    provably bypassable: `<!--<script>` double-escapes the script element and
    blanks the whole page without ever executing."""
    data = json.dumps(snap).replace("<", "\\u003c")
    return TEMPLATE.replace("__HOMI__", data, 1)


def write_pages(snap):
    """The snapshot names the pinned key fingerprint, every agent, every
    grant — it gets the same 0700/0600 posture as the state dir itself."""
    d = board_dir()
    homi.ensure_dir_0700(d)
    for name, body in (("index.html", render_html(snap)),
                       ("state.json", json.dumps(snap))):
        p = os.path.join(d, name)
        homi._atomic_write(p, body)
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass
    return os.path.join(d, "index.html")


# ---- serve -------------------------------------------------------------------

class _Cache:
    """Collection costs real measurements (a ping per link, optional ssh) —
    one collection serves every request inside the TTL."""
    def __init__(self, no_remote, ttl=10.0):
        self.no_remote = no_remote
        self.ttl = ttl
        self.mu = threading.Lock()
        self.at = 0.0
        self.snap = None

    def get(self):
        with self.mu:
            if self.snap is None or (time.time() - self.at) > self.ttl:
                self.snap = collect(no_remote=self.no_remote)
                self.at = time.time()
            return self.snap


def serve(port, bind, no_remote, ttl=10.0):
    import hmac
    import http.server
    import urllib.parse
    import homi_talk
    import homi_transcript

    cache = _Cache(no_remote, ttl=ttl)
    # Write-path defense, honestly named. The AUTHN boundary is tailnet
    # reachability + the Host allowlist (a loopback bind behind `tailscale
    # serve` also gives a trustworthy Tailscale-User-Login, logged per send).
    # The per-serve token is NOT an auth factor — it is served to anyone who
    # can load /talk. What it buys: (1) a CSRF/cross-origin defense (paired
    # with the JSON content-type + X-Homi custom header, which force a
    # preflight no cross-origin page can satisfy — verified: OPTIONS 501, no
    # CORS), and (2) a session binding — a restart mints a new token, so a
    # stale tab's writes 403 instead of acting. Injected into served pages
    # only, never a URL. constant-time compared.
    token = os.urandom(16).hex()
    # Short-TTL shared cache of the human's inbox: every /api/conv poll (per
    # tab, every 2.5 s) would otherwise drag the whole mailbox through the
    # daemon. One read serves all pollers within the window.
    _inbox_cache = {}   # name -> {"at": ts, "val": inbox} — keyed so no
    _inbox_mu = threading.Lock()  # caller can ever read another name's mail

    def _inbox_cached(name):
        with _inbox_mu:
            ent = _inbox_cache.get(name)
            if ent is None or (time.time() - ent["at"]) > 1.5:
                ent = {"at": time.time(),
                       "val": homi._call({"op": "inbox", "name": name,
                                          "tail": 500})}
                _inbox_cache[name] = ent
            return ent["val"]
    handle = homi_talk.human_handle()
    if handle:
        homi_talk.ensure_human(handle)
        homi_talk.ensure_talk_dir()   # once, not per message

    # Host allowlist: the DNS-rebinding pin. A rebinding attack rides the
    # victim's own browser with an attacker Host name — refuse anything that
    # isn't one of OUR names for this server.
    allowed_hosts = {bind, "%s:%d" % (bind, port),
                     "localhost", "localhost:%d" % port,
                     "127.0.0.1", "127.0.0.1:%d" % port}
    ts_ip = homi._tailscale_ip()
    if ts_ip:
        allowed_hosts.update({ts_ip, "%s:%d" % (ts_ip, port)})
    try:
        out = subprocess.run(["tailscale", "status", "--json"],
                             capture_output=True, text=True, timeout=5)
        dns = (json.loads(out.stdout or "{}").get("Self") or {}).get("DNSName", "")
        dns = dns.rstrip(".")
        if dns:
            allowed_hosts.update({dns, "%s:%d" % (dns, port),
                                  dns.split(".")[0],
                                  "%s:%d" % (dns.split(".")[0], port)})
    except Exception as e:
        sys.stderr.write("board serve: no tailscale DNS names (%s)\n" % e)
    # A fronting proxy (e.g. a Cloudflare Tunnel with an identity wall) has
    # its own Host name for us. HOMI_BOARD_HOSTS names it explicitly — the
    # allowlist stays a pin, never a wildcard. The proxy MUST gate identity
    # before the origin; this env only teaches the origin its public name.
    for h in (os.environ.get("HOMI_BOARD_HOSTS") or "").split(","):
        h = h.strip()
        if h:
            allowed_hosts.update({h, "%s:%d" % (h, port)})
    # Compared case-insensitively (Host header is lowercased on receipt).
    allowed_hosts = {h.lower() for h in allowed_hosts}

    class Handler(http.server.BaseHTTPRequestHandler):
        def _host_ok(self):
            return (self.headers.get("Host") or "").lower() in allowed_hosts

        def _token_ok(self):
            got = self.headers.get("X-Homi-Token") or ""
            return hmac.compare_digest(got, token)

        def _json(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _body(self):
            path = self.path.split("?", 1)[0]   # cache-busters must not 404
            if path in ("/", "/index.html"):
                return render_html(cache.get()).encode(), "text/html; charset=utf-8"
            if path == "/state.json":
                return json.dumps(cache.get()).encode(), "application/json"
            if path.startswith("/talk/"):
                target = urllib.parse.unquote(path[len("/talk/"):])
                if not handle:
                    return (b"claim a handle first: communicate homi init",
                            "text/plain; charset=utf-8")
                if not homi_talk.valid_target(target):
                    return None, None
                return (homi_talk.render_talk(handle, target, token).encode(),
                        "text/html; charset=utf-8")
            if path.startswith("/api/timeline/"):
                # The merged view: session spine + authoritative mail.
                # Same token gate as every other read of fabric content.
                if not self._token_ok():
                    self._json(403, {"ok": False, "err": "token"})
                    raise _Handled
                target = urllib.parse.unquote(path[len("/api/timeline/"):])
                if not handle or not homi_talk.valid_target(target):
                    self._json(400, {"ok": False, "err": "bad target"})
                    raise _Handled
                q = urllib.parse.parse_qs(
                    urllib.parse.urlsplit(self.path).query)

                def _iq(k, cast=int):
                    v = (q.get(k) or [None])[0]
                    try:
                        return cast(v) if v is not None else None
                    except ValueError:
                        return None
                tsa = _iq("ts_after", float) or 0.0
                if not math.isfinite(tsa):   # nan/inf would poison the cursor
                    tsa = 0.0
                r = homi_talk.timeline(
                    handle, target, t_after=_iq("t_after"), ts_after=tsa,
                    t_before=_iq("t_before"), n=_iq("n") or 150,
                    inbox=_inbox_cached)
                return json.dumps(r).encode(), "application/json"
            if path.startswith("/api/session/"):
                # Transcripts are the most sensitive read on the board —
                # same token gate as conversations, malformed names get an
                # explicit 400 (never a path lookup), and qualified targets
                # are refused honestly: the transcript lives on the agent's
                # own device, and this server only reads local files.
                if not self._token_ok():
                    self._json(403, {"ok": False, "err": "token"})
                    raise _Handled
                target = urllib.parse.unquote(path[len("/api/session/"):])
                if "@" in target:
                    self._json(400, {"ok": False,
                                     "err": "session view is local-only"})
                    raise _Handled
                if not homi_talk.valid_target(target):
                    self._json(400, {"ok": False, "err": "bad target"})
                    raise _Handled
                q = urllib.parse.parse_qs(
                    urllib.parse.urlsplit(self.path).query)

                def _iq(k):
                    v = (q.get(k) or [None])[0]
                    try:
                        return int(v) if v is not None else None
                    except ValueError:
                        return None
                r = homi_transcript.turns_for(target, after=_iq("after"),
                                              before=_iq("before"),
                                              n=_iq("n") or None)
                return json.dumps(r).encode(), "application/json"
            if path.startswith("/api/conv/"):
                if not self._token_ok():
                    self._json(403, {"ok": False, "err": "token"})
                    raise _Handled
                target = urllib.parse.unquote(path[len("/api/conv/"):])
                if not handle or not homi_talk.valid_target(target):
                    return None, None
                q = urllib.parse.parse_qs(
                    urllib.parse.urlsplit(self.path).query)
                try:
                    since = float((q.get("since") or ["0"])[0])
                except ValueError:
                    since = 0.0
                if not math.isfinite(since):   # nan/inf would poison the cursor
                    since = 0.0
                entries = homi_talk.conversation(handle, target, since=since,
                                                 inbox=_inbox_cached)
                return (json.dumps({"ok": True, "entries": entries}).encode(),
                        "application/json")
            return None, None

        def _get(self, send_body):
            if not self._host_ok():
                self.send_error(403, "host")
                return
            # homi._call sys.exit()s when the daemon is down — SystemExit is a
            # BaseException, so a bare `except Exception` provably let it
            # escape the handler and the client saw an EMPTY REPLY instead of
            # a 500. Catch both; the detail goes to stderr, never the status
            # line (which BaseHTTPRequestHandler emits unescaped). _Handled is
            # the explicit sentinel — the handler already wrote its response.
            try:
                body, ctype = self._body()
            except _Handled:
                return
            except (Exception, SystemExit) as e:
                sys.stderr.write("board serve: collect failed: %s\n" % e)
                self.send_error(500, "collect failed")
                return
            if body is None:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if send_body:
                self.wfile.write(body)

        def do_GET(self):
            self._get(True)

        def do_HEAD(self):
            self._get(False)

        def do_POST(self):
            # Write path: every layer must hold. Host pin; JSON content type +
            # custom header (cross-origin forms can't send either without a
            # failing preflight); same-origin Sec-Fetch when the browser sends
            # it; and the page-injected mutation token, constant-time.
            if not self._host_ok():
                self.send_error(403, "host")
                return
            path = self.path.split("?", 1)[0]
            if path != "/api/send":
                self.send_error(404)
                return
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip()
            sfs = self.headers.get("Sec-Fetch-Site")
            if (ctype != "application/json"
                    or self.headers.get("X-Homi") != "1"
                    or (sfs is not None and sfs != "same-origin")):
                self._json(403, {"ok": False, "err": "headers"})
                return
            if not self._token_ok():
                self._json(403, {"ok": False, "err": "token"})
                return
            if not handle:
                self._json(403, {"ok": False, "err": "claim a handle first"})
                return
            try:
                # Floor at 0: a negative Content-Length would become
                # rfile.read(-1) = read-to-EOF, pinning the thread.
                n = max(0, min(int(self.headers.get("Content-Length") or 0), 65536))
                req = json.loads(self.rfile.read(n).decode("utf-8"))
                target = req.get("to") or ""
                text = (req.get("text") or "").strip()
            except (ValueError, UnicodeDecodeError):
                self._json(400, {"ok": False, "err": "bad json"})
                return
            if not homi_talk.valid_target(target) or not text:
                self._json(400, {"ok": False, "err": "bad target or empty text"})
                return
            who = (self.headers.get("Tailscale-User-Login")
                   or self.headers.get("Cf-Access-Authenticated-User-Email"))
            try:
                entry = homi_talk.send(handle, target, text)
            except homi_talk.SendRefused as e:
                # A daemon refusal is a CLIENT error — surface the honest reason.
                self._json(400, {"ok": False, "err": str(e)})
                return
            except (Exception, SystemExit) as e:
                sys.stderr.write("board serve: send failed: %s\n" % e)
                self._json(500, {"ok": False, "err": "send failed"})
                return
            sys.stderr.write("talk: %s -> %s (%d chars, routed %s%s)\n"
                             % (handle, target, len(text), entry.get("routed"),
                                (", ts-user " + who) if who else ""))
            body = json.dumps({"ok": True, "routed": entry.get("routed"),
                               "entry": entry}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer((bind, port), Handler)
    sys.stdout.write("homi board -> http://%s:%d/   (Ctrl-C to stop)\n"
                     % (bind, port))
    if handle:
        sys.stdout.write("talk: click any agent, or /talk/<name> — sends go "
                         "out as @%s\n" % handle)
    else:
        sys.stdout.write("talk: disabled (claim a handle first: "
                         "communicate homi init)\n")
    sys.stdout.flush()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


# ---- cli ---------------------------------------------------------------------

def main(argv):
    no_remote = "--no-remote" in argv
    if "--serve" in argv:
        i = argv.index("--serve")
        port = None
        if i + 1 < len(argv) and argv[i + 1].isdigit():
            port = int(argv[i + 1])
        if port is None:
            try:
                port = int(os.environ.get("HOMI_BOARD_PORT") or 7421)
            except ValueError:
                port = 7421
        bind = None
        if "--bind" in argv and argv.index("--bind") + 1 < len(argv):
            bind = argv[argv.index("--bind") + 1]
            if bind.startswith("--"):   # a flag is not an address
                bind = None
        if not bind and "--ts" in argv:
            # The phone path: loopback behind `tailscale serve` — a real
            # HTTPS origin (secure context on iOS) + trustworthy identity
            # headers, exactly because nothing else can reach a loopback bind.
            bind = "127.0.0.1"
            sys.stdout.write("front it for HTTPS:  tailscale serve --bg %d\n"
                             % port)
            try:
                out = subprocess.run(["tailscale", "status", "--json"],
                                     capture_output=True, text=True, timeout=5)
                dns = (json.loads(out.stdout or "{}").get("Self")
                       or {}).get("DNSName", "").rstrip(".")
                if dns:
                    sys.stdout.write("then open:           https://%s/\n" % dns)
            except Exception:
                pass
        if not bind:
            # Tailnet-only by construction: bind the device's Tailscale IP,
            # never 0.0.0.0 (the docs/site/serve.sh idiom).
            bind = homi._tailscale_ip()
        if not bind:
            sys.stderr.write("no tailscale IP (is tailscale up?) — "
                             "pass --bind 127.0.0.1 or --ts for loopback\n")
            return 1
        return serve(port, bind, no_remote)

    try:
        snap = collect(no_remote=no_remote)
        if "--json" in argv:
            print(json.dumps(snap, indent=1))
            return 0
        path = write_pages(snap)   # ensure_dir_0700 can refuse a foreign dir
    except Exception as e:
        sys.stderr.write("board: %s\n" % e)
        return 1
    sys.stdout.write(path + "\n")
    if "--open" in argv:
        # macOS first; xdg-open for the Linux devices pair stages this onto.
        for opener in ("open", "xdg-open"):
            try:
                subprocess.run([opener, path], check=False)
                break
            except OSError:
                continue
    return 0


# ---- the fixed template ------------------------------------------------------
# One page, self-contained: tokens + the honesty vocabulary (pattern-fill
# state dots, provenance tags, measured-vs-reported greyscale). The snapshot
# is inlined at __HOMI__; when served, the page polls the sibling state.json
# (guarded off under file://). DOM is built only via textContent — no string
# from the fabric can ever become markup.

TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>homi board</title>
<style>
  /* Instrument panel, dark-first, warm-neutral. Color is worry (status) or
     nothing. One instrument over all rows — never a card per row. Mono is a
     scalpel: machine identifiers only. */
  :root {
    color-scheme: dark;
    --plane: #090909;
    --surface: #131312;
    --surface-2: #1b1b19;
    --ink: #f6f5f1;
    --ink-2: #bab8b0;
    --muted: #898781;
    --grid: #262624;
    --baseline: #36352f;
    --border: rgba(255, 255, 255, 0.075);
    --rule: #1e1e1c;   /* fallback: ~55% of --grid on the plane */
    --rule: color-mix(in srgb, var(--grid) 55%, transparent);
    --status-good: #0ca30c;
    --status-warn: #fab219;
    --status-critical: #d03b3b;
    --font-sans: system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    --font-mono: ui-monospace, "SF Mono", SFMono-Regular, Menlo, Consolas, monospace;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  html { background: var(--plane); }
  body {
    max-width: 1400px; margin: 0 auto; padding: 30px 28px 80px;
    background: var(--plane); color: var(--ink);
    font-family: var(--font-sans);
    font-size: 14px; line-height: 1.45; letter-spacing: -0.006em;
    -webkit-font-smoothing: antialiased; text-rendering: optimizeLegibility;
  }
  a { color: inherit; }
  :focus-visible { outline: 2px solid var(--ink); outline-offset: 2px; }

  h1 { font-size: 32px; font-weight: 500; line-height: 1.05; letter-spacing: -0.025em; }
  .lede { margin-top: 6px; font-size: 12.5px; color: var(--muted); font-variant-numeric: tabular-nums; }
  .lede b { color: var(--ink-2); font-weight: 500; }

  .eyebrow {
    display: flex; align-items: baseline; gap: 12px;
    margin: 40px 0 12px;
    font-size: 14.5px; font-weight: 500; letter-spacing: -0.012em;
  }
  .eyebrow::after { content: ""; flex: 1; border-top: 1px solid var(--grid); transform: translateY(-3px); }
  .eyebrow .n { font-family: var(--font-mono); font-size: 11px; color: var(--muted); font-variant-numeric: tabular-nums; }

  .board {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    overflow: hidden;
  }
  .grouprow {
    display: flex; align-items: baseline; gap: 10px;
    padding: 10px 18px 8px;
    border-top: 1px solid var(--grid);
  }
  .board > .grouprow:first-child { border-top: 0; }
  .grouprow .dev { font-family: var(--font-mono); font-size: 11px; color: var(--ink-2); font-weight: 500; }
  .grouprow .tag { margin-left: auto; }
  .tag {
    font-size: 10.5px; letter-spacing: 0.06em; text-transform: uppercase;
    color: var(--muted); white-space: nowrap; font-variant-numeric: tabular-nums;
  }
  .head, .row {
    display: grid;
    grid-template-columns: 10px minmax(0, 1fr) minmax(96px, max-content) 64px minmax(0, 120px) minmax(0, 34%);
    align-items: baseline; gap: 0 12px;
    padding: 8px 18px;
  }
  .head {
    padding: 10px 18px 9px;
    font-size: 10.5px; letter-spacing: 0.08em; text-transform: uppercase;
    color: var(--muted);
    border-bottom: 1px solid var(--grid);
  }
  .row { font-size: 13px; border-top: 1px solid var(--rule); }
  .grouprow + .row, .head + .row { border-top: 0; }
  .row:hover { background: var(--surface-2); }
  .row .name { font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .row .name a { color: inherit; text-decoration: none; border-bottom: 1px dotted var(--baseline); }
  .row .name a:hover { color: var(--ink); border-bottom-color: var(--ink); }
  .row .st { font-size: 12.5px; }
  .st.t-live { color: var(--ink); font-weight: 500; }
  .st.t-stored { color: var(--muted); }
  .st.t-dead { color: var(--status-critical); }
  .row .held { font-size: 11.5px; color: var(--status-warn); font-variant-numeric: tabular-nums; text-align: right; }
  .row .seat { font-family: var(--font-mono); font-size: 10.5px; color: var(--muted);
               overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .row .card { font-size: 12px; color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

  .dot { width: 6px; height: 6px; border-radius: 50%; background: var(--baseline); align-self: center; }
  .dot.live { background: var(--status-good); }
  .dot.dead { background: var(--status-critical); }
  .dot.busy { animation: breathe 2.4s ease-in-out infinite; }
  @keyframes breathe { 0%, 100% { opacity: 0.25; } 50% { opacity: 1; } }
  @media (prefers-reduced-motion: reduce) { .dot.busy { animation: none; } }

  .lhead, .lrow {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 78px 110px 64px 56px 76px;
    align-items: baseline; gap: 0 12px;
    padding: 8px 18px;
  }
  .lhead {
    padding: 10px 18px 9px;
    font-size: 10.5px; letter-spacing: 0.08em; text-transform: uppercase;
    color: var(--muted); border-bottom: 1px solid var(--grid);
  }
  .lrow { font-size: 12.5px; border-top: 1px solid var(--rule); font-variant-numeric: tabular-nums; }
  .lhead + .lrow { border-top: 0; }
  .lrow:hover { background: var(--surface-2); }
  .lrow .dev { font-family: var(--font-mono); font-size: 11px; color: var(--ink-2); font-weight: 500;
               overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .num { color: var(--ink); }
  .warn { color: var(--status-warn); }
  .bad { color: var(--status-critical); }
  .quiet { color: var(--muted); }

  .person { padding: 12px 18px 12px; border-top: 1px solid var(--grid); }
  .board > .person:first-child { border-top: 0; }
  .person .who { display: flex; align-items: baseline; gap: 10px; }
  .person .handle { font-size: 14px; font-weight: 500; letter-spacing: -0.006em; }
  .person .who .tag { margin-left: auto; }
  .person .key { margin-top: 3px; font-family: var(--font-mono); font-size: 10.5px; color: var(--muted); }
  .person .line { margin-top: 5px; font-size: 12.5px; color: var(--ink-2); }
  .person .line .lbl { color: var(--muted); }

  .empty { padding: 12px 18px; font-size: 12.5px; color: var(--muted); }
  .note { padding: 8px 18px 10px; font-size: 12.5px; color: var(--muted); }

  footer {
    margin-top: 44px; padding-top: 10px;
    border-top: 1px solid var(--grid);
    font-family: var(--font-mono); font-size: 10.5px; color: var(--muted);
  }
  @media (max-width: 860px) {
    body { padding: 22px 16px 60px; }
    .head, .row { grid-template-columns: 10px minmax(0, 1fr) minmax(90px, max-content) 56px; }
    .head .seat, .row .seat, .head .card, .row .card { display: none; }
    .lhead, .lrow { grid-template-columns: minmax(0, 1fr) 70px 90px 56px; }
    .lhead .c-dead, .lrow .c-dead, .lhead .c-seats, .lrow .c-seats { display: none; }
  }
</style>
</head>
<body>
<script id="homi-state" type="application/json">__HOMI__</script>
<script>
(function () {
  "use strict";
  var state;
  try {
    state = JSON.parse(document.getElementById("homi-state").textContent);
  } catch (e) {
    document.body.textContent = "no board data (render via communicate homi board)";
    return;
  }
  var B = document.body;
  function el(t, c, x) {
    var n = document.createElement(t);
    if (c) n.className = c;
    if (x != null) n.textContent = x;
    return n;
  }
  function add(p, t, c, x) { var n = el(t, c, x); p.appendChild(n); return n; }
  function ago(ts) {
    var t = Number(ts);
    if (!isFinite(t) || t <= 0) return "";
    var d = Math.max(0, Math.floor(Date.now() / 1000 - t));
    if (d < 5) return "just now";
    if (d < 60) return d + "s ago";
    if (d < 3600) return Math.floor(d / 60) + "m ago";
    if (d < 86400) return Math.floor(d / 3600) + "h ago";
    return Math.floor(d / 86400) + "d ago";
  }
  function hoursLeft(exp) {
    // "expired" is a definite claim — reserve it for a finite PAST timestamp;
    // absent or malformed data is honestly "unknown".
    if (exp == null || !isFinite(Number(exp))) return "unknown";
    var s = Number(exp) - Date.now() / 1000;
    if (s <= 0) return "expired";
    if (s < 3600) return "~" + Math.max(1, Math.floor(s / 60)) + "m left";
    if (s < 172800) return "~" + Math.floor(s / 3600) + "h left";
    return "~" + Math.floor(s / 86400) + "d left";
  }

  var hosts = {};
  function build() {
    var who = state.user && state.user.handle ? "@" + state.user.handle : "unclaimed";
    document.title = "homi / " + who;
    add(B, "h1", null, who);
    hosts.lede = add(B, "div", "lede");
    renderLede();

    var eb1 = add(B, "div", "eyebrow", "agents");
    hosts.agentsN = add(eb1, "span", "n");
    hosts.devices = add(B, "div", "board");

    var eb2 = add(B, "div", "eyebrow", "links");
    hosts.linksN = add(eb2, "span", "n");
    hosts.links = add(B, "div", "board");

    var eb3 = add(B, "div", "eyebrow", "people");
    hosts.peopleN = add(eb3, "span", "n");
    hosts.people = add(B, "div", "board");

    hosts.footer = add(B, "footer");
  }

  function renderLede() {
    var h = hosts.lede;
    h.textContent = "";
    h.appendChild(document.createTextNode("the fabric, right now"));
    var t = state.totals || {};
    [["devices", t.devices], ["people", t.people], ["agents", t.agents],
     ["live", t.live], ["held", t.undelivered], ["queued", t.queued]
    ].forEach(function (kv) {
      h.appendChild(document.createTextNode(" · "));
      h.appendChild(el("b", null, kv[1] == null ? "0" : String(kv[1])));
      h.appendChild(document.createTextNode(" " + kv[0]));
    });
    var a = ago(state.generated_ts);
    if (a) h.appendChild(document.createTextNode(" · generated " + a));
  }

  function stClass(st) {
    if (st === "live") return "st t-live";
    if (st === "dead") return "st t-dead";
    return "st t-stored";
  }
  function dotClass(a) {
    var c = "dot";
    if (a.state === "live") c += " live";
    if (a.state === "dead") c += " dead";
    if (a.surface_state === "busy") c += " busy";
    return c;
  }

  function renderDevices(host) {
    host.textContent = "";
    var total = 0;
    var headDone = false;
    (state.devices || []).forEach(function (d) {
      var g = add(host, "div", "grouprow");
      add(g, "span", "dev", d.device || "?");
      var bits = [];
      if (d.self) bits.push("this device");
      if (d.version) bits.push("v" + d.version);
      if (d.reachability && d.reachability.rtt_ms != null) bits.push(d.reachability.rtt_ms + " ms");
      if (d.roster_provenance) bits.push(d.roster_provenance);
      add(g, "span", "tag", bits.join(" · "));
      if (!d.agents || !d.agents.length) {
        var note = (d.reachability && d.reachability.state === "unreachable")
          ? "unreachable — mail queues durably (" + (d.reachability.queued || 0) + " pending)"
          : (d.roster_provenance && d.roster_provenance.indexOf("unavailable") >= 0)
            ? "roster unavailable"
            : "no agents here yet — communicate homi claim <name>";
        add(host, "div", "note", note);
        return;
      }
      if (!headDone) {
        // One header for the whole instrument, before the FIRST device that
        // actually has rows (an empty home device must not eat it).
        headDone = true;
        var hd = add(host, "div", "head");
        add(hd, "span");
        add(hd, "span", null, "name");
        add(hd, "span", null, "state");
        add(hd, "span", "held", "held");
        add(hd, "span", "seat", "seat");
        add(hd, "span", "card", "card");
      }
      d.agents.forEach(function (a) {
        total += 1;
        var r = add(host, "div", "row");
        add(r, "span", dotClass(a));
        // Served pages link each agent to its talk surface; a file:// open
        // stays a static snapshot (nothing to talk to).
        var nm = add(r, "span", "name");
        if (a.name && window.location.protocol.indexOf("http") === 0
            && (d.self || d.device)) {
          var link = el("a", null, a.name);
          link.href = "/talk/" + encodeURIComponent(
            d.self ? a.name : a.name + "@" + d.device);
          nm.appendChild(link);
        } else {
          nm.textContent = a.name || "?";
        }
        var st = add(r, "span", stClass(a.state));
        st.textContent = a.state || "?";
        if (a.provenance && a.provenance !== "probed") st.textContent += " · " + a.provenance;
        add(r, "span", "held", a.undelivered ? String(a.undelivered) : "");
        add(r, "span", "seat", a.seat || "");
        add(r, "span", "card", a.card || "");
      });
    });
    hosts.agentsN.textContent = total ? String(total) : "";
  }

  function renderLinks(host) {
    host.textContent = "";
    var links = state.links || [];
    hosts.linksN.textContent = links.length ? String(links.length) : "";
    if (!links.length) {
      add(host, "div", "empty", "no links — enroll a device: communicate homi pair <user@host>");
      return;
    }
    var hd = add(host, "div", "lhead");
    add(hd, "span", null, "device");
    add(hd, "span", null, "kind");
    add(hd, "span", null, "round trip");
    add(hd, "span", null, "queue");
    add(hd, "span", "c-dead", "dead");
    add(hd, "span", "c-seats", "seats");
    links.forEach(function (l) {
      var r = add(host, "div", "lrow");
      add(r, "span", "dev", l.device);
      add(r, "span", "quiet", l.kind + (l.legacy ? " · legacy" : ""));
      var rt = add(r, "span");
      if (l.rtt_ms != null) add(rt, "span", "num", l.rtt_ms + " ms");
      else add(rt, "span", "bad", l.err || "unreachable");
      add(r, "span", l.queue ? "warn" : "quiet", l.queue ? String(l.queue) : "");
      add(r, "span", "c-dead " + (l.dead ? "bad" : "quiet"), l.dead ? String(l.dead) : "");
      add(r, "span", "c-seats quiet", l.allow_seats ? "granted" : "");
    });
  }

  function renderPeople(host) {
    host.textContent = "";
    var people = state.people || [];
    hosts.peopleN.textContent = people.length ? String(people.length) : "";
    if (!people.length) {
      add(host, "div", "empty", "no people connected yet — communicate homi connect --invite");
      return;
    }
    people.forEach(function (p) {
      var box = add(host, "div", "person");
      var who = add(box, "div", "who");
      add(who, "span", "handle", "@" + p.handle);
      var bits = [];
      if (p.link && p.link.rtt_ms != null) bits.push(p.link.rtt_ms + " ms");
      else if (p.link && p.link.err) bits.push("unreachable");
      if (p.link && p.link.queue) bits.push(p.link.queue + " queued");
      if (bits.length) add(who, "span", "tag", bits.join(" · "));
      if (p.fp) add(box, "div", "key", p.fp);
      var g = add(box, "div", "line");
      add(g, "span", "lbl", "granted ");
      if (p.grants_ok === false) add(g, "span", "lbl", "grants unavailable");
      else if (p.granted && p.granted.length) g.appendChild(document.createTextNode(p.granted.join(", ")));
      else add(g, "span", "lbl", "nothing — communicate homi grant " + p.handle + " <agent>");
      var auto = p.auto || {};
      var names = Object.keys(auto).sort();
      if (names.length) {
        var a = add(box, "div", "line");
        add(a, "span", "lbl", "return paths ");
        names.forEach(function (n, i) {
          if (i) a.appendChild(document.createTextNode(", "));
          a.appendChild(document.createTextNode(n + " "));
          add(a, "span", "tag", hoursLeft(auto[n]));
        });
      }
    });
  }

  function renderFooter() {
    hosts.footer.textContent =
      ["homi board", state.device, "v" + (state.version || "?")].filter(Boolean).join(" · ");
  }

  function renderAll() {
    var who = state.user && state.user.handle ? "@" + state.user.handle : "unclaimed";
    document.title = "homi / " + who;
    renderLede();
    renderDevices(hosts.devices);
    renderLinks(hosts.links);
    renderPeople(hosts.people);
    renderFooter();
  }

  build();
  renderAll();
  window.setInterval(renderLede, 10000);   // the age in the lede stays honest

  // Live upgrade when served; a file:// open stays a faithful static snapshot.
  function pollState() {
    if (window.location.protocol === "file:") return;
    window.setInterval(function () {
      if (document.hidden) return;   // a background tab must not drive collects
      fetch("state.json", { cache: "no-store" })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (next) {
          if (!next) return;
          state = next;
          renderAll();
        })
        .catch(function () {});
    }, 5000);
  }
  pollState();
})();
</script>
</body>
</html>
"""
