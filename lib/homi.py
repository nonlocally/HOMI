#!/usr/bin/env python3
"""homi — the communicate fabric daemon (agent-fabric v1).

One per-device daemon that gives agents durable identity and a durable
address, independent of any process or session:

  * identities   `claim <name>` binds a STABLE socket <sockdir>/homi-<name>.sock
                 and plants a sweep-proof Claude sidecar, so the name is
                 messageable (SendMessage / cc-socks) even when no session
                 backs it. Never pid-derived paths.
  * mailboxes    every inbound frame is appended durably to
                 $HOMI_STATE/mail/<name>/inbox.jsonl before anything else.
  * store→wake   when a REAL session with that name is alive (sidecar name
                 match + live socket), the homi unplants its own sidecar
                 and drains undelivered mail into the session as protocol
                 turns; when the session dies it replants and holds.
  * liveness     measured, never inferred from a file existing. probe =
                 connect + recv(1): fast EOF => dead, timeout => a live
                 listener holds the line. The homi probes ITS OWN
                 published sockets too (self-probe), provenance-labelled.
  * links        homi↔homi, one per device pair. Outbound only
                 (`ssh -N -L` toward the peer's inbound socket for THIS
                 device) so inbound and outbound fail independently.

State layout ($HOMI_STATE = ${COMM_STATE:-~/.local/state/communicate}/homi):
  daemon.pid daemon.log daemon.lock/   singleton bookkeeping
  homi.sock                              control socket (CLI ops, one JSON/conn)
  identities.json                      claimed names (reloaded on start)
  mail/<name>/inbox.jsonl + .cursor    the durable address
  in/<device>.sock                     link inbound (arrival-line attribution)
  links.json  links/<device>.sock      link outbound state + local ends
  out/<device>/<ts>-<id>.json          outbound queue (at-least-once + ack)
  seen/<device>                        dedup ring of received msg_ids
  routes.json                          materialized routes + measured liveness

Env: COMM_STATE, HOMI_SOCK_DIR, HOMI_SESSIONS_DIR, HOMI_SELF, HOMI_TICK, HOMI_PROBE.
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
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cc_peer  # deliver, _sidecar_obj, _read_line, _extract_text, _addr_from


# ---- paths / env -------------------------------------------------------------

def state_root():
    base = os.environ.get("COMM_STATE") or os.path.join(
        os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"),
        "communicate")
    return os.path.join(base, "homi")


def sock_dir():
    d = os.environ.get("HOMI_SOCK_DIR")
    if d:
        return d
    m = os.environ.get("CLAUDE_CODE_MESSAGING_SOCKET")
    if m:
        return os.path.dirname(m)
    return os.path.join(os.environ.get("XDG_RUNTIME_DIR") or "/tmp", "cc-socks")


def sessions_dir():
    d = os.environ.get("HOMI_SESSIONS_DIR")
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
    n = os.environ.get("HOMI_SELF")
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

class Homi:
    def __init__(self):
        self.root = state_root()
        self.sockdir = sock_dir()
        self.sessdir = sessions_dir()
        self.device = self_device()
        self.pid = os.getpid()
        self.tick_s = float(os.environ.get("HOMI_TICK") or 2)
        self.probe_s = float(os.environ.get("HOMI_PROBE") or 30)
        self.mu = threading.Lock()
        self.mail_mu = threading.Lock()
        self.stop_ev = threading.Event()
        self.identities = {}   # name -> {"sock", "claimed_at", "kind", ["home"]}
        self.seen = {}         # name -> set of received msg_ids (dedup)
        self.links = {}        # device -> {"addr", "sock", "created_at"}
        self.link_in = {}      # device -> inbound server socket (arrival line)
        self.seen_dev = {}     # device -> set of received envelope msg_ids
        self.link_state = {}   # device -> {"backoff_s","backoff_until","last_ok","last_err"}
        self.ssh_procs = {}    # device -> Popen of the ssh -N -L child
        self.out_ev = threading.Event()
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
                        sys.stderr.write("homi already running (pid %d)\n" % oldpid)
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
    # A claimed identity is: a STABLE socket <sockdir>/homi-<name>.sock (bound for
    # the daemon's whole life — cached senders and links keep working), a
    # sweep-proof sidecar (our live pid; version "communicate-homi" so the
    # reconciler can tell our plants from real sessions), and a mailbox dir.

    _NAME_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}$")
    _RESERVED = {"pm", "self", "all", "homi"}

    def identity_sock(self, name):
        return os.path.join(self.sockdir, "homi-%s.sock" % name)

    def sidecar_path(self, name):
        """Discovery only LISTS sidecars whose filename is pid-shaped (verified
        live 2026-08-15: a homi-<name>.json plant survives the sweep but never
        appears in ListAgents; the same object as 3999901.json is listed).
        So: deterministic numeric filenames well above any real pid
        (macOS pid_max is 99998), linear-probed on cross-name collision."""
        n = 3000000 + (zlib.crc32(name.encode("utf-8")) % 900000)
        while True:
            p = os.path.join(self.sessdir, "%d.json" % n)
            d = _read_json(p, None)
            if d is None or d.get("name") == name:
                return p
            n += 1

    def _persist_identities(self):
        with self.mu:
            data = {n: {"claimed_at": e["claimed_at"],
                        "kind": e.get("kind", "local"),
                        "home": e.get("home")}
                    for n, e in self.identities.items()}
        _atomic_write(self.path("identities.json"), json.dumps(data, indent=1))

    def _plant(self, name):
        """Write the sidecar (compact separators — claude.sh greps it raw)."""
        ent = self.identities.get(name)
        if not ent:
            return
        obj = cc_peer._sidecar_obj(ent["sock"], name, self.pid)
        obj["version"] = "communicate-homi"
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
        with self.mu:
            ent = self.identities.get(name) or {}
            kind, home = ent.get("kind", "local"), ent.get("home")
        if kind == "proxy":
            # A frame to a remote peer: attribute the sender (session name by
            # reverse socket lookup, else the wrapper's from-name) and queue
            # the envelope toward the identity's home device.
            frm_id = (self._name_for_socket(entry["from"])
                      or entry["from_name"] or "unknown")
            self._queue_out(home, {"v": 1, "kind": "m", "to": name,
                                   "from": frm_id, "msg_id": entry["msg_id"],
                                   "text": entry["text"], "ts": entry["ts"]})
            self.log("queued for", "%s@%s" % (name, home), "from", frm_id)
            return
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
            if not isinstance(d, dict) or d.get("version") == "communicate-homi":
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
        name, dev = (to.split("@", 1) if "@" in to else (to, None))
        if dev == self.device:
            dev = None
        with self.mu:
            ent = self.identities.get(name)
            kind = ent.get("kind") if ent else None
            home = ent.get("home") if ent else None
        if dev is None and kind == "proxy":
            dev = home  # a known remote peer is addressable by bare name
        if dev:
            with self.mu:
                have_link = dev in self.links
            if not have_link:
                return {"ok": False, "err": "device not linked: %s" % dev}
            env = {"v": 1, "kind": "m", "to": name, "from": from_name,
                   "msg_id": uuid.uuid4().hex, "text": text, "ts": time.time()}
            self._queue_out(dev, env)
            return {"ok": True, "routed": "link:%s" % dev}
        if kind == "local":
            entry = {"ts": time.time(), "msg_id": uuid.uuid4().hex, "from": "",
                     "from_name": from_name, "text": text}
            self._store(name, entry)
            try:
                self._deliver_pending(name)
            except Exception as e:
                self.log("wake failed:", name, e)
            with self.mail_mu:
                routed = ("live" if self._read_cursor(name) >= len(self._inbox_lines(name))
                          else "inbox")
            return {"ok": True, "routed": routed}
        return {"ok": False,
                "err": "unknown identity: %s (claim it here, or address <name>@<device>)" % name}

    def _do_claim(self, name):
        if not self._NAME_RE.match(name or ""):
            return {"ok": False, "err": "invalid name (want [a-z0-9][a-z0-9._-]{0,63})"}
        if name in self._RESERVED:
            return {"ok": False, "err": "'%s' is reserved" % name}
        with self.mu:
            ent = self.identities.get(name)
        if ent is not None and ent.get("kind") == "proxy":
            # A local claim outranks a remote proxy for the same name.
            self._do_release(name)
        elif ent is not None:
            return {"ok": True, "already": True}
        sock = self.identity_sock(name)
        srv = self.bind_unix(sock)
        ent = {"sock": sock, "claimed_at": time.time(), "_srv": srv, "kind": "local"}
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

    def _rebind(self, name):
        with self.mu:
            ent = self.identities.get(name)
        if not ent:
            return
        try:
            ent["_srv"].close()
        except (OSError, KeyError):
            pass
        srv = self.bind_unix(ent["sock"])
        ent["_srv"] = srv
        threading.Thread(target=self._identity_server, args=(name, srv),
                         daemon=True).start()

    def _load_identities(self):
        data = _read_json(self.path("identities.json"), {})
        for name in sorted(data):
            e = data.get(name) or {}
            if e.get("kind") == "proxy" and e.get("home"):
                if not self._ensure_proxy(name, e["home"]):
                    self.log("re-proxy failed:", name)
                continue
            r = self._do_claim(name)
            if not r.get("ok"):
                self.log("re-claim failed:", name, r.get("err"))

    def _name_for_socket(self, sock_path):
        """Reverse-resolve a sender socket to a session name (for envelope
        attribution when a local session messages a proxy identity)."""
        if not sock_path:
            return None
        try:
            files = os.listdir(self.sessdir)
        except OSError:
            return None
        for fn in files:
            if not fn.endswith(".json"):
                continue
            d = _read_json(os.path.join(self.sessdir, fn), None)
            if isinstance(d, dict) and d.get("messagingSocketPath") == sock_path:
                return d.get("name")
        return None

    def _ensure_proxy(self, name, device):
        """A remote sender becomes a local proxy peer: a stable socket + a
        planted sidecar, so local sessions list it and reply to it by name.
        Frames arriving on a proxy socket are queued out to its home device.
        Never shadows a locally-claimed identity."""
        if not self._NAME_RE.match(name or "") or name in self._RESERVED:
            return None
        with self.mu:
            ent = self.identities.get(name)
            if ent is not None:
                return ent if ent.get("kind") == "proxy" else None
        sock = self.identity_sock(name)
        try:
            srv = self.bind_unix(sock)
        except OSError as e:
            self.log("proxy bind failed:", name, e)
            return None
        ent = {"sock": sock, "claimed_at": time.time(), "_srv": srv,
               "kind": "proxy", "home": device}
        with self.mu:
            self.identities[name] = ent
        self._plant(name)
        threading.Thread(target=self._identity_server, args=(name, srv),
                         daemon=True).start()
        self._persist_identities()
        self.log("proxy identity:", name, "home", device)
        return ent

    # -- links (homi ↔ homi) ------------------------------------------
    #
    # One link per device pair; each side manages only its OUTBOUND half, so
    # inbound and outbound fail independently. Envelopes arrive on a
    # per-device inbound socket (in/<device>.sock) — attribution derives from
    # the ARRIVAL LINE, not a sender-claimed string. Delivery is
    # at-least-once: files queue under out/<device>/ until the far homi
    # acks the msg_id; receivers dedup on a per-device ring.

    _DEV_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}$")

    def link_in_sock(self, device):
        return self.path("in", device + ".sock")

    def _persist_links(self):
        with self.mu:
            data = {d: {"addr": e.get("addr"), "sock": e.get("sock"),
                        "remote_home": e.get("remote_home"),
                        "created_at": e.get("created_at")}
                    for d, e in self.links.items()}
        _atomic_write(self.path("links.json"), json.dumps(data, indent=1))

    def _seed_seen_dev(self, device):
        ids = set()
        try:
            with open(self.path("seen", device), encoding="utf-8") as f:
                for line in f.readlines()[-500:]:
                    line = line.strip()
                    if line:
                        ids.add(line)
        except OSError:
            pass
        self.seen_dev[device] = ids

    def _remember_dev_msg(self, device, msg_id):
        self.seen_dev.setdefault(device, set()).add(msg_id)
        try:
            with open(self.path("seen", device), "a", encoding="utf-8") as f:
                f.write(msg_id + "\n")
        except OSError:
            pass

    def _ssh_cmd(self, device, addr, remote_home):
        """The exact outbound dial: BatchMode (auth failures fail cleanly,
        never prompt), forward-only -L toward the peer's inbound socket FOR
        THIS device, StreamLocalBindUnlink so a dead tunnel's socket litter
        never blocks the redial."""
        rin = "%s/.local/state/communicate/homi/in/%s.sock" % (
            remote_home or "<REMOTE_HOME>", self.device)
        lsock = self.path("links", device + ".sock")
        return ["ssh", "-N",
                "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
                "-o", "ExitOnForwardFailure=yes",
                "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
                "-o", "StreamLocalBindMask=0177",
                "-o", "StreamLocalBindUnlink=yes",
                "-L", "%s:%s" % (lsock, rin), addr]

    def _remote_home(self, addr):
        try:
            out = subprocess.run(["ssh", "-o", "BatchMode=yes",
                                  "-o", "ConnectTimeout=8", addr, "echo $HOME"],
                                 capture_output=True, text=True, timeout=15)
            home = (out.stdout or "").strip().splitlines()
            return home[-1] if home and out.returncode == 0 else None
        except Exception:
            return None

    def _ensure_ssh(self, device):
        """Keep one ssh -N -L child alive per addr-linked device; returns the
        local end of the forward. Raises OSError when the dial fails (the
        outbound loop turns that into per-device backoff)."""
        with self.mu:
            ent = dict(self.links.get(device) or {})
        addr = ent.get("addr")
        if not addr:
            return None
        lsock = self.path("links", device + ".sock")
        proc = self.ssh_procs.get(device)
        if proc is not None and proc.poll() is None and os.path.exists(lsock):
            return lsock
        if proc is not None and proc.poll() is not None:
            self.ssh_procs.pop(device, None)
        rhome = ent.get("remote_home")
        if not rhome:
            rhome = self._remote_home(addr)
            if not rhome:
                raise OSError("cannot resolve remote $HOME on %s" % addr)
            with self.mu:
                if device in self.links:
                    self.links[device]["remote_home"] = rhome
            self._persist_links()
        try:
            os.unlink(lsock)
        except OSError:
            pass
        cmd = self._ssh_cmd(device, addr, rhome)
        self.log("link", device, "dialing:", " ".join(cmd))
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, start_new_session=True)
        self.ssh_procs[device] = proc
        deadline = time.time() + 10
        while time.time() < deadline:
            if os.path.exists(lsock):
                return lsock
            if proc.poll() is not None:
                break
            time.sleep(0.2)
        raise OSError("ssh link to %s did not come up" % device)

    def _do_link(self, device, addr=None, sock=None, print_cmd=False):
        if not self._DEV_RE.match(device or ""):
            return {"ok": False, "err": "invalid device name"}
        if device == self.device:
            return {"ok": False, "err": "refusing to link to self"}
        if print_cmd:
            with self.mu:
                stored = dict(self.links.get(device) or {})
            return {"ok": True,
                    "cmd": self._ssh_cmd(device, addr or stored.get("addr") or "<ADDR>",
                                         stored.get("remote_home"))}
        if device not in self.link_in:
            srv = self.bind_unix(self.link_in_sock(device))
            self.link_in[device] = srv
            threading.Thread(target=self._link_server, args=(device, srv),
                             daemon=True).start()
        self._seed_seen_dev(device)
        os.makedirs(self.path("out", device), exist_ok=True)
        with self.mu:
            prev = self.links.get(device) or {}
            self.links[device] = {"addr": addr, "sock": sock,
                                  "remote_home": prev.get("remote_home"),
                                  "created_at": time.time()}
        self._persist_links()
        self.out_ev.set()
        self.log("linked device:", device, "->", sock or addr or "?")
        return {"ok": True}

    def _do_unlink(self, device):
        with self.mu:
            ent = self.links.pop(device, None)
        proc = self.ssh_procs.pop(device, None)
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass
        try:
            os.unlink(self.path("links", device + ".sock"))
        except OSError:
            pass
        srv = self.link_in.pop(device, None)
        if srv:
            try:
                srv.close()
            except OSError:
                pass
            try:
                os.unlink(self.link_in_sock(device))
            except OSError:
                pass
        if not ent:
            return {"ok": False, "err": "not linked: %s" % device}
        self._persist_links()
        self.log("unlinked device:", device)
        return {"ok": True}

    def _load_links(self):
        data = _read_json(self.path("links.json"), {})
        for device, e in sorted(data.items()):
            r = self._do_link(device, addr=(e or {}).get("addr"),
                              sock=(e or {}).get("sock"))
            if not r.get("ok"):
                self.log("re-link failed:", device, r.get("err"))

    def _link_server(self, device, srv):
        while not self.stop_ev.is_set():
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            threading.Thread(target=self._link_conn, args=(device, conn),
                             daemon=True).start()

    def _link_conn(self, device, conn):
        raw = cc_peer._read_line(conn, timeout=2.0)
        line = raw.split(b"\n", 1)[0].strip()
        resp = None
        if line:
            try:
                env = json.loads(line.decode("utf-8", "replace"))
            except Exception:
                env = None
            if isinstance(env, dict):
                try:
                    resp = self._recv_envelope(device, env)
                except Exception as e:
                    self.log("recv envelope failed:", device, e)
                    resp = {"ok": False, "err": str(e)}
        if resp is not None:
            try:
                conn.sendall((json.dumps(resp) + "\n").encode("utf-8"))
            except OSError:
                pass
        try:
            conn.close()
        except OSError:
            pass

    def _recv_envelope(self, device, env):
        if env.get("v") != 1 or env.get("kind") != "m":
            return {"ok": False, "err": "bad envelope"}
        to = env.get("to") or ""
        frm = env.get("from") or "unknown"
        mid = env.get("msg_id") or ""
        text = env.get("text") or ""
        if not to or not mid or not text:
            return {"ok": False, "err": "bad envelope"}
        if mid in self.seen_dev.setdefault(device, set()):
            return {"ok": True, "ack": mid, "dup": True}
        self._ensure_proxy(frm, device)
        with self.mu:
            ent = self.identities.get(to)
            kind = ent.get("kind") if ent else None
        if kind == "proxy":
            return {"ok": False,
                    "err": "%s is not local here (multi-hop not supported)" % to}
        if ent is None:
            r = self._do_claim(to)  # auto-claim: local mail never bounces
            if not r.get("ok"):
                return {"ok": False, "err": "cannot claim %s: %s" % (to, r.get("err"))}
            self.log("auto-claimed", to, "for inbound mail via", device)
        entry = {"ts": time.time(), "msg_id": mid, "from": "",
                 "from_name": frm, "via": device, "text": text}
        self._store(to, entry)
        self._remember_dev_msg(device, mid)
        try:
            self._deliver_pending(to)
        except Exception as e:
            self.log("wake failed:", to, e)
        return {"ok": True, "ack": mid}

    def _queue_out(self, device, env):
        d = self.path("out", device)
        os.makedirs(d, exist_ok=True)
        fn = "%016d-%s.json" % (int(env.get("ts", time.time()) * 1000), env["msg_id"])
        _atomic_write(os.path.join(d, fn), json.dumps(env))
        self.out_ev.set()

    def _link_endpoint(self, device):
        with self.mu:
            ent = self.links.get(device) or {}
        if ent.get("sock"):
            return ent["sock"]
        return self._ensure_ssh(device)

    def _send_envelope(self, endpoint, env):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(5)
        try:
            s.connect(endpoint)
            s.sendall((json.dumps(env) + "\n").encode("utf-8"))
            raw = cc_peer._read_line(s, timeout=5.0)
        finally:
            try:
                s.close()
            except OSError:
                pass
        line = raw.split(b"\n", 1)[0].strip()
        if not line:
            raise OSError("no ack")
        resp = json.loads(line.decode("utf-8", "replace"))
        if not (resp.get("ok") and resp.get("ack") == env["msg_id"]):
            raise OSError("bad ack: %r" % (resp,))

    def outbound_loop(self):
        """Drain out/<device>/ queues in order; ack-then-delete; exponential
        backoff per device on failure. Independent of everything inbound."""
        while not self.stop_ev.is_set():
            self.out_ev.wait(1.0)
            self.out_ev.clear()
            if self.stop_ev.is_set():
                return
            with self.mu:
                devices = list(self.links)
            now = time.time()
            for dev in devices:
                st = self.link_state.setdefault(dev, {})
                if now < st.get("backoff_until", 0):
                    continue
                qdir = self.path("out", dev)
                try:
                    files = sorted(f for f in os.listdir(qdir) if f.endswith(".json"))
                except OSError:
                    continue
                if not files:
                    continue
                try:
                    endpoint = self._link_endpoint(dev)
                except (OSError, ValueError) as e:
                    st["backoff_s"] = (1 if not st.get("backoff_s")
                                       else min(st["backoff_s"] * 2, 30))
                    st["backoff_until"] = time.time() + st["backoff_s"]
                    st["last_err"] = str(e)
                    self.log("link", dev, "dial failed (backoff %ss):"
                             % st["backoff_s"], e)
                    continue
                if not endpoint:
                    st["last_err"] = "no transport"
                    continue
                sent = 0
                for fn in files:
                    fp = os.path.join(qdir, fn)
                    env = _read_json(fp, None)
                    if not isinstance(env, dict) or not env.get("msg_id"):
                        try:
                            os.unlink(fp)
                        except OSError:
                            pass
                        continue
                    try:
                        self._send_envelope(endpoint, env)
                    except (OSError, ValueError) as e:
                        st["backoff_s"] = (1 if not st.get("backoff_s")
                                           else min(st["backoff_s"] * 2, 30))
                        st["backoff_until"] = time.time() + st["backoff_s"]
                        st["last_err"] = str(e)
                        self.log("link", dev, "send failed (backoff %ss):"
                                 % st["backoff_s"], e)
                        break
                    try:
                        os.unlink(fp)
                    except OSError:
                        pass
                    sent += 1
                if sent:
                    st["backoff_s"] = 0
                    st["backoff_until"] = 0
                    st["last_ok"] = time.time()
                    st["last_err"] = None
                    self.log("link", dev, "delivered", sent, "envelope(s)")

    # -- status --
    def build_status(self, fresh_probe=True):
        socks = {}
        own = {"homi.sock": self.path("homi.sock")}
        with self.mu:
            for name, ent in self.identities.items():
                own["homi-%s.sock" % name] = ent["sock"]
        for label, p in own.items():
            st = probe(p) if fresh_probe else "unknown"
            socks[label] = {"path": p, "state": st, "provenance": "probed",
                            "ts": time.time()}
        smap = self._scan_sidecars()
        with self.mu:
            items = [(n, e["sock"], e["claimed_at"], e.get("kind", "local"),
                      e.get("home")) for n, e in self.identities.items()]
        idents = {}
        for n, sockp, claimed, kind, home in items:
            if kind == "proxy":
                idents[n] = {"kind": "proxy", "home": home, "sock": sockp,
                             "claimed_at": claimed}
                continue
            cands = smap.get(n) or []
            sess = self._choose_session(cands)
            if sess and fresh_probe:
                alive = probe(sess["messagingSocketPath"]) == "live"
                state, prov = ("live", "probed") if alive else ("stored", "probed")
            elif sess:
                state, prov = "live", "reported"
            else:
                # Absence measured from sidecars + pid liveness, not a socket probe.
                state, prov = "stored", "reported"
            with self.mail_mu:
                count = len(self._inbox_lines(n))
                cur = self._read_cursor(n)
            idents[n] = {
                "kind": "local", "sock": sockp, "claimed_at": claimed,
                "route": {"state": state, "provenance": prov,
                          "ambiguous": len(cands) > 1,
                          "session": ({"pid": sess.get("pid"),
                                       "socket": sess.get("messagingSocketPath"),
                                       "kind": sess.get("kind"),
                                       "startedAt": sess.get("startedAt")}
                                      if sess else None)},
                "inbox": {"count": count, "undelivered": max(0, count - cur)},
            }
        with self.mu:
            linkents = {d: dict(e) for d, e in self.links.items()}
        links = {}
        for d, e in linkents.items():
            lst = self.link_state.get(d, {})
            try:
                q = len([f for f in os.listdir(self.path("out", d))
                         if f.endswith(".json")])
            except OSError:
                q = 0
            links[d] = {"endpoint": e.get("sock") or e.get("addr"),
                        "queue": q, "last_ok": lst.get("last_ok"),
                        "last_err": lst.get("last_err"),
                        "in_sock": self.link_in_sock(d)}
        return {"ok": True,
                "self": {"device": self.device, "pid": self.pid,
                         "state_root": self.root, "sock_dir": self.sockdir,
                         "socks": socks},
                "identities": idents,
                "links": links,
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
        if op == "link":
            return self._do_link(req.get("device", ""), addr=req.get("addr"),
                                 sock=req.get("sock"),
                                 print_cmd=bool(req.get("print_cmd")))
        if op == "unlink":
            return self._do_unlink(req.get("device", ""))
        if op == "stop":
            threading.Thread(target=self._delayed_shutdown, daemon=True).start()
            return {"ok": True, "stopping": True}
        return {"ok": False, "err": "unknown op: %r" % op}

    def _delayed_shutdown(self):
        time.sleep(0.2)  # let the stop reply flush to the client first
        self.shutdown()

    def control_server(self):
        srv = self.bind_unix(self.path("homi.sock"))
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
            socks = {n: self.identities[n]["sock"] for n in names}
            kinds = {n: self.identities[n].get("kind", "local") for n in names}
        for name in names:
            # Self-heal: a lost socket file means our published address is a
            # lie; re-bind before anything else (self-probe discipline).
            if not os.path.exists(socks[name]):
                self.log("identity socket lost, re-binding:", name)
                try:
                    self._rebind(name)
                except Exception as e:
                    self.log("rebind failed:", name, e)
            if kinds[name] == "proxy":
                # Proxies have no local mailbox to drain and no session to
                # defer to — they just stay listed and route outward.
                try:
                    self._plant(name)
                except Exception as e:
                    self.log("plant failed:", name, e)
                continue
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
        paths = [self.path("homi.sock")]
        with self.mu:
            names = list(self.identities)
            for ent in self.identities.values():
                paths.append(ent["sock"])
        for d, srv in list(self.link_in.items()):
            try:
                srv.close()
            except OSError:
                pass
            paths.append(self.link_in_sock(d))
        for d, proc in list(self.ssh_procs.items()):
            if proc is not None and proc.poll() is None:
                try:
                    proc.terminate()
                except OSError:
                    pass
            paths.append(self.path("links", d + ".sock"))
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
        self.log("homi stopped")
        os._exit(0)

    def run(self):
        self.setup_dirs()
        self.acquire_singleton()
        signal.signal(signal.SIGTERM, self.shutdown)
        signal.signal(signal.SIGINT, self.shutdown)
        self.log("homi starting: device=%s pid=%d root=%s sockdir=%s"
                 % (self.device, self.pid, self.root, self.sockdir))
        threading.Thread(target=self.control_server, daemon=True).start()
        self._load_identities()
        self._load_links()
        threading.Thread(target=self.outbound_loop, daemon=True).start()
        self.write_routes()
        self.tick_loop()
        self.shutdown()


# ---- CLI client ("call") -------------------------------------------------------

def _call(req, timeout=10.0):
    path = os.path.join(state_root(), "homi.sock")
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(path)
    except OSError:
        sys.stderr.write("homi not running (no listener at %s)\n" % path)
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
        sys.stderr.write("no reply from homi\n")
        sys.exit(2)
    return json.loads(line.decode("utf-8", "replace"))


def _human_status(st):
    self_ = st.get("self", {})
    out = ["homi @ %s (pid %s)" % (self_.get("device"), self_.get("pid"))]
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
        sys.stderr.write("usage: homi.py call <op> [args...]\n")
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
            sys.stderr.write("usage: communicate homi %s <name>\n" % op)
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
            sys.stderr.write("usage: communicate homi send <name> <message...> [--from NAME]\n")
            return 1
        r = _call({"op": "send", "to": args[0], "text": " ".join(args[1:]),
                   "from": frm})
        if r.get("ok"):
            print("routed: %s" % r.get("routed"))
            return 0
        sys.stderr.write((r.get("err") or "failed") + "\n")
        return 1
    if op in ("link", "unlink"):
        if not args:
            sys.stderr.write("usage: communicate homi %s <device> [--addr user@host] [--sock path]\n" % op)
            return 1
        req = {"op": op, "device": args[0]}
        if "--sock" in args:
            try:
                req["sock"] = args[args.index("--sock") + 1]
            except IndexError:
                sys.stderr.write("--sock needs a value\n")
                return 1
        if "--addr" in args:
            try:
                req["addr"] = args[args.index("--addr") + 1]
            except IndexError:
                sys.stderr.write("--addr needs a value\n")
                return 1
        if "--print-cmd" in args:
            req["print_cmd"] = True
        r = _call(req)
        if r.get("ok") and r.get("cmd"):
            print(" ".join(r["cmd"]))
            return 0
        if r.get("ok"):
            print("%sed %s" % (op, args[0]))
            return 0
        sys.stderr.write((r.get("err") or "failed") + "\n")
        return 1
    sys.stderr.write("unknown op: %s\n" % op)
    return 1


def cli_retitle(argv):
    """Dormant rename-sync (beam's method): append a custom-title record to a
    transcript so the session carries the fabric name when next resumed."""
    if len(argv) != 2:
        sys.stderr.write("usage: homi.py retitle <transcript.jsonl|session-uuid> <name>\n")
        return 1
    target, name = argv
    path = target
    if not os.path.exists(path):
        import glob
        base = os.path.join(os.environ.get("CLAUDE_CONFIG_DIR")
                            or os.path.expanduser("~/.claude"), "projects")
        hits = glob.glob(os.path.join(base, "*", target + ".jsonl"))
        if not hits:
            sys.stderr.write("no transcript found for %s\n" % target)
            return 1
        path = hits[0]
    sid = os.path.basename(path)[:-len(".jsonl")]
    rec = {"type": "custom-title", "customTitle": name, "sessionId": sid}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    print("retitled %s -> %s" % (sid, name))
    return 0


def main():
    if len(sys.argv) < 2:
        sys.stderr.write("usage: homi.py {daemon|call|retitle|selfname} ...\n")
        sys.exit(1)
    mode = sys.argv[1]
    if mode == "daemon":
        Homi().run()
    elif mode == "selfname":
        print(self_device())
    elif mode == "retitle":
        sys.exit(cli_retitle(sys.argv[2:]))
    elif mode == "call":
        sys.exit(cli_call(sys.argv[2:]))
    else:
        sys.stderr.write("unknown mode: %s\n" % mode)
        sys.exit(1)


if __name__ == "__main__":
    main()
