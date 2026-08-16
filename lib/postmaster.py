#!/usr/bin/env python3
"""postmaster — the communicate fabric daemon (agent-fabric v1).

One per-device daemon that gives agents durable identity and a durable
address, independent of any process or session:

  * identities   `claim <name>` binds a STABLE socket <sockdir>/pm-<name>.sock
                 and plants a sweep-proof Claude sidecar, so the name is
                 messageable (SendMessage / cc-socks) even when no session
                 backs it. Never pid-derived paths.
  * mailboxes    every inbound frame is appended durably to
                 $PM_STATE/mail/<name>/inbox.jsonl before anything else.
  * store→wake   when a REAL session with that name is alive (sidecar name
                 match + live socket), the postmaster unplants its own sidecar
                 and drains undelivered mail into the session as protocol
                 turns; when the session dies it replants and holds.
  * liveness     measured, never inferred from a file existing. probe =
                 connect + recv(1): fast EOF => dead, timeout => a live
                 listener holds the line. The postmaster probes ITS OWN
                 published sockets too (self-probe), provenance-labelled.
  * links        postmaster↔postmaster, one per device pair. Outbound only
                 (`ssh -N -L` toward the peer's inbound socket for THIS
                 device) so inbound and outbound fail independently.

State layout ($PM_STATE = ${COMM_STATE:-~/.local/state/communicate}/pm):
  daemon.pid daemon.log daemon.lock/   singleton bookkeeping
  pm.sock                              control socket (CLI ops, one JSON/conn)
  identities.json                      claimed names (reloaded on start)
  mail/<name>/inbox.jsonl + .cursor    the durable address
  in/<device>.sock                     link inbound (arrival-line attribution)
  links.json  links/<device>.sock      link outbound state + local ends
  out/<device>/<ts>-<id>.json          outbound queue (at-least-once + ack)
  seen/<device>                        dedup ring of received msg_ids
  routes.json                          materialized routes + measured liveness

Env: COMM_STATE, PM_SOCK_DIR, PM_SESSIONS_DIR, PM_SELF, PM_TICK, PM_PROBE.
"""
import json
import os
import re
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cc_peer  # deliver, _sidecar_obj, _read_line, _extract_text, _addr_from


# ---- paths / env -------------------------------------------------------------

def state_root():
    base = os.environ.get("COMM_STATE") or os.path.join(
        os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"),
        "communicate")
    return os.path.join(base, "pm")


def sock_dir():
    d = os.environ.get("PM_SOCK_DIR")
    if d:
        return d
    m = os.environ.get("CLAUDE_CODE_MESSAGING_SOCKET")
    if m:
        return os.path.dirname(m)
    return os.path.join(os.environ.get("XDG_RUNTIME_DIR") or "/tmp", "cc-socks")


def sessions_dir():
    d = os.environ.get("PM_SESSIONS_DIR")
    if d:
        return d
    return os.path.join(os.environ.get("CLAUDE_CONFIG_DIR")
                        or os.path.expanduser("~/.claude"), "sessions")


