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
        self.stop_ev = threading.Event()
        self.identities = {}   # name -> {"sock": path, "claimed_at": ts}
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
            idents = {n: dict(e) for n, e in self.identities.items()}
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
        pass  # store→wake lands in a later task

    # -- lifecycle --
    def shutdown(self, *_a):
        self.stop_ev.set()
        paths = [self.path("pm.sock")]
        with self.mu:
            for ent in self.identities.values():
                paths.append(ent["sock"])
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