def ensure_dir_0700(d):
    """comm_ensure_socket_dir semantics: exists, owned by us, mode 0700.
    Refuses a pre-created dir owned by someone else or group/world-accessible."""
    os.makedirs(d, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    st = os.stat(d)
    if st.st_uid != os.getuid():
        raise RuntimeError("refusing dir %s: not owned by us (uid=%d)" % (d, st.st_uid))
    if stat.S_IMODE(st.st_mode) != 0o700:
        raise RuntimeError("refusing dir %s: mode %o is not 700" % (d, stat.S_IMODE(st.st_mode)))


def self_device():
    n = os.environ.get("PM_SELF")
    if n:
        return n
    try:
        out = subprocess.run(["tailscale", "status", "--json"],
                             capture_output=True, text=True, timeout=5)
        dns = (json.loads(out.stdout or "{}").get("Self") or {}).get("DNSName", "")
        if dns:
            return dns.split(".")[0]
    except Exception:
        pass
    import platform
    return (platform.node().split(".")[0] or "localhost").lower()


def probe(path, timeout=0.35):
    """Measured liveness (never file-existence): connect then recv(1).
    A dead listener EOFs in milliseconds; a live one holds the line until we
    time out. No socket / connection refused => dead."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(path)
        try:
            b = s.recv(1)
            return "dead" if b == b"" else "live"
        except socket.timeout:
            return "live"
    except OSError:
        return "dead"
    finally:
        try:
            s.close()
        except OSError:
            pass


def _atomic_write(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(data)
    os.replace(tmp, path)


def _read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


# from-name attribution inside a cross-session-message wrapper (cc_peer._wrap).
_FROM_NAME_RE = re.compile(r'<cross-session-message\b[^>]*\bfrom-name="([^"]*)"')


# ---- the daemon ----------------------------------------------------------------

class PM:
    def __init__(self):
        self.root = state_root()
        self.sockdir = sock_dir()
        self.sessdir = sessions_dir()
        self.device = self_device()
        self.pid = os.getpid()
        self.tick_s = float(os.environ.get("PM_TICK") or 2)
        self.probe_s = float(os.environ.get("PM_PROBE") or 30)
        self.mu = threading.Lock()
        self.mail_mu = threading.Lock()
        self.stop_ev = threading.Event()
        self.identities = {}   # name -> {"sock": path, "claimed_at": ts}
        self.seen = {}         # name -> set of received msg_ids (dedup)
        self.routes = {}       # latest materialized snapshot (dict)
        self.log_f = None

    # -- logging --
    def log(self, *parts):
        line = "%s %s\n" % (time.strftime("%H:%M:%S"), " ".join(str(p) for p in parts))
        try:
            self.log_f.write(line)
            self.log_f.flush()
        except Exception:
            sys.stderr.write(line)

    # -- filesystem layout --
    def path(self, *p):
        return os.path.join(self.root, *p)

    def setup_dirs(self):
        ensure_dir_0700(self.root)
        ensure_dir_0700(self.sockdir)
        for sub in ("mail", "in", "links", "out", "seen"):
            os.makedirs(self.path(sub), exist_ok=True)
        self.log_f = open(self.path("daemon.log"), "a", encoding="utf-8")

    # -- singleton --
    def acquire_singleton(self):
        lockdir = self.path("daemon.lock")
        pidfile = self.path("daemon.pid")
        for attempt in (1, 2):
            try:
                os.mkdir(lockdir)
                break
            except FileExistsError:
                old = _read_json(pidfile, None)
                oldpid = None
                try:
                    with open(pidfile) as f:
                        oldpid = int(f.read().strip())
                except Exception:
                    pass
                if oldpid:
                    try:
                        os.kill(oldpid, 0)
                        sys.stderr.write("postmaster already running (pid %d)\n" % oldpid)
                        sys.exit(3)
                    except ProcessLookupError:
                        pass  # stale
                # stale lock: reclaim
                try:
                    os.rmdir(lockdir)
                except OSError:
                    pass
                if attempt == 2:
                    sys.stderr.write("could not acquire daemon lock\n")
                    sys.exit(3)
        with open(pidfile, "w") as f:
            f.write(str(self.pid))

    # -- sockets --
    def bind_unix(self, path, backlog=64):
        if os.path.exists(path):
            os.unlink(path)
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(path)
        os.chmod(path, 0o600)
        srv.listen(backlog)
        return srv

    # -- identities ------------------------------------------------------------
    #
    # A claimed identity is: a STABLE socket <sockdir>/pm-<name>.sock (bound for
    # the daemon's whole life — cached senders and links keep working), a
    # sweep-proof sidecar (our live pid; version "communicate-pm" so the
    # reconciler can tell our plants from real sessions), and a mailbox dir.

    _NAME_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}$")
    _RESERVED = {"pm", "self", "all", "postmaster"}

    def identity_sock(self, name):
        return os.path.join(self.sockdir, "pm-%s.sock" % name)

    def sidecar_path(self, name):
        return os.path.join(self.sessdir, "pm-%s.json" % name)

    def _persist_identities(self):
        with self.mu:
            data = {n: {"claimed_at": e["claimed_at"]}
                    for n, e in self.identities.items()}
        _atomic_write(self.path("identities.json"), json.dumps(data, indent=1))

    def _plant(self, name):
        """Write the sidecar (compact separators — claude.sh greps it raw)."""
        ent = self.identities.get(name)
        if not ent:
            return
        obj = cc_peer._sidecar_obj(ent["sock"], name, self.pid)
        obj["version"] = "communicate-pm"
        os.makedirs(self.sessdir, exist_ok=True)
        tmp = self.sidecar_path(name) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, separators=(",", ":"))
        os.replace(tmp, self.sidecar_path(name))

    def _unplant(self, name):
        try:
            os.unlink(self.sidecar_path(name))
        except OSError:
            pass

    def _identity_server(self, name, srv):
        while not self.stop_ev.is_set():
            try:
                conn, _ = srv.accept()
            except OSError:
                return  # released or shutting down
            threading.Thread(target=self._identity_conn, args=(name, conn),
                             daemon=True).start()

    def _identity_conn(self, name, conn):
        raw = cc_peer._read_line(conn, timeout=2.0)
        try:
            conn.close()
        except OSError:
            pass
        line = raw.split(b"\n", 1)[0].strip()
        if not line:
            return  # liveness probe
        try:
            msg = json.loads(line.decode("utf-8", "replace"))
        except Exception:
            return
        if msg.get("type") != "user":
            return
        content = cc_peer._extract_text((msg.get("message") or {}).get("content"))
        if not content:
            return
        m = _FROM_NAME_RE.search(content)
        entry = {"ts": time.time(),
                 "msg_id": msg.get("msg_id") or uuid.uuid4().hex,
                 "from": cc_peer._addr_from(msg.get("from")) or "",
                 "from_name": m.group(1) if m else None,
                 "text": cc_peer._WRAP_INNER(content)}
        if self._store(name, entry):
            self.log("stored for", name, "msg_id", entry["msg_id"])
            try:
                self._deliver_pending(name)
            except Exception as e:
                self.log("wake failed:", name, e)

    # -- mail (the durable address) ---------------------------------------------

    def mail_dir(self, name):
        return self.path("mail", name)

    def inbox_path(self, name):
        return os.path.join(self.mail_dir(name), "inbox.jsonl")

    def cursor_path(self, name):
        return os.path.join(self.mail_dir(name), ".cursor")

    def _read_cursor(self, name):
        try:
            with open(self.cursor_path(name)) as f:
                return int(f.read().strip() or 0)
        except (OSError, ValueError):
            return 0

    def _write_cursor(self, name, v):
        _atomic_write(self.cursor_path(name), str(int(v)))

    def _inbox_lines(self, name):
        try:
            with open(self.inbox_path(name), encoding="utf-8") as f:
                return f.readlines()
        except OSError:
            return []

    def _seed_seen(self, name):
        ids = set()
        for line in self._inbox_lines(name)[-200:]:
            try:
                mid = json.loads(line).get("msg_id")
                if mid:
                    ids.add(mid)
            except Exception:
                continue
        self.seen[name] = ids

    def _store(self, name, entry):
        """Durably append one inbox line. Returns False on a duplicate msg_id.
        The append happens BEFORE any delivery attempt — durability first."""
        with self.mail_mu:
            ids = self.seen.setdefault(name, set())
            if entry["msg_id"] in ids:
                return False
            os.makedirs(self.mail_dir(name), exist_ok=True)
            with open(self.inbox_path(name), "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
                f.flush()
            ids.add(entry["msg_id"])
        return True

    # -- store→wake ---------------------------------------------------------------

    def _scan_sidecars(self):
        """One pass over the sessions dir -> {name: [real session sidecars]}.
        'Real' = not one of our own plants, pid alive, socket file present."""
        out = {}
        try:
            files = os.listdir(self.sessdir)
        except OSError:
            return out
        for fn in files:
            if not fn.endswith(".json"):
                continue
            d = _read_json(os.path.join(self.sessdir, fn), None)
            if not isinstance(d, dict) or d.get("version") == "communicate-pm":
                continue
            name, pid, sock = d.get("name"), d.get("pid"), d.get("messagingSocketPath")
            if not name or not pid or not sock:
                continue
            try:
                os.kill(int(pid), 0)
            except (OSError, ValueError):
                continue
            if not os.path.exists(sock):
                continue
            out.setdefault(name, []).append(d)
        return out

    @staticmethod
    def _choose_session(cands):
        """Collision rule (names DO collide in the wild): prefer interactive,
        then newest startedAt. Deterministic, so the choice doesn't flap."""
        cands = sorted(cands, key=lambda d: (d.get("kind") == "interactive",
                                             d.get("startedAt") or 0), reverse=True)
        return cands[0] if cands else None

    def _deliver_pending(self, name, sess=None):
        """Drain undelivered inbox lines into the live session, in order, as
        protocol turns (an inbound message wakes an idle session). The cursor
        advances only after a successful socket write — at-least-once."""
        with self.mail_mu:
            lines = self._inbox_lines(name)
            cur = self._read_cursor(name)
        if cur >= len(lines):
            return
        if sess is None:
            sess = self._choose_session(self._scan_sidecars().get(name) or [])
        if not sess:
            return
        to_sock = sess.get("messagingSocketPath")
        for i in range(cur, len(lines)):
            try:
                obj = json.loads(lines[i])
            except Exception:
                obj = {"text": lines[i].strip()}
            frm = obj.get("from") or self.identity_sock(name)
            try:
                cc_peer.deliver(to_sock, obj.get("text") or "", frm,
                                from_name=obj.get("from_name") or None)
            except OSError as e:
                # Listener gone mid-drain: hold the rest, stay addressable.
                self.log("deliver to", name, "failed (hold):", e)
                self._plant(name)
                return
            with self.mail_mu:
                self._write_cursor(name, i + 1)
        self.log("drained", len(lines) - cur, "message(s) to live", name)

    def _do_send(self, to, text, from_name):
        if not text:
            return {"ok": False, "err": "empty message"}
        with self.mu:
            local = to in self.identities
        if local:
            entry = {"ts": time.time(), "msg_id": uuid.uuid4().hex, "from": "",
                     "from_name": from_name, "text": text}
            self._store(to, entry)
            try:
                self._deliver_pending(to)
            except Exception as e:
                self.log("wake failed:", to, e)
            with self.mail_mu:
                routed = ("live" if self._read_cursor(to) >= len(self._inbox_lines(to))
                          else "inbox")
            return {"ok": True, "routed": routed}
        return {"ok": False,
                "err": "unknown identity: %s (not claimed here; device links land later)" % to}

    def _do_claim(self, name):
        if not self._NAME_RE.match(name or ""):
            return {"ok": False, "err": "invalid name (want [a-z0-9][a-z0-9._-]{0,63})"}
        if name in self._RESERVED:
            return {"ok": False, "err": "'%s' is reserved" % name}
        with self.mu:
            if name in self.identities:
                return {"ok": True, "already": True}
        sock = self.identity_sock(name)
        srv = self.bind_unix(sock)
        ent = {"sock": sock, "claimed_at": time.time(), "_srv": srv}
        with self.mu:
            self.identities[name] = ent
        os.makedirs(self.path("mail", name), exist_ok=True)
        self._seed_seen(name)
        self._plant(name)
        threading.Thread(target=self._identity_server, args=(name, srv),
                         daemon=True).start()
        self._persist_identities()
        self.log("claimed identity:", name, "->", sock)
        return {"ok": True}

    def _do_release(self, name):
        with self.mu:
            ent = self.identities.pop(name, None)
        if not ent:
            return {"ok": False, "err": "not claimed: %s" % name}
        try:
            ent["_srv"].close()
        except OSError:
            pass
        try:
            os.unlink(ent["sock"])
        except OSError:
            pass
        self._unplant(name)
        self._persist_identities()
        self.log("released identity:", name)
        return {"ok": True}

    def _load_identities(self):
        data = _read_json(self.path("identities.json"), {})
        for name in sorted(data):
            r = self._do_claim(name)
            if not r.get("ok"):
                self.log("re-claim failed:", name, r.get("err"))

    # -- status --
    def build_status(self, fresh_probe=True):
        socks = {}
        own = {"pm.sock": self.path("pm.sock")}
        with self.mu:
            for name, ent in self.identities.items():
                own["pm-%s.sock" % name] = ent["sock"]
        for label, p in own.items():
            st = probe(p) if fresh_probe else "unknown"
            socks[label] = {"path": p, "state": st, "provenance": "probed",
                            "ts": time.time()}
        with self.mu:
            idents = {n: {"kind": "local", "sock": e["sock"],
                          "claimed_at": e["claimed_at"]}
                      for n, e in self.identities.items()}
        return {"ok": True,
                "self": {"device": self.device, "pid": self.pid,
                         "state_root": self.root, "sock_dir": self.sockdir,
                         "socks": socks},
                "identities": idents,
                "links": {},
                "ts": time.time()}

    def write_routes(self):
        try:
            _atomic_write(self.path("routes.json"),
                          json.dumps(self.build_status(fresh_probe=True), indent=1))
        except Exception as e:
            self.log("routes.json write failed:", e)

    # -- control ops --
    def op(self, req):
        op = req.get("op")
        if op == "status":
            return self.build_status()
        if op == "claim":
            return self._do_claim(req.get("name", ""))
        if op == "release":
            return self._do_release(req.get("name", ""))
        if op == "send":
            return self._do_send(req.get("to", ""), req.get("text", ""),
                                 req.get("from") or "cli")
        if op == "stop":
            threading.Thread(target=self._delayed_shutdown, daemon=True).start()
            return {"ok": True, "stopping": True}
        return {"ok": False, "err": "unknown op: %r" % op}

    def _delayed_shutdown(self):
        time.sleep(0.2)  # let the stop reply flush to the client first
        self.shutdown()

    def control_server(self):
        srv = self.bind_unix(self.path("pm.sock"))
        while not self.stop_ev.is_set():
            try:
                conn, _ = srv.accept()
            except OSError:
                if self.stop_ev.is_set():
                    return
                continue
            threading.Thread(target=self._control_conn, args=(conn,), daemon=True).start()

    def _control_conn(self, conn):
        raw = cc_peer._read_line(conn, timeout=2.0)
        line = raw.split(b"\n", 1)[0].strip()
        if not line:
            try:
                conn.close()
            except OSError:
                pass
            return  # liveness probe
        try:
            req = json.loads(line.decode("utf-8", "replace"))
        except Exception:
            req = {}
        try:
            resp = self.op(req)
        except Exception as e:
            self.log("op failed:", repr(req)[:200], e)
            resp = {"ok": False, "err": str(e)}
        try:
            conn.sendall((json.dumps(resp) + "\n").encode("utf-8"))
        except OSError:
            pass
        try:
            conn.close()
        except OSError:
            pass

    # -- tick loop --
    def tick_loop(self):
        last_probe = 0.0
        while not self.stop_ev.wait(self.tick_s):
            try:
                self.reconcile()
            except Exception as e:
                self.log("reconcile failed:", e)
            now = time.time()
            if now - last_probe >= self.probe_s:
                last_probe = now
                self.write_routes()

    def reconcile(self):
        """The store→wake heart, every tick: for each claimed identity, if a
        REAL session owns the name, step aside (unplant, so local discovery
        resolves to the session) and drain held mail into it; if none does,
        keep our sweep-proof sidecar planted and hold mail durably."""
        smap = self._scan_sidecars()
        with self.mu:
            names = list(self.identities)
        for name in names:
            sess = self._choose_session(smap.get(name) or [])
            if sess:
                self._unplant(name)
                try:
                    self._deliver_pending(name, sess)
                except Exception as e:
                    self.log("drain failed:", name, e)
            else:
                try:
                    self._plant(name)
                except Exception as e:
                    self.log("plant failed:", name, e)

    # -- lifecycle --
    def shutdown(self, *_a):
        self.stop_ev.set()
        paths = [self.path("pm.sock")]
        with self.mu:
            names = list(self.identities)
            for ent in self.identities.values():
                paths.append(ent["sock"])
        for n in names:
            self._unplant(n)
        for p in paths:
            try:
                os.unlink(p)
            except OSError:
                pass
        for p in (self.path("daemon.pid"),):
            try:
                os.unlink(p)
            except OSError:
                pass
        try:
            os.rmdir(self.path("daemon.lock"))
        except OSError:
            pass
        self.log("postmaster stopped")
        os._exit(0)

    def run(self):
        self.setup_dirs()
        self.acquire_singleton()
        signal.signal(signal.SIGTERM, self.shutdown)
        signal.signal(signal.SIGINT, self.shutdown)
        self.log("postmaster starting: device=%s pid=%d root=%s sockdir=%s"
                 % (self.device, self.pid, self.root, self.sockdir))
        threading.Thread(target=self.control_server, daemon=True).start()
        self._load_identities()
        self.write_routes()
        self.tick_loop()
        self.shutdown()


# ---- CLI client ("call") -------------------------------------------------------

def _call(req, timeout=10.0):
    path = os.path.join(state_root(), "pm.sock")
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(path)
    except OSError:
        sys.stderr.write("postmaster not running (no listener at %s)\n" % path)
        sys.exit(2)
    try:
        s.sendall((json.dumps(req) + "\n").encode("utf-8"))
        try:
            s.shutdown(socket.SHUT_WR)
        except OSError:
            pass
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    finally:
        s.close()
    line = buf.split(b"\n", 1)[0].strip()
    if not line:
        sys.stderr.write("no reply from postmaster\n")
        sys.exit(2)
    return json.loads(line.decode("utf-8", "replace"))


def _human_status(st):
    self_ = st.get("self", {})
    out = ["postmaster @ %s (pid %s)" % (self_.get("device"), self_.get("pid"))]
    for label, s in sorted((self_.get("socks") or {}).items()):
        out.append("  %-28s %-5s [%s]" % (label, s.get("state"), s.get("provenance")))
    idents = st.get("identities") or {}
    if idents:
        out.append("identities:")
        for n, e in sorted(idents.items()):
            out.append("  %-24s %s" % (n, e.get("sock", "")))
    return "\n".join(out)


def cli_call(argv):
    if not argv:
        sys.stderr.write("usage: postmaster.py call <op> [args...]\n")
        return 1
    op, args = argv[0], argv[1:]
    if op == "status":
        st = _call({"op": "status"})
        if "--json" in args:
            print(json.dumps(st, indent=1))
        else:
            print(_human_status(st))
        return 0 if st.get("ok") else 1
    if op == "stop":
        r = _call({"op": "stop"})
        print("stopped" if r.get("ok") else json.dumps(r))
        return 0 if r.get("ok") else 1
    if op in ("claim", "release"):
        if not args:
            sys.stderr.write("usage: communicate pm %s <name>\n" % op)
            return 1
        r = _call({"op": op, "name": args[0]})
        if r.get("ok"):
            print("%s %s" % (op + ("ed" if op == "claim" else "d"), args[0]))
            return 0
        sys.stderr.write((r.get("err") or "failed") + "\n")
        return 1
    if op == "send":
        frm = "cli"
        if "--from" in args:
            i = args.index("--from")
            try:
                frm = args[i + 1]
            except IndexError:
                sys.stderr.write("--from needs a value\n")
                return 1
            args = args[:i] + args[i + 2:]
        if len(args) < 2:
            sys.stderr.write("usage: communicate pm send <name> <message...> [--from NAME]\n")
            return 1
        r = _call({"op": "send", "to": args[0], "text": " ".join(args[1:]),
                   "from": frm})
        if r.get("ok"):
            print("routed: %s" % r.get("routed"))
            return 0
        sys.stderr.write((r.get("err") or "failed") + "\n")
        return 1
    sys.stderr.write("unknown op: %s\n" % op)
    return 1


def main():
    if len(sys.argv) < 2:
        sys.stderr.write("usage: postmaster.py {daemon|call ...}\n")
        sys.exit(1)
    mode = sys.argv[1]
    if mode == "daemon":
        PM().run()
    elif mode == "call":
        sys.exit(cli_call(sys.argv[2:]))
    else:
        sys.stderr.write("unknown mode: %s\n" % mode)
        sys.exit(1)


if __name__ == "__main__":
    main()
