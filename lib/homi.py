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
import homi_seat  # the seat plane (tmux driver); imported lazily-usable, no tmux at import
import homi_workspace  # the workspace axis (git interrogation, worktrees)


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


def getpass_user():
    for k in ("HOMI_FLEET_USER", "USER", "LOGNAME"):
        v = os.environ.get(k)
        if v:
            return v
    try:
        import getpass
        return getpass.getuser()
    except Exception:
        return "user"


def _tailscale_ip():
    try:
        out = subprocess.run(["tailscale", "ip", "-4"],
                             capture_output=True, text=True, timeout=5)
        ip = (out.stdout or "").strip().split("\n")[0].strip()
        if ip.startswith("100."):
            return ip
    except Exception:
        pass
    return None


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
    # Unique temp name: concurrent writers must not clobber each other's
    # in-flight temp file (interleaved persists could drop the newest state).
    tmp = "%s.tmp-%s" % (path, uuid.uuid4().hex[:8])
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(data)
    os.replace(tmp, path)


class NegativeAck(Exception):
    """The far homi answered with a well-formed refusal ({"ok": false}).
    Retrying is pointless — the envelope must be dead-lettered, not requeued,
    or one poison message wedges every later message to that device."""


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
        self.mail_cv = threading.Condition()   # signals wait_for_message pollers
        # Cross-fleet: per-fleet grant sets (which local names a foreign fleet
        # may address) and a control-plane token (gates homi.sock once a fleet
        # link exists). Both loaded/created in run().
        self.grants = {}
        self.control_token = ""
        self.claim_mu = threading.RLock()   # serializes claim/proxy/release/rebind
        self.deliver_mu = {}                # name -> Lock (one drain per name)
        self.stop_ev = threading.Event()
        self.identities = {}   # name -> {"sock", "claimed_at", "kind", ["home"]}
        self.seen = {}         # name -> set of received msg_ids (dedup)
        self.links = {}        # device -> {"addr", "sock", "created_at"}
        self.link_in = {}      # device -> inbound server socket (arrival line)
        self.seen_dev = {}     # device -> set of received envelope msg_ids
        self.link_state = {}   # device -> {"backoff_s","backoff_until","last_ok","last_err"}
        self.ssh_procs = {}    # device -> Popen of the ssh -N -L child
        self.out_ev = threading.Event()
        # Pending reply-correlated asks. corr -> {"ev","reply","from","asker","target"}.
        self.pending = {}
        self.pending_by_asker = {}   # asker -> [corr, ...] FIFO for natural-reply fallback
        self.pending_mu = threading.Lock()
        self._seat = None            # lazily-built SeatDriver (needs tmux at runtime)
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
    def _pid_is_homi(self, pid):
        """Is this pid actually a homi daemon of ours? Pids get reused —
        especially across reboots — so a live pid alone proves nothing."""
        try:
            out = subprocess.run(["ps", "-o", "command=", "-p", str(pid)],
                                 capture_output=True, text=True, timeout=5)
            return "homi" in (out.stdout or "")
        except Exception:
            return False

    def acquire_singleton(self):
        lockdir = self.path("daemon.lock")
        pidfile = self.path("daemon.pid")
        for attempt in (1, 2, 3):
            try:
                os.mkdir(lockdir)
                break
            except FileExistsError:
                # The truth test is "does the control socket answer" — never
                # pid arithmetic alone (kill -9 + reboot leaves a stale lock,
                # and the old pid may now belong to anything, even another
                # user's process, where kill(0) raises PermissionError).
                if probe(self.path("homi.sock")) == "live":
                    sys.stderr.write("homi already running (control socket answers)\n")
                    sys.exit(3)
                oldpid = None
                try:
                    with open(pidfile) as f:
                        oldpid = int(f.read().strip())
                except (OSError, ValueError):
                    pass
                if oldpid is None:
                    # Lock present but no pidfile yet = ANOTHER daemon is between
                    # its mkdir and its pidfile write (the concurrent-autostart
                    # window two MCP clients can hit). Do NOT reclaim the lock —
                    # that would delete the winner's lock and split-brain. Back
                    # off and re-probe; the winner binds homi.sock within a beat.
                    if attempt < 3:
                        time.sleep(0.4 * attempt)
                        continue
                    sys.stderr.write("another homi is starting; giving way\n")
                    sys.exit(3)
                if oldpid:
                    try:
                        os.kill(oldpid, 0)
                        alive = True
                    except (ProcessLookupError, PermissionError, OSError):
                        alive = False
                    if alive and self._pid_is_homi(oldpid):
                        sys.stderr.write("homi lock held by live homi pid %d "
                                         "with a dead control socket — refusing "
                                         "to double-bind\n" % oldpid)
                        sys.exit(3)
                # stale lock (dead pid, reused pid, or foreign pid): reclaim
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

    _NAME_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")
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
            if d is not None and d.get("name") != name:
                n += 1
                continue
            if d is None:
                # Linux pid_max can be 4194304, overlapping this window; never
                # squat a slot whose number IS a live pid — a real session
                # could later write its own sidecar at that filename.
                try:
                    os.kill(n, 0)
                    alive = True
                except ProcessLookupError:
                    alive = False
                except OSError:
                    alive = True
                if alive:
                    n += 1
                    continue
            return p

    def _persist_identities(self):
        with self.mu:
            data = {n: {"claimed_at": e["claimed_at"],
                        "kind": e.get("kind", "local"),
                        "home": e.get("home"),
                        "seat": e.get("seat"),
                        "boxed": e.get("boxed", False),
                        "aliases": e.get("aliases") or [],
                        "workspace": e.get("workspace"),
                        "place": e.get("place") or {
                            "kind": "boxed" if e.get("boxed") else "local",
                            "device": e.get("home")},
                        "surface": e.get("surface"),
                        "card": e.get("card")}
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
        # Only a LEADING wrapper is attribution (that is where cc_peer._wrap
        # and Claude's own PCr() put it); a wrapper quoted mid-text is content
        # and must be neither trusted nor stripped.
        if content.startswith("<cross-session-message"):
            m = _FROM_NAME_RE.match(content)
            from_name = m.group(1) if m else None
            text = cc_peer._WRAP_INNER(content)
        else:
            from_name, text = None, content
        entry = {"ts": time.time(),
                 "msg_id": msg.get("msg_id") or uuid.uuid4().hex,
                 "from": cc_peer._addr_from(msg.get("from")) or "",
                 "from_name": from_name,
                 "text": text}
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
            # A plain inbound to an asking identity may be a natural reply.
            self._resolve_ask_natural(name, entry["text"], entry.get("from_name"))
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
        The append happens BEFORE any delivery attempt — durability first.
        fsync'd (an acked message must survive power loss), and a torn final
        line from a past crash is newline-terminated so entries never merge."""
        with self.mail_mu:
            ids = self.seen.setdefault(name, set())
            if entry["msg_id"] in ids:
                return False
            os.makedirs(self.mail_dir(name), exist_ok=True)
            p = self.inbox_path(name)
            prefix = b""
            try:
                if os.path.getsize(p) > 0:
                    with open(p, "rb") as rf:
                        rf.seek(-1, os.SEEK_END)
                        if rf.read(1) != b"\n":
                            prefix = b"\n"
            except OSError:
                pass
            with open(p, "ab") as f:
                f.write(prefix + (json.dumps(entry) + "\n").encode("utf-8"))
                f.flush()
                os.fsync(f.fileno())
            ids.add(entry["msg_id"])
        # Wake any wait_for_message long-pollers (MCP's inbound-wake for runtimes
        # with no socket push). _store is the mail-change event.
        with self.mail_cv:
            self.mail_cv.notify_all()
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
        """Collision rule (names DO collide in the wild): prefer a probe-live
        socket, then interactive, then newest startedAt. Deterministic, so the
        choice doesn't flap; the probe only runs when there IS a collision."""
        cands = sorted(cands, key=lambda d: (d.get("kind") == "interactive",
                                             d.get("startedAt") or 0), reverse=True)
        if len(cands) > 1:
            live = [d for d in cands
                    if probe(d.get("messagingSocketPath") or "") == "live"]
            if live:
                return live[0]
        return cands[0] if cands else None

    def _reply_addr(self, from_name):
        """Reply address for a delivered turn. NEVER the recipient's own
        socket: a protocol-faithful peer replying to `from` would loop the
        reply straight back into the same mailbox and session (with a codex
        peer behind the name — forever). The sender's identity/proxy socket
        routes replies correctly; otherwise a dead-drop path nothing listens
        on, so a reply fails visibly instead of looping silently."""
        if from_name:
            with self.mu:
                ent = self.identities.get(from_name)
            if ent:
                return ent["sock"]
        return self.path("drop.sock")

    def _deliver_pending(self, name, sess=None):
        """Drain undelivered inbox lines into the live session, in order, as
        protocol turns (an inbound message wakes an idle session). The cursor
        advances only after a successful socket write — at-least-once. One
        drain per name at a time (tick, socket arrival, link receive, and
        control send may all race here), and the cursor only moves forward."""
        with self.mail_mu:
            lk = self.deliver_mu.setdefault(name, threading.Lock())
        if not lk.acquire(blocking=False):
            return  # a drain for this name is already in flight
        try:
            with self.mail_mu:
                lines = self._inbox_lines(name)
                cur = self._read_cursor(name)
            if cur >= len(lines):
                return
            with self.mu:
                ent = self.identities.get(name) or {}
            if ent.get("boxed"):
                # A boxed agent's published socket IS its session socket.
                if probe(ent["sock"]) == "live":
                    sess = {"messagingSocketPath": ent["sock"]}
                else:
                    return  # container down: hold mail durably
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
                frm = obj.get("from") or self._reply_addr(obj.get("from_name") or "")
                try:
                    cc_peer.deliver(to_sock, obj.get("text") or "", frm,
                                    from_name=obj.get("from_name") or None)
                except OSError as e:
                    # Listener gone mid-drain: hold the rest, stay addressable.
                    self.log("deliver to", name, "failed (hold):", e)
                    self._plant(name)
                    return
                with self.mail_mu:
                    if i + 1 > self._read_cursor(name):
                        self._write_cursor(name, i + 1)
            self.log("drained", len(lines) - cur, "message(s) to live", name)
        finally:
            lk.release()

    # -- ask / reply (reply-correlated request-response) -------------------------
    #
    # ask blocks until a correlated reply arrives or a timeout elapses. The
    # message carries a return token `<asker>@<device>~<corr>`; the recipient
    # replies with `communicate homi reply <token> "<answer>"`, which routes the
    # reply home (local resolve, or a kind-"r" envelope over a link) and unblocks
    # the caller. Without a token, a plain message back to the asker resolves the
    # oldest pending ask for that asker (best-effort natural fallback).

    def _ask_token(self, asker, corr):
        return "%s@%s~%s" % (asker, self.device, corr)

    def _register_ask(self, corr, asker, target):
        slot = {"ev": threading.Event(), "reply": None, "from": None,
                "asker": asker, "target": target}
        with self.pending_mu:
            self.pending[corr] = slot
            self.pending_by_asker.setdefault(asker, []).append(corr)
        return slot

    def _drop_ask(self, corr):
        with self.pending_mu:
            slot = self.pending.pop(corr, None)
            if slot:
                lst = self.pending_by_asker.get(slot["asker"])
                if lst and corr in lst:
                    lst.remove(corr)
        return slot

    def _fill_ask(self, corr, text, frm):
        with self.pending_mu:
            slot = self.pending.get(corr)
        if not slot or slot["ev"].is_set():
            return False
        slot["reply"], slot["from"] = text, frm
        slot["ev"].set()
        return True

    def _resolve_ask_natural(self, asker, text, frm):
        """A plain message arrived at `asker`; if an ask is pending for it,
        treat this as the reply (oldest first)."""
        with self.pending_mu:
            lst = self.pending_by_asker.get(asker) or []
            corr = lst[0] if lst else None
            slot = self.pending.get(corr) if corr else None
        if slot and not slot["ev"].is_set():
            slot["reply"], slot["from"] = text, frm
            slot["ev"].set()
            return True
        return False

    def _do_ask(self, to, text, from_name, timeout):
        if not text:
            return {"ok": False, "err": "empty message"}
        asker = from_name or "asker"
        if not self._NAME_RE.match(asker) or asker in self._RESERVED:
            return {"ok": False, "err": "invalid --from identity %r" % asker}
        # The asker must be a real local identity so replies have a home.
        with self.mu:
            have = (asker in self.identities
                    and self.identities[asker].get("kind") == "local")
        if not have:
            r = self._do_claim(asker)
            if not r.get("ok"):
                return {"ok": False, "err": "cannot claim asker %s: %s"
                        % (asker, r.get("err"))}
        corr = uuid.uuid4().hex
        token = self._ask_token(asker, corr)
        wrapped = ("%s\n\n[reply with: communicate homi reply %s \"<answer>\" "
                   "--from %s]" % (text, token, to.split("@", 1)[0]))
        slot = self._register_ask(corr, asker, to.split("@", 1)[0])
        r = self._do_send(to, wrapped, asker)
        if not r.get("ok"):
            self._drop_ask(corr)
            return {"ok": False, "err": "send failed: %s" % r.get("err"), "corr": corr}
        t0 = time.time()
        got = slot["ev"].wait(timeout)
        self._drop_ask(corr)
        if not got:
            return {"ok": False, "err": "timeout", "corr": corr,
                    "waited": round(time.time() - t0, 2)}
        return {"ok": True, "reply": slot["reply"], "from": slot["from"],
                "corr": corr, "latency": round(time.time() - t0, 2)}

    def _do_reply(self, token, text, from_name):
        # token: <asker>@<device>~<corr> (full) or a bare <corr> for a local ask.
        asker = dev = corr = None
        if "~" in token:
            left, corr = token.rsplit("~", 1)
            asker, dev = left.split("@", 1) if "@" in left else (left, None)
        else:
            corr = token
        if dev and dev != self.device:
            with self.mu:
                have_link = dev in self.links
            if not have_link:
                return {"ok": False, "err": "reply device not linked: %s" % dev}
            self._queue_out(dev, {"v": 1, "kind": "r", "to": asker, "corr": corr,
                                  "from": from_name or "unknown", "text": text,
                                  "msg_id": uuid.uuid4().hex, "ts": time.time()})
            return {"ok": True, "routed": "link:%s" % dev}
        filled = self._fill_ask(corr, text, from_name or "unknown")
        if asker:
            self._store(asker, {"ts": time.time(), "msg_id": uuid.uuid4().hex,
                                "from": "", "from_name": from_name, "text": text,
                                "corr": corr})
        return {"ok": True, "resolved": filled}

    def _do_group(self, names, text, from_name):
        results = {}
        for n in [x for x in names if x]:
            results[n] = self._do_send(n, text, from_name)
        ok = all(r.get("ok") for r in results.values()) if results else False
        return {"ok": ok,
                "results": {n: (r.get("routed") or r.get("err"))
                            for n, r in results.items()}}

    # -- seat plane (interactive surfaces) --------------------------------------

    def _seat_drv(self):
        if self._seat is None:
            self._seat = homi_seat.SeatDriver(log=self.log)
        return self._seat

    def _seat_target_device(self, sub, req):
        """A seat may be addressed <device>:<seat> (or spawned with args.device).
        Returns (device_or_None, rewritten_req) with the device stripped."""
        dev = None
        seat = req.get("seat") or ""
        if ":" in seat:
            d, s = seat.split(":", 1)
            if self._DEV_RE.match(d) and d != self.device:
                dev, req = d, dict(req, seat=s)
        if sub == "spawn" and req.get("device") and req["device"] != self.device:
            dev = req["device"]
            req = {k: v for k, v in req.items() if k != "device"}
        return dev, req

    def _do_seat(self, sub, req):
        # Remote seat ops ride the link synchronously: the ack carries the
        # result. Requires --allow-seats granted on the far side's link to us.
        dev, req = self._seat_target_device(sub, req)
        if dev:
            with self.mu:
                have = dev in self.links
            if not have:
                return {"ok": False, "err": "device not linked: %s" % dev}
            try:
                endpoint = self._link_endpoint(dev)
            except (OSError, ValueError) as e:
                return {"ok": False, "err": "link to %s down: %s" % (dev, e)}
            if not endpoint:
                return {"ok": False, "err": "no transport to %s" % dev}
            to = float(req.get("timeout") or 30) + 15 if sub == "wait" else 20
            env = {"v": 1, "kind": "seat", "sub": sub, "args": req,
                   "msg_id": uuid.uuid4().hex, "ts": time.time()}
            try:
                ack = self._send_envelope_result(endpoint, env, timeout=to)
            except (OSError, ValueError) as e:
                return {"ok": False, "err": "seat op to %s failed: %s" % (dev, e)}
            if not ack.get("ok"):
                return {"ok": False, "err": ack.get("err") or "remote refused"}
            res = ack.get("result") or {}
            # Re-qualify a spawned seat id with its device for the caller.
            if sub == "spawn" and res.get("seat"):
                res = dict(res, seat="%s:%s" % (dev, res["seat"]))
            return res
        drv = self._seat_drv()
        try:
            if sub == "ls":
                return drv.ls()
            if sub == "spawn":
                cmd = req.get("cmd")
                if not cmd:
                    return {"ok": False, "err": "seat spawn needs a command"}
                return drv.spawn(cmd, cwd=req.get("cwd"),
                                 window_name=req.get("name"))
            if sub == "send":
                return drv.send(req.get("seat", ""), req.get("text", ""))
            if sub == "read":
                return drv.read(req.get("seat", ""),
                                lines=int(req.get("lines") or 40),
                                raw=bool(req.get("raw")))
            if sub == "state":
                return {"ok": True, "seat": req.get("seat"),
                        "state": drv.state(req.get("seat", ""))}
            if sub == "wait":
                return drv.wait(req.get("seat", ""),
                                timeout=float(req.get("timeout") or 120))
            if sub == "respond":
                return drv.respond(req.get("seat", ""),
                                   decision=req.get("decision") or "allow")
            if sub == "interrupt":
                return drv.interrupt(req.get("seat", ""))
            if sub == "kill":
                return drv.kill(req.get("seat", ""))
            if sub == "bind":
                return self._do_seat_bind(req.get("seat", ""), req.get("name", ""))
            return {"ok": False, "err": "unknown seat op: %s" % sub}
        except homi_seat.SeatError as e:
            return {"ok": False, "err": str(e)}

    def _do_seat_bind(self, seat, name):
        if not seat or not name:
            return {"ok": False, "err": "seat bind needs <seat> <name>"}
        with self.mu:
            ent = self.identities.get(name)
            if not ent or ent.get("kind") != "local":
                return {"ok": False, "err": "claim %s first (local identity)" % name}
            ent["seat"] = seat
        self._persist_identities()
        self.log("bound seat", seat, "->", name)
        return {"ok": True, "seat": seat, "name": name}

    # -- spawn / fan / consult (the fabric creates agents) ----------------------
    #
    # spawn = claim a durable identity + launch an agent in a seat + bind + (for
    # claude) adopt via rename-sync, so the agent is reachable by mail at its
    # name AND watchable in a seat. The fabric becomes a creator of agents, not
    # just a router — using its OWN seat plane, with no dependency on old anu.

    def _do_spawn(self, name, cmd, cwd=None, adopt=False, boot_wait=25):
        if not self._NAME_RE.match(name or "") or name in self._RESERVED:
            return {"ok": False, "err": "invalid name %r" % name}
        if not cmd:
            return {"ok": False, "err": "spawn needs a command (--cli or a command)"}
        r = self._do_claim(name)
        if not r.get("ok"):
            return {"ok": False, "err": "claim %s: %s" % (name, r.get("err"))}
        sp = self._do_seat("spawn", {"cmd": cmd, "cwd": cwd, "name": name})
        if not sp.get("ok"):
            return {"ok": False, "err": "seat spawn: %s" % sp.get("err")}
        seat = sp["seat"]
        self._do_seat_bind(seat, name)
        adopted = False
        if adopt:
            drv = self._seat_drv()
            t0 = time.time()
            # let the agent boot to a ready prompt
            while time.time() - t0 < boot_wait:
                if drv.state(seat) in ("idle", "approval"):
                    break
                time.sleep(0.5)
            try:
                drv.send(seat, "/rename %s" % name)
            except homi_seat.SeatError as e:
                self.log("adopt send failed:", e)
            for _ in range(16):
                if name in self._scan_sidecars():
                    adopted = True
                    break
                time.sleep(0.5)
        self.log("spawned", name, "in seat", seat, "adopted" if adopted else "")
        return {"ok": True, "name": name, "seat": seat, "adopted": adopted}

    def _do_fan(self, n, cmd, prefix, cwd=None, adopt=False):
        if n < 1 or n > 32:
            return {"ok": False, "err": "fan count must be 1..32"}
        out = []
        for i in range(1, n + 1):
            nm = "%s-%d" % (prefix, i)
            out.append(self._do_spawn(nm, cmd, cwd=cwd, adopt=adopt))
        names = [r["name"] for r in out if r.get("ok")]
        return {"ok": bool(names), "spawned": out, "group": names}

    def _do_consult(self, name, cmd, text, timeout, adopt=False):
        # spawn-or-reuse a private consultant, then ask it one question.
        with self.mu:
            exists = (name in self.identities
                      and self.identities[name].get("kind") == "local")
        reused = exists
        if not exists:
            r = self._do_spawn(name, cmd, adopt=adopt)
            if not r.get("ok"):
                return r
        if not text:
            return {"ok": True, "name": name, "reused": reused, "spawned": not reused}
        ans = self._do_ask(name, text, "consultant", float(timeout))
        ans["name"] = name
        ans["reused"] = reused
        return ans

    # -- inbox / wait / roster (read surface for the MCP faces) -----------------

    def _inbox_entries(self, name, tail=50, after_msg_id=None):
        with self.mail_mu:
            lines = self._inbox_lines(name)
        out = []
        started = after_msg_id is None
        for ln in lines:
            try:
                e = json.loads(ln)
            except Exception:
                continue
            if not started:
                if e.get("msg_id") == after_msg_id:
                    started = True
                continue
            out.append(e)
        return out[-tail:] if (after_msg_id is None) else out

    def _do_inbox(self, name, tail=50, after_msg_id=None):
        if not self._NAME_RE.match(name or ""):
            return {"ok": False, "err": "invalid name"}
        return {"ok": True, "name": name,
                "messages": self._inbox_entries(name, tail, after_msg_id)}

    def _do_wait(self, name, timeout, after_msg_id=None):
        """Block until a message beyond after_msg_id (or beyond the current tail)
        lands for `name`. This is the inbound wake for MCP runtimes with no
        socket push; Claude sessions get native socket delivery instead."""
        if not self._NAME_RE.match(name or ""):
            return {"ok": False, "err": "invalid name"}
        # Anchor at the current tail when no cursor was given.
        if after_msg_id is None:
            cur = self._inbox_entries(name, tail=1)
            after_msg_id = cur[-1]["msg_id"] if cur else None
        deadline = time.time() + timeout
        while True:
            new = self._inbox_entries(name, tail=1000, after_msg_id=after_msg_id)
            if new:
                return {"ok": True, "name": name, "messages": new}
            remaining = deadline - time.time()
            if remaining <= 0:
                return {"ok": False, "err": "timeout", "name": name}
            with self.mail_cv:
                self.mail_cv.wait(min(remaining, 5.0))

    def _do_agents(self):
        st = self.build_status()
        agents = []
        for n, e in sorted((st.get("identities") or {}).items()):
            route = e.get("route") or {}
            agents.append({
                "name": n, "kind": e.get("kind", "local"),
                "home": e.get("home"),
                "state": route.get("state"), "provenance": route.get("provenance"),
                "undelivered": (e.get("inbox") or {}).get("undelivered", 0),
                "seat": e.get("seat"),
            })
        return {"ok": True, "device": st["self"]["device"], "agents": agents}

    # -- move: relocate an agent-being to another device -------------------------
    #
    # The agent = three JSON artifacts: the transcript (its mind, moved by the
    # CLI via rsync), the mailbox (inbox.jsonl + cursor), and the identity claim.
    # The move order keeps the ADDRESS alive across the transfer: depart flips
    # the name to a proxy pointing at the target in the same breath it releases
    # the claim, so mail never lands in a dead inbox. (Old beam moved only the
    # transcript and let the address die at the origin.)

    def _do_premove(self, name):
        with self.mu:
            ent = self.identities.get(name)
        if not ent or ent.get("kind") != "local":
            return {"ok": False, "err": "%s is not a local identity here" % name}
        cands = self._scan_sidecars().get(name) or []
        sess = self._choose_session(cands)
        live = bool(sess and probe(sess["messagingSocketPath"]) == "live")
        with self.mail_mu:
            lines = len(self._inbox_lines(name))
            cur = self._read_cursor(name)
        return {"ok": True, "name": name, "live": live,
                "session": ({"pid": sess.get("pid"), "kind": sess.get("kind")}
                            if sess else None),
                "mailbox": self.inbox_path(name),
                "cursor_path": self.cursor_path(name),
                "lines": lines, "cursor": cur, "seat": ent.get("seat")}

    def _do_depart(self, name, device):
        """Atomically release the local claim and rebind the name as a proxy
        homed at `device` — the address survives the move. Requires the link."""
        with self.mu:
            linked = device in self.links
        if not linked:
            return {"ok": False, "err": "device not linked: %s" % device}
        with self.claim_mu:
            with self.mu:
                ent = self.identities.get(name)
            if not ent or ent.get("kind") != "local":
                return {"ok": False, "err": "%s is not a local identity" % name}
            r = self._do_release(name)
            if not r.get("ok"):
                return {"ok": False, "err": "release: %s" % r.get("err")}
            p = self._ensure_proxy(name, device)
            if p is None:
                return {"ok": False, "err": "proxy rebind failed for %s" % name}
        self.log("departed:", name, "-> proxy home", device)
        return {"ok": True, "name": name, "proxied_to": device}

    def _do_arrive(self, name, staged, cursor):
        """Claim `name` here (idempotent vs the auto-claim race) and merge a
        staged origin inbox. The cursor is a delivered-PREFIX count, and mail
        may already be waiting here (a straggler auto-claimed mid-move), so the
        merge RECOMPOSES the file: target-delivered, then origin-delivered
        (cursor covers both — a resumed session never re-receives acted-on
        mail), then every undelivered line after the cursor so store->wake
        delivers it. Dedup by msg_id; atomic rewrite."""
        with self.mu:
            have = (name in self.identities
                    and self.identities[name].get("kind") == "local")
        if not have:
            r = self._do_claim(name)
            if not r.get("ok"):
                return {"ok": False, "err": "claim: %s" % r.get("err")}
        entries = []
        try:
            with open(staged, encoding="utf-8") as f:
                for ln in f:
                    ln = ln.strip()
                    if not ln:
                        continue
                    try:
                        entries.append(json.loads(ln))
                    except Exception:
                        continue
        except OSError as e:
            return {"ok": False, "err": "staged inbox unreadable: %s" % e}
        cursor = max(0, min(int(cursor or 0), len(entries)))
        # Hold the per-name deliver lock across the recompose: _deliver_pending
        # releases mail_mu mid-delivery (holding only this lock), then advances
        # the cursor by index — a concurrent recompose that reorders the file
        # under it would scramble those indices (redelivery). lk -> mail_mu is
        # the same order _deliver_pending uses, so no deadlock.
        with self.mail_mu:
            lk = self.deliver_mu.setdefault(name, threading.Lock())
        lk.acquire()
        try:
            with self.mail_mu:
                seen = self.seen.setdefault(name, set())
                # Seed from the full file (the in-memory ring only holds a tail).
                existing = []
                existing_ids = set()
                for ln in self._inbox_lines(name):
                    ln = ln.strip()
                    if not ln:
                        continue
                    try:
                        e = json.loads(ln)
                    except Exception:
                        continue
                    existing.append(e)
                    if e.get("msg_id"):
                        existing_ids.add(e["msg_id"])
                fresh_del = [e for e in entries[:cursor]
                             if e.get("msg_id") and e["msg_id"] not in existing_ids]
                fresh_und = [e for e in entries[cursor:]
                             if e.get("msg_id") and e["msg_id"] not in existing_ids]
                tcur = self._read_cursor(name)
                tcur = max(0, min(tcur, len(existing)))
                merged = (existing[:tcur] + fresh_del
                          + existing[tcur:] + fresh_und)
                os.makedirs(self.mail_dir(name), exist_ok=True)
                _atomic_write(self.inbox_path(name),
                              "".join(json.dumps(e) + "\n" for e in merged))
                self._write_cursor(name, tcur + len(fresh_del))
                for e in fresh_del + fresh_und:
                    seen.add(e["msg_id"])
        finally:
            lk.release()
        if fresh_und or len(existing) > tcur:
            threading.Thread(target=self._safe_deliver, args=(name,),
                             daemon=True).start()
        self.log("arrived:", name, "merged", len(fresh_del), "delivered +",
                 len(fresh_und), "undelivered")
        return {"ok": True, "name": name, "merged_delivered": len(fresh_del),
                "merged_undelivered": len(fresh_und)}

    def _do_notify(self, reason, from_name):
        if not reason:
            return {"ok": False, "err": "empty reason"}
        rec = {"ts": time.time(), "from": from_name or "", "reason": reason,
               "device": self.device}
        d = self.path("notify")
        os.makedirs(d, exist_ok=True)
        fn = "%016d-%s.json" % (int(rec["ts"] * 1000), uuid.uuid4().hex[:6])
        _atomic_write(os.path.join(d, fn), json.dumps(rec))
        cmd = os.environ.get("HOMI_NOTIFY_CMD")
        if cmd:
            try:
                subprocess.run(cmd, shell=True, input=(reason + "\n").encode(),
                               timeout=10)
            except Exception as e:
                self.log("notify hook failed:", e)
        self.log("notify:", reason)
        return {"ok": True}

    def _do_send(self, to, text, from_name, msg_id=None):
        if not text:
            return {"ok": False, "err": "empty message"}
        # A caller-supplied msg_id makes the send idempotent end-to-end (the
        # boxed outbox re-offers frames after a lost ack): store/envelope dedup
        # by this id instead of minting a fresh one that dedup can never catch.
        mid = msg_id or uuid.uuid4().hex
        name, dev = (to.split("@", 1) if "@" in to else (to, None))
        # Validate LOCALLY: a bad name queued toward a device would come back
        # as a deterministic negative ack from the far side.
        if not self._NAME_RE.match(name or ""):
            return {"ok": False,
                    "err": "invalid name %r (want [a-z0-9][a-z0-9._-]{0,63})" % name}
        if dev is not None and not self._DEV_RE.match(dev):
            return {"ok": False, "err": "invalid device %r" % dev}
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
                link_ent = self.links.get(dev)
            if link_ent is None:
                return {"ok": False, "err": "device not linked: %s" % dev}
            if (link_ent.get("kind") == "fleet"
                    and self._NAME_RE.match(from_name or "")
                    and not self._is_granted(dev, from_name)):
                # Sending to a fleet opens YOUR return path: grant the sender
                # identity to THAT fleet only, so their reply can land. Mail has
                # a return address; deny-by-default stays for everything else.
                self._do_grant(dev, from_name)
                self.log("auto-granted return path:", from_name, "to fleet", dev)
            env = {"v": 1, "kind": "m", "to": name, "from": from_name,
                   "msg_id": mid, "text": text, "ts": time.time()}
            self._queue_out(dev, env)
            return {"ok": True, "routed": "link:%s" % dev}
        if kind == "local":
            entry = {"ts": time.time(), "msg_id": mid, "from": "",
                     "from_name": from_name, "text": text}
            dup = not self._store(name, entry)   # False => msg_id already seen
            if dup:
                return {"ok": True, "routed": "dup"}
            self._resolve_ask_natural(name, text, from_name)
            self._resolve_ask_natural(name, text, from_name)
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

    def _blank_axes(self, boxed=False):
        """The four axes, empty. Every identity carries them from birth so a
        later writer never has to remember to create them (the registry law:
        a field that needs a separate remembered write dies)."""
        return {
            "workspace": None,                       # {path, ref, branch, worktree}
            "place": {"kind": "boxed" if boxed else "local", "device": None},
            "surface": None,                         # {driver, handle, state, measured_at}
            "card": None,                            # {what, ask_me_for, derived, updated}
            "aliases": [],                           # durable role names for this identity
        }

    def _do_claim(self, name, boxed=False, cwd=None, worktree=False):
        if not self._NAME_RE.match(name or ""):
            return {"ok": False, "err": "invalid name (want [a-z0-9][a-z0-9._-]{0,63})"}
        if name in self._RESERVED:
            return {"ok": False, "err": "'%s' is reserved" % name}
        # Compute the workspace record BEFORE the claim lock: describe() and
        # especially make_worktree() shell out to git, and make_worktree's own
        # timeout is 60s — holding claim_mu (instance-wide) across that would
        # stall every OTHER claim on this daemon, including the auto-claims on
        # inbound mail (:1929) and _do_ask (:701). Best-effort in the fullest
        # sense: ANY failure here (bad path, permission error, malformed cwd)
        # must never fail the claim itself, only leave the axis unset.
        #
        # A cheap pre-check skips that work entirely when `name` is already a
        # LOCAL identity: without it, an ordinary client retry of a claim (or
        # any repeat --worktree claim against a different --cwd) would run
        # `git worktree add` before ever reaching the no-op below, littering
        # the target repo with an orphaned branch + checkout nothing
        # references. A PROXY does not count as "already claimed" here —
        # _do_claim releases and re-claims it below, a genuine new local
        # claim that must still get its workspace recorded. This check is
        # intentionally racy (outside claim_mu): a benign concurrent double
        # claim can still do the work twice, which is acceptable; the
        # authoritative decision stays the check inside claim_mu below,
        # unchanged.
        with self.mu:
            already = (name in self.identities
                       and self.identities[name].get("kind") == "local")
        ws = None
        if cwd and not already:
            try:
                ws = (homi_workspace.make_worktree(cwd, name) if worktree
                      else homi_workspace.describe(cwd))
            except Exception as e:
                self.log("workspace not recorded for", name, ":", e)
        with self.claim_mu:  # check+bind+insert must be one atomic step
            with self.mu:
                ent = self.identities.get(name)
            if ent is not None and ent.get("kind") == "proxy":
                # A local claim outranks a remote proxy for the same name.
                self._do_release(name)
            elif ent is not None:
                return {"ok": True, "already": True}
            sock = self.identity_sock(name)
            if boxed:
                # A BOXED identity: the socket is published INTO the sockdir by
                # the container (vsock forwarder, guest-listener/host-connector
                # — the only direction that works; measured). Homi never binds
                # it; it records the path as authoritative, probes it for
                # liveness, and drains the box's outbox socket. To every local
                # session the boxed agent looks like any other peer.
                ent = {"sock": sock, "claimed_at": time.time(), "_srv": None,
                       "kind": "local", "boxed": True}
                ent.update(self._blank_axes(boxed=True))
                with self.mu:
                    self.identities[name] = ent
                os.makedirs(self.path("mail", name), exist_ok=True)
                os.makedirs(self.path("boxes", name), exist_ok=True)
                self._seed_seen(name)
                self._plant(name)
                threading.Thread(target=self._box_drain_loop, args=(name,),
                                 daemon=True).start()
                if ws is not None:
                    with self.mu:
                        self.identities[name]["workspace"] = ws
                self._persist_identities()
                self.log("claimed BOXED identity:", name, "-> published", sock)
                return {"ok": True, "boxed": True,
                        "publish_in": sock,
                        "publish_out": self.path("boxes", name, "outbox.sock")}
            srv = self.bind_unix(sock)
            ent = {"sock": sock, "claimed_at": time.time(), "_srv": srv,
                   "kind": "local"}
            ent.update(self._blank_axes())
            with self.mu:
                self.identities[name] = ent
            os.makedirs(self.path("mail", name), exist_ok=True)
            self._seed_seen(name)
            self._plant(name)
            threading.Thread(target=self._identity_server, args=(name, srv),
                             daemon=True).start()
            if ws is not None:
                with self.mu:
                    self.identities[name]["workspace"] = ws
            self._persist_identities()
        self.log("claimed identity:", name, "->", sock)
        return {"ok": True}

    def _box_drain_loop(self, name):
        """Hold a persistent connection to a boxed agent's OUTBOX socket (the
        second published socket) and route every frame it emits. At-least-once:
        each frame carries msg_id; we ack on the same connection after routing,
        and the in-box shim clears its spool on ack. The from-rewrite happens
        here — a guest path means nothing on the host; attribution is the boxed
        identity itself."""
        out_sock = self.path("boxes", name, "outbox.sock")
        backoff = 1.0

        def still_boxed():
            with self.mu:
                ent = self.identities.get(name)
            return bool(ent and ent.get("boxed"))

        while not self.stop_ev.is_set():
            if not still_boxed():
                return  # released or re-claimed unboxed: stop draining
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(2.0)
            try:
                s.connect(out_sock)
                backoff = 1.0
                buf = b""
                while not self.stop_ev.is_set():
                    try:
                        chunk = s.recv(65536)
                    except socket.timeout:
                        # Re-check ownership on every idle tick: a release or
                        # re-claim-unboxed must stop this drain within ~2s, not
                        # keep routing a detached container's frames as `name`.
                        if not still_boxed():
                            return
                        continue
                    if not chunk:
                        break  # box side closed; reconnect
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        if not line.strip():
                            continue
                        try:
                            fr = json.loads(line.decode("utf-8", "replace"))
                        except Exception:
                            continue
                        if not still_boxed():
                            return
                        to = fr.get("to") or ""
                        text = fr.get("text") or ""
                        mid = fr.get("msg_id") or ""
                        if to and text and mid:
                            # Route with the frame's OWN msg_id so a re-offer
                            # after a lost ack is deduped end-to-end (a fresh id
                            # would slip past every seen-set).
                            r = self._do_send(to, text, name, msg_id=mid)
                            ok = bool(r.get("ok"))
                            fatal = (not ok) and bool(r.get("err"))
                        else:
                            ok, fatal = False, True  # malformed: never routable
                        # A permanently-unroutable frame must not spin forever
                        # (the box re-offers on every nack). Ack it as accepted
                        # after dead-lettering, mirroring the link poison path.
                        if fatal:
                            self._box_deadletter(name, fr)
                            ok = True
                        try:
                            s.sendall((json.dumps(
                                {"ack": mid, "ok": ok}) + "\n").encode())
                        except OSError:
                            break
            except OSError:
                pass
            finally:
                try:
                    s.close()
                except OSError:
                    pass
            self.stop_ev.wait(backoff)
            backoff = min(backoff * 2, 15.0)

    def _box_deadletter(self, name, frame):
        d = self.path("boxes", name, "dead")
        os.makedirs(d, exist_ok=True)
        try:
            _atomic_write(os.path.join(d, "%d-%s.json" % (
                int(time.time() * 1000), (frame.get("msg_id") or "x")[:16])),
                json.dumps(frame))
        except Exception as e:
            self.log("box dead-letter failed:", name, e)
        self.log("box frame dead-lettered:", name, frame.get("to"))

    def _do_release(self, name):
        with self.claim_mu:
            with self.mu:
                ent = self.identities.pop(name, None)
            if not ent:
                return {"ok": False, "err": "not claimed: %s" % name}
            try:
                if ent.get("_srv") is not None:
                    ent["_srv"].close()
            except OSError:
                pass
            try:
                # A boxed identity's socket belongs to the container's
                # forwarder — never unlink it out from under a running box.
                if not ent.get("boxed"):
                    os.unlink(ent["sock"])
            except OSError:
                pass
            self._unplant(name)
            self._persist_identities()
        self.log("released identity:", name)
        return {"ok": True}

    def _rebind(self, name):
        with self.claim_mu:
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
            r = self._do_claim(name, boxed=bool(e.get("boxed")))
            if not r.get("ok"):
                self.log("re-claim failed:", name, r.get("err"))
                continue
            with self.mu:
                if name in self.identities:
                    for k in ("seat", "aliases", "workspace", "place",
                              "surface", "card"):
                        if e.get(k) is not None:
                            self.identities[name][k] = e[k]
        self._persist_identities()

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
        # A fleet-qualified proxy (sender@fleet) is legal — and self-routing:
        # _do_send splits it back into (name, fleet-link). Bare names keep the
        # strict local rule.
        base, _, qual = (name or "").partition("@")
        if (not self._NAME_RE.match(base) or name in self._RESERVED
                or (qual and not self._DEV_RE.match(qual))):
            return None
        with self.claim_mu:  # atomic vs concurrent claims/proxies/releases
            with self.mu:
                ent = self.identities.get(name)
            if ent is not None:
                if ent.get("kind") != "proxy":
                    return None
                if ent.get("home") != device:
                    # Two devices sending as one name: first home wins;
                    # surface it instead of silently misrouting replies.
                    self.log("proxy home collision:", name, "stays",
                             ent.get("home"), "— also seen via", device)
                return ent
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

    _DEV_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")

    def link_in_sock(self, device):
        return self.path("in", device + ".sock")

    def _persist_links(self):
        with self.mu:
            data = {d: {"addr": e.get("addr"), "sock": e.get("sock"),
                        "remote_home": e.get("remote_home"),
                        "allow_seats": e.get("allow_seats", False),
                        "kind": e.get("kind", "device"),
                        "remote_in": e.get("remote_in"),
                        "identity_file": e.get("identity_file"),
                        "key_fp": e.get("key_fp"),
                        "created_at": e.get("created_at")}
                    for d, e in self.links.items()}
        _atomic_write(self.path("links.json"), json.dumps(data, indent=1))

    # -- cross-fleet: control token, grants, cards --------------------------------

    def _ensure_control_token(self):
        p = self.path("control.token")
        try:
            with open(p, "r") as f:
                tok = f.read().strip()
            if tok:
                return tok
        except OSError:
            pass
        tok = os.urandom(24).hex()
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, tok.encode())
        finally:
            os.close(fd)
        return tok

    def _needs_token(self):
        with self.mu:
            return any(e.get("kind") == "fleet" for e in self.links.values())

    def _load_grants(self):
        gdir = self.path("grants")
        try:
            files = os.listdir(gdir)
        except OSError:
            return
        for f in files:
            if f.endswith(".json"):
                fleet = f[:-5]
                data = _read_json(os.path.join(gdir, f), {})
                self.grants[fleet] = set(data.get("granted") or [])

    def _persist_grant(self, fleet):
        os.makedirs(self.path("grants"), exist_ok=True)
        _atomic_write(self.path("grants", fleet + ".json"),
                      json.dumps({"granted": sorted(self.grants.get(fleet, set()))},
                                 indent=1))

    def _do_grant(self, fleet, name):
        if not self._DEV_RE.match(fleet or ""):
            return {"ok": False, "err": "invalid fleet name"}
        if name and not self._NAME_RE.match(name):
            return {"ok": False, "err": "invalid identity name"}
        self.grants.setdefault(fleet, set())
        if name:
            self.grants[fleet].add(name)
        self._persist_grant(fleet)
        return {"ok": True, "fleet": fleet, "granted": sorted(self.grants[fleet])}

    def _do_revoke_grant(self, fleet, name):
        self.grants.setdefault(fleet, set()).discard(name)
        self._persist_grant(fleet)
        return {"ok": True, "fleet": fleet, "granted": sorted(self.grants[fleet])}

    def _is_granted(self, fleet, name):
        return name in self.grants.get(fleet, set())

    def _link_fleet(self, device):
        """If the arrival link `device` is a fleet (cross-operator) link, return
        its fleet petname (== the link name); else None. Attribution is by the
        arrival line: whatever lands on in/<name>.sock IS that fleet."""
        with self.mu:
            ent = self.links.get(device) or {}
        return device if ent.get("kind") == "fleet" else None

    def _fleet_key(self):
        """This device's fleet keypair — outbound dials only, generated once,
        never leaves the machine. Returns (private_path, pubkey_line, fp)."""
        priv = self.path("keys", "fleet_ed25519")
        os.makedirs(self.path("keys"), exist_ok=True)
        if not os.path.exists(priv):
            subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-q",
                            "-f", priv, "-C", "homi/%s@%s" %
                            (os.environ.get("HOMI_FLEET", getpass_user()), self.device)],
                           check=True, capture_output=True)
        pub = ""
        try:
            with open(priv + ".pub") as f:
                pub = f.read().strip()
        except OSError:
            pass
        fp = ""
        try:
            out = subprocess.run(["ssh-keygen", "-lf", priv + ".pub"],
                                 capture_output=True, text=True, timeout=5)
            fp = (out.stdout or "").split()[1] if out.returncode == 0 else ""
        except Exception:
            pass
        return priv, pub, fp

    def _do_card(self):
        """This device's homi-card: what a peer needs to link back to us. The
        inbound path is included because a forward-only key cannot probe $HOME."""
        addr = "%s@%s" % (getpass_user(), self.device)
        try:
            _, pub, fp = self._fleet_key()
        except Exception as e:
            return {"ok": False, "err": "fleet key: %s" % e}
        return {"ok": True, "card": {
            "v": 1, "kind": "homi-card",
            "fleet": os.environ.get("HOMI_FLEET", getpass_user()),
            "device": self.device,
            "addr": addr,
            "tailscale_ip": _tailscale_ip(),
            "pubkey": pub,
            "fingerprint": fp,
            "identity_file": self.path("keys", "fleet_ed25519"),
            # A peer's mail to us arrives on in/<their-fleet>.sock; we tell them
            # the directory so their invite carries the exact bind path.
            "inbound_dir": self.path("in"),
        }}

    def _seed_seen_dev(self, device):
        ids = set()
        tail = []
        try:
            with open(self.path("seen", device), encoding="utf-8") as f:
                tail = [l.strip() for l in f.readlines() if l.strip()][-500:]
        except OSError:
            pass
        for line in tail:
            ids.add(line)
        # Keep the on-disk file a ring too (it is append-only between loads).
        try:
            _atomic_write(self.path("seen", device), "\n".join(tail) + ("\n" if tail else ""))
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
        never blocks the redial. A fleet link adds -i <fleet key> +
        IdentitiesOnly (never the default agent keys) and takes the remote
        inbound path VERBATIM from the invite card — a forward-only key cannot
        run `echo $HOME`, so the path can never be composed."""
        with self.mu:
            ent = dict(self.links.get(device) or {})
        rin = ent.get("remote_in")
        if not rin:
            rin = "%s/.local/state/communicate/homi/in/%s.sock" % (
                remote_home or "<REMOTE_HOME>", self.device)
        lsock = self.path("links", device + ".sock")
        cmd = ["ssh", "-N",
               "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
               "-o", "ExitOnForwardFailure=yes",
               "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
               "-o", "StreamLocalBindMask=0177",
               "-o", "StreamLocalBindUnlink=yes"]
        idf = ent.get("identity_file")
        if idf:
            cmd += ["-i", idf, "-o", "IdentitiesOnly=yes"]
        cmd += ["-L", "%s:%s" % (lsock, rin), addr]
        return cmd

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
        # A fleet link carries the remote inbound path in its invite card, so we
        # never probe $HOME (the forward-only key forbids exec). Only device
        # links (full trust, same operator) resolve the home.
        if not rhome and not ent.get("remote_in"):
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

    def _do_link(self, device, addr=None, sock=None, print_cmd=False,
                 allow_seats=None, fleet=False, remote_in=None,
                 identity_file=None, key_fp=None):
        if not self._DEV_RE.match(device or ""):
            return {"ok": False, "err": "invalid device name"}
        if device == self.device:
            return {"ok": False, "err": "refusing to link to self"}
        # Seat-control grant: opt-in per link, upgradable in place. None = keep
        # the current grant; True/False = set it. A link carries mail by default;
        # driving this device's seats from the far side requires an explicit grant.
        prior = self.links.get(device) or {}
        prior_grant = prior.get("allow_seats", False)
        # Fleet (cross-operator) vs device (same-operator, full trust) link kind.
        kind = "fleet" if (fleet or prior.get("kind") == "fleet") else prior.get("kind", "device")
        remote_in = remote_in or prior.get("remote_in")
        identity_file = identity_file or prior.get("identity_file")
        key_fp = key_fp or prior.get("key_fp")
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
        if prev.get("addr") and addr and prev["addr"] != addr:
            # Address changed: the old tunnel would silently keep carrying
            # traffic to the old host. Kill it and re-resolve the remote home.
            proc = self.ssh_procs.pop(device, None)
            if proc is not None and proc.poll() is None:
                try:
                    proc.terminate()
                except OSError:
                    pass
            prev = {}
        grant = prior_grant if allow_seats is None else bool(allow_seats)
        with self.mu:
            self.links[device] = {"addr": addr, "sock": sock,
                                  "remote_home": (prev.get("remote_home")
                                                  if prev.get("addr") == addr else None),
                                  "allow_seats": grant,
                                  "kind": kind, "remote_in": remote_in,
                                  "identity_file": identity_file, "key_fp": key_fp,
                                  "created_at": time.time()}
        self._persist_links()
        self.out_ev.set()
        self.log("linked %s:" % kind, device, "->", sock or addr or "?",
                 "(seats %s)" % ("granted" if grant else "denied"))
        return {"ok": True, "allow_seats": grant, "kind": kind}

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
            e = e or {}
            r = self._do_link(device, addr=e.get("addr"), sock=e.get("sock"),
                              allow_seats=e.get("allow_seats", False),
                              fleet=(e.get("kind") == "fleet"),
                              remote_in=e.get("remote_in"),
                              identity_file=e.get("identity_file"),
                              key_fp=e.get("key_fp"))
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
        kind = env.get("kind")
        if env.get("v") != 1 or kind not in ("m", "r", "seat"):
            return {"ok": False, "err": "bad envelope"}
        mid = env.get("msg_id") or ""
        if not mid:
            return {"ok": False, "err": "bad envelope"}
        if kind == "seat":
            # A seat op driven from a linked device. Opt-in per link: the far
            # device may drive our seats only if this link was granted
            # --allow-seats. Execute locally and return the RESULT in the ack
            # (seat ops are synchronous request/response, NOT queued mail, so no
            # dedup — one shot, executed live).
            with self.mu:
                granted = (self.links.get(device) or {}).get("allow_seats", False)
            if not granted:
                return {"ok": False, "err": "seat control not granted for %s "
                        "(run: homi link %s --allow-seats)" % (device, device)}
            result = self._do_seat(env.get("sub", ""), env.get("args") or {})
            return {"ok": True, "ack": mid, "result": result}
        to = env.get("to") or ""
        frm = env.get("from") or "unknown"
        text = env.get("text") or ""
        if not to:
            return {"ok": False, "err": "bad envelope"}
        if kind == "m" and not text:
            return {"ok": False, "err": "bad envelope"}
        if mid in self.seen_dev.setdefault(device, set()):
            return {"ok": True, "ack": mid, "dup": True}
        # A fleet (cross-operator) link is deny-by-default: the sender may only
        # address explicitly-granted names, no name is auto-created for them, and
        # the sender is proxied FLEET-QUALIFIED so a foreign fleet can never
        # shadow or squat a local name. Same-operator device links keep the
        # trusting behaviour (auto-claim, bare proxies).
        fleet = self._link_fleet(device)
        if fleet and not self._is_granted(fleet, to):
            # One deliberately-ambiguous error: never reveal which names exist.
            return {"ok": False, "err": "unknown or ungranted"}
        with self.mu:
            local_to = (to in self.identities
                        and self.identities[to].get("kind") == "local")
        if kind == "r":
            # A reply routed home from a remote target: resolve the pending ask
            # (by corr) and store durably in the asker's inbox.
            corr = env.get("corr") or ""
            self._fill_ask(corr, text, frm)
            if not local_to:
                if fleet:
                    return {"ok": False, "err": "unknown or ungranted"}
                self._do_claim(to)  # device link: auto-claim as before
            self._store(to, {"ts": time.time(), "msg_id": mid, "from": "",
                             "from_name": frm, "text": text, "corr": corr})
            self._remember_dev_msg(device, mid)
            threading.Thread(target=self._safe_deliver, args=(to,), daemon=True).start()
            return {"ok": True, "ack": mid}
        proxy_name = ("%s@%s" % (frm, fleet)) if fleet else frm
        if fleet:
            # Boundary rewrite (the token is a return address): an ask token
            # embeds the asker's DEVICE name, which means nothing here — the
            # valid route back is the arrival link's fleet petname.
            text = re.sub(
                r"(reply\s+[a-z0-9][a-z0-9._-]*)@[a-z0-9._-]+~([0-9a-f]+)",
                r"\1@%s~\2" % fleet, text)
        self._ensure_proxy(proxy_name, device)
        with self.mu:
            ent = self.identities.get(to)
            tkind = ent.get("kind") if ent else None
        if tkind == "proxy":
            return {"ok": False,
                    "err": "%s is not local here (multi-hop not supported)" % to}
        if ent is None:
            if fleet:
                # No mailbox-creation oracle for foreigners: a granted name that
                # is not actually claimed here is treated as absent.
                return {"ok": False, "err": "unknown or ungranted"}
            r = self._do_claim(to)  # device link: local mail never bounces
            if not r.get("ok"):
                return {"ok": False, "err": "cannot claim %s: %s" % (to, r.get("err"))}
            self.log("auto-claimed", to, "for inbound mail via", device)
        entry = {"ts": time.time(), "msg_id": mid, "from": "",
                 "from_name": proxy_name, "via": device, "text": text}
        self._store(to, entry)
        self._resolve_ask_natural(to, text, frm)
        self._remember_dev_msg(device, mid)
        # Ack now (the message is durable); wake asynchronously — a slow local
        # session must not push the sender into ack-timeout retry churn.
        threading.Thread(target=self._safe_deliver, args=(to,), daemon=True).start()
        return {"ok": True, "ack": mid}

    def _safe_deliver(self, name):
        try:
            self._deliver_pending(name)
        except Exception as e:
            self.log("wake failed:", name, e)

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

    def _send_envelope_result(self, endpoint, env, timeout=10.0):
        """Send an envelope and return the FULL ack dict (for synchronous
        request/response like seat ops, where the ack carries the result)."""
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            s.connect(endpoint)
            s.sendall((json.dumps(env) + "\n").encode("utf-8"))
            raw = cc_peer._read_line(s, timeout=timeout)
        finally:
            try:
                s.close()
            except OSError:
                pass
        line = raw.split(b"\n", 1)[0].strip()
        if not line:
            raise OSError("no ack")
        return json.loads(line.decode("utf-8", "replace"))

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
        if resp.get("ok") and resp.get("ack") == env["msg_id"]:
            return
        if resp.get("ok") is False and resp.get("err"):
            raise NegativeAck(resp["err"])   # deterministic refusal: dead-letter
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
                    files = sorted(f for f in os.listdir(qdir)
                                   if f.endswith(".json")
                                   and os.path.isfile(os.path.join(qdir, f)))
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
                    except NegativeAck as e:
                        # A refusal is final: dead-letter and KEEP DRAINING —
                        # one poison envelope must never wedge the queue.
                        dead = os.path.join(qdir, "dead")
                        os.makedirs(dead, exist_ok=True)
                        try:
                            os.replace(fp, os.path.join(dead, fn))
                        except OSError:
                            pass
                        self.log("link", dev, "dead-lettered", fn, "—", e)
                        continue
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
                      e.get("home"), e.get("seat"), e.get("boxed", False))
                     for n, e in self.identities.items()]
        idents = {}
        for n, sockp, claimed, kind, home, seat, boxed in items:
            if kind == "proxy":
                idents[n] = {"kind": "proxy", "home": home, "sock": sockp,
                             "claimed_at": claimed}
                continue
            if boxed:
                # A boxed agent's liveness is the published socket, measured.
                alive = probe(sockp) == "live"
                with self.mail_mu:
                    count = len(self._inbox_lines(n))
                    cur = self._read_cursor(n)
                idents[n] = {
                    "kind": "local", "sock": sockp, "claimed_at": claimed,
                    "boxed": True,
                    "route": {"state": "live" if alive else "stored",
                              "provenance": "boxed-probed", "session": None},
                    "inbox": {"count": count, "undelivered": max(0, count - cur)},
                    "seat": seat,
                }
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
                "seat": seat,
            }
        with self.mu:
            linkents = {d: dict(e) for d, e in self.links.items()}
        links = {}
        for d, e in linkents.items():
            lst = self.link_state.get(d, {})
            qdir = self.path("out", d)
            try:
                q = len([f for f in os.listdir(qdir)
                         if f.endswith(".json")
                         and os.path.isfile(os.path.join(qdir, f))])
            except OSError:
                q = 0
            try:
                dead = len(os.listdir(os.path.join(qdir, "dead")))
            except OSError:
                dead = 0
            links[d] = {"endpoint": e.get("sock") or e.get("addr"),
                        "addr": e.get("addr"), "kind": e.get("kind", "device"),
                        "allow_seats": e.get("allow_seats", False),
                        "queue": q, "dead": dead, "last_ok": lst.get("last_ok"),
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
        if op == "agents":
            return self._do_agents()
        if op == "inbox":
            return self._do_inbox(req.get("name", ""),
                                  tail=int(req.get("tail") or 50),
                                  after_msg_id=req.get("after_msg_id"))
        if op == "wait":
            return self._do_wait(req.get("name", ""),
                                 float(req.get("timeout") or 60),
                                 after_msg_id=req.get("after_msg_id"))
        if op == "claim":
            return self._do_claim(req.get("name", ""),
                                  boxed=bool(req.get("boxed")),
                                  cwd=req.get("cwd"),
                                  worktree=bool(req.get("worktree")))
        if op == "release":
            return self._do_release(req.get("name", ""))
        if op == "send":
            return self._do_send(req.get("to", ""), req.get("text", ""),
                                 req.get("from") or "cli")
        if op == "ask":
            return self._do_ask(req.get("to", ""), req.get("text", ""),
                                req.get("from") or "asker",
                                float(req.get("timeout") or 240))
        if op == "reply":
            return self._do_reply(req.get("token", ""), req.get("text", ""),
                                  req.get("from") or "")
        if op == "group":
            return self._do_group(req.get("names") or [], req.get("text", ""),
                                  req.get("from") or "cli")
        if op == "notify":
            return self._do_notify(req.get("reason", ""), req.get("from") or "")
        if op == "seat":
            return self._do_seat(req.get("sub", ""), req)
        if op == "spawn":
            return self._do_spawn(req.get("name", ""), req.get("cmd", ""),
                                  cwd=req.get("cwd"), adopt=bool(req.get("adopt")))
        if op == "fan":
            return self._do_fan(int(req.get("n") or 1), req.get("cmd", ""),
                                req.get("prefix") or "worker", cwd=req.get("cwd"),
                                adopt=bool(req.get("adopt")))
        if op == "consult":
            return self._do_consult(req.get("name", ""), req.get("cmd", ""),
                                    req.get("text", ""),
                                    float(req.get("timeout") or 120),
                                    adopt=bool(req.get("adopt")))
        if op == "link":
            return self._do_link(req.get("device", ""), addr=req.get("addr"),
                                 sock=req.get("sock"),
                                 print_cmd=bool(req.get("print_cmd")),
                                 allow_seats=req.get("allow_seats"),
                                 fleet=bool(req.get("fleet")),
                                 remote_in=req.get("remote_in"),
                                 identity_file=req.get("identity_file"),
                                 key_fp=req.get("key_fp"))
        if op == "move":
            # The MCP face can't shell out, so the daemon runs the SAME shared
            # orchestration, dispatching its own ops instead of socket calls.
            return _move_run(self.op, req.get("name", ""), req.get("device", ""),
                             addr=req.get("addr"), as_name=req.get("as"),
                             spawn=bool(req.get("spawn")),
                             fork=bool(req.get("fork")),
                             dry=bool(req.get("dry_run")))
        if op == "premove":
            return self._do_premove(req.get("name", ""))
        if op == "depart":
            return self._do_depart(req.get("name", ""), req.get("device", ""))
        if op == "arrive":
            return self._do_arrive(req.get("name", ""), req.get("staged", ""),
                                   req.get("cursor", 0))
        if op == "grant":
            return self._do_grant(req.get("fleet", ""), req.get("name", ""))
        if op == "revoke-grant":
            return self._do_revoke_grant(req.get("fleet", ""), req.get("name", ""))
        if op == "grants":
            return {"ok": True, "grants": {f: sorted(s) for f, s in self.grants.items()}}
        if op == "card":
            return self._do_card()
        if op == "unlink":
            return self._do_unlink(req.get("device", ""))
        if op == "stop":
            threading.Thread(target=self._delayed_stop, daemon=True).start()
            return {"ok": True, "stopping": True}
        return {"ok": False, "err": "unknown op: %r" % op}

    def _delayed_stop(self):
        time.sleep(0.2)  # let the stop reply flush to the client first
        self.stop_ev.set()  # the MAIN thread runs shutdown (no lock reentrancy)

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
        # Control-plane gate. Once any fleet (cross-operator) link exists, a
        # forward-only peer could reach homi.sock by forwarding to it — so
        # control ops must present the control token, a file they cannot read
        # (the forward-only key forbids exec/read). Local/device-only use keeps
        # the 0700 dir as the only guard (no token needed).
        if self._needs_token() and req.get("op") is not None:
            # Once a fleet link exists, EVERY control op needs the token —
            # including status, which returns the full roster + link topology
            # (a name-enumeration oracle that would defeat the fleet path's
            # deliberate "unknown or ungranted" masking). Local + MCP callers
            # attach `auth` from control.token automatically. A bare liveness
            # probe (empty line, op=None) is the only exemption.
            if req.get("auth") != self.control_token:
                resp = {"ok": False, "err": "control token required (fleet link active)"}
                try:
                    conn.sendall((json.dumps(resp) + "\n").encode("utf-8"))
                    conn.close()
                except OSError:
                    pass
                return
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
        with self.mu:
            boxed = {n: self.identities[n].get("boxed", False) for n in names}
        for name in names:
            if boxed.get(name):
                # A boxed identity's socket belongs to the CONTAINER's vsock
                # forwarder — never rebind it. Deliver held mail whenever the
                # published socket answers (the box's store→wake).
                with self.mail_mu:
                    undeliv = len(self._inbox_lines(name)) > self._read_cursor(name)
                if undeliv and probe(socks[name]) == "live":
                    threading.Thread(target=self._safe_deliver, args=(name,),
                                     daemon=True).start()
                continue
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
    def _on_signal(self, *_a):
        self.stop_ev.set()

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
        # Handlers only set the event: shutdown() takes locks the interrupted
        # main thread may already hold (a handler calling it would deadlock).
        signal.signal(signal.SIGTERM, self._on_signal)
        signal.signal(signal.SIGINT, self._on_signal)
        self.log("homi starting: device=%s pid=%d root=%s sockdir=%s"
                 % (self.device, self.pid, self.root, self.sockdir))
        self.control_token = self._ensure_control_token()
        self._load_grants()
        threading.Thread(target=self.control_server, daemon=True).start()
        self._load_identities()
        self._load_links()
        threading.Thread(target=self.outbound_loop, daemon=True).start()
        self.write_routes()
        self.tick_loop()
        self.shutdown()


# ---- CLI client ("call") -------------------------------------------------------

def _encode_card(card):
    import base64
    return base64.b64encode(json.dumps(card).encode()).decode()


def _decode_card(blob):
    import base64
    return json.loads(base64.b64decode(blob.encode()))


def _authkeys_path():
    return os.path.join(os.path.expanduser("~"), ".ssh", "authorized_keys")


_PUBKEY_RE = re.compile(
    r"^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp256|ecdsa-sha2-nistp384|"
    r"ecdsa-sha2-nistp521|sk-ssh-ed25519@openssh\.com) "
    r"[A-Za-z0-9+/]+={0,3}( [^\r\n]*)?$")
_IP_RE = re.compile(r"^[0-9a-fA-F:.]{3,45}$")


def _authkeys_add(pubkey, peer_fleet, from_ip):
    """Append the peer's forward-only key so THEY can dial into us. restrict =
    deny-all; port-forwarding re-grants exactly the transport; the forced command
    makes any exec attempt run /usr/bin/false; from= pins the source IP. A marker
    comment makes revocation exact. Idempotent by marker.

    Every field is peer-controlled (from a base64 card), so a smuggled newline
    in `pubkey`/`from_ip` could write a SECOND, unrestricted authorized_keys
    line = full host compromise. Validate strictly (single line, exact shape)
    and refuse anything that doesn't match — never write unvalidated bytes."""
    pubkey = (pubkey or "").strip()
    if not _PUBKEY_RE.match(pubkey):
        raise ValueError("refusing card: pubkey is not a single well-formed ssh key")
    if from_ip and not _IP_RE.match(from_ip):
        raise ValueError("refusing card: tailscale_ip is not an IP")
    if not re.match(r"^[a-z0-9][a-z0-9._-]{0,63}$", peer_fleet or ""):
        raise ValueError("refusing card: bad fleet name")
    marker = "homi-fleet:%s" % peer_fleet
    p = _authkeys_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    existing = ""
    try:
        with open(p) as f:
            existing = f.read()
    except OSError:
        pass
    if marker in existing:
        return "already present"
    frm = ('from="%s",' % from_ip) if from_ip else ""
    line = ('restrict,port-forwarding,%scommand="/usr/bin/false" %s %s\n'
            % (frm, pubkey, marker))
    with open(p, "a") as f:
        f.write(line)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    return "added"


def _authkeys_remove(peer_fleet):
    marker = "homi-fleet:%s" % peer_fleet
    p = _authkeys_path()
    try:
        with open(p) as f:
            lines = f.readlines()
    except OSError:
        return "no authorized_keys"
    kept = [l for l in lines if marker not in l]
    if len(kept) == len(lines):
        return "not found"
    with open(p, "w") as f:
        f.writelines(kept)
    return "removed"


def _cli_federate(args):
    if not args:
        sys.stderr.write("usage: communicate homi federate {invite|accept|revoke|status} ...\n")
        return 1
    sub, rest = args[0], args[1:]
    if sub == "invite":
        r = _call({"op": "card"})
        if not r.get("ok"):
            sys.stderr.write((r.get("err") or "failed") + "\n")
            return 1
        card = r["card"]
        blob = _encode_card(card)
        peer = rest[0] if rest else "<peer>"
        print("# Your homi-card (fleet=%s, device=%s, fp=%s)."
              % (card["fleet"], card["device"], card.get("fingerprint")))
        print("# Send it to %s over a trusted channel; they run:" % peer)
        print("communicate homi federate accept %s --card '%s'" % (card["fleet"], blob))
        return 0
    if sub == "accept":
        if not rest:
            sys.stderr.write("usage: communicate homi federate accept <peer-fleet> --card '<blob>' "
                             "[--yes] [--no-authkey]\n")
            return 1
        peer_fleet = rest[0]
        if "--card" not in rest:
            sys.stderr.write("--card '<blob>' required\n")
            return 1
        try:
            peer = _decode_card(rest[rest.index("--card") + 1])
        except Exception as e:
            sys.stderr.write("bad card: %s\n" % e)
            return 1
        if peer.get("kind") != "homi-card":
            sys.stderr.write("not a homi-card\n")
            return 1
        mine = _call({"op": "card"})
        if not mine.get("ok"):
            sys.stderr.write("cannot read own card: %s\n" % mine.get("err"))
            return 1
        my_fleet = mine["card"]["fleet"]
        sys.stderr.write("peer fleet=%s device=%s fingerprint=%s\n"
                         % (peer.get("fleet"), peer.get("device"), peer.get("fingerprint")))
        if not re.match(r"^[a-z0-9][a-z0-9._-]{0,63}$", peer_fleet):
            sys.stderr.write("bad peer-fleet name\n")
            return 1
        if "--yes" not in rest:
            sys.stderr.write("re-run with --yes to install the key + link.\n")
            return 1
        if "--no-authkey" not in rest and peer.get("pubkey"):
            try:
                st = _authkeys_add(peer["pubkey"], peer_fleet, peer.get("tailscale_ip"))
            except ValueError as e:
                sys.stderr.write(str(e) + "\n")
                return 1
            sys.stderr.write("authorized_keys: %s\n" % st)
        # Link outbound to them: our mail lands on THEIR in/<my_fleet>.sock.
        # Guard the card's inbound_dir (it flows into a socket path we dial).
        idir = peer.get("inbound_dir", "")
        if "\n" in idir or "\x00" in idir:
            sys.stderr.write("refusing card: bad inbound_dir\n")
            return 1
        remote_in = os.path.join(idir, my_fleet + ".sock")
        req = {"op": "link", "device": peer_fleet, "fleet": True,
               "addr": peer.get("addr"), "remote_in": remote_in,
               "identity_file": mine["card"].get("identity_file"),
               "key_fp": peer.get("fingerprint")}
        r = _call(req)
        if r.get("ok"):
            print("federated with %s (fleet link). Grant identities with: "
                  "communicate homi grant %s <name>" % (peer_fleet, peer_fleet))
            return 0
        sys.stderr.write((r.get("err") or "link failed") + "\n")
        return 1
    if sub == "revoke":
        if not rest:
            sys.stderr.write("usage: communicate homi federate revoke <peer-fleet>\n")
            return 1
        peer_fleet = rest[0]
        st = _authkeys_remove(peer_fleet)
        _call({"op": "unlink", "device": peer_fleet})
        print("revoked %s (authorized_keys: %s, link removed)" % (peer_fleet, st))
        return 0
    if sub == "status":
        c = _call({"op": "card"})
        if c.get("ok"):
            card = c["card"]
            print("fleet=%s device=%s ip=%s fp=%s"
                  % (card["fleet"], card["device"], card.get("tailscale_ip"),
                     card.get("fingerprint")))
        g = _call({"op": "grants"})
        for f, names in sorted((g.get("grants") or {}).items()):
            print("  grant %-16s %s" % (f, ", ".join(names) or "(none)"))
        return 0
    sys.stderr.write("unknown federate subcommand: %s\n" % sub)
    return 1


# ---- move: relocate an agent-being to another device (CLI orchestration) ------

def _projects_dir():
    return os.path.join(os.environ.get("CLAUDE_CONFIG_DIR")
                        or os.path.expanduser("~/.claude"), "projects")


def _slug(p):
    return re.sub(r"[^A-Za-z0-9]", "-", p)


def _last_title(path):
    t = ""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for ln in f:
                if '"type":"custom-title"' in ln or '"type": "custom-title"' in ln:
                    try:
                        t = json.loads(ln).get("customTitle") or t
                    except Exception:
                        pass
    except OSError:
        pass
    return t


def _last_cwd(path):
    cwd = ""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for ln in f:
                m = re.search(r'"cwd":\s*"([^"]+)"', ln)
                if m:
                    cwd = m.group(1)
    except OSError:
        pass
    return cwd


def _find_transcript(name):
    """The transcript whose latest custom-title is `name` (newest wins) — the
    identity-in-the-artifact rule: the name lives inside the file."""
    import glob as _glob
    hits = []
    for f in _glob.glob(os.path.join(_projects_dir(), "*", "*.jsonl")):
        if _last_title(f) == name:
            hits.append((os.path.getmtime(f), f))
    if not hits:
        return None
    hits.sort(reverse=True)
    if len(hits) > 1:
        sys.stderr.write("note: %d transcripts named %r — using newest\n"
                         % (len(hits), name))
    return hits[0][1]


def _ssh_run(target, script, timeout=30):
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
                        target, "bash -lc " + _shq(script)],
                       capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()


def _shq(s):
    return "'" + s.replace("'", "'\\''") + "'"


def _cli_move(args):
    """communicate homi move <name> <device> [--addr user@host] [--as NEW]
         [--spawn] [--fork] [--dry-run]
    Relocate an agent-being: transcript (rsync) + mailbox (staged merge) +
    identity (depart->proxy at origin, arrive->claim at target). The address
    survives: after the move, mail to <name> routes over the link."""
    name = dev = addr = as_name = None
    spawn = fork = dry = False
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--addr" and i + 1 < len(args):
            addr = args[i + 1]; i += 2; continue
        if a == "--as" and i + 1 < len(args):
            as_name = args[i + 1]; i += 2; continue
        if a == "--spawn":
            spawn = True; i += 1; continue
        if a == "--fork":
            fork = True; i += 1; continue
        if a == "--dry-run":
            dry = True; i += 1; continue
        if name is None:
            name = a
        elif dev is None:
            dev = a
        i += 1
    if not name or not dev:
        sys.stderr.write("usage: communicate homi move <name> <device> "
                         "[--addr user@host] [--as NEW] [--spawn] [--fork] [--dry-run]\n")
        return 1
    r = _move_run(_call, name, dev, addr=addr, as_name=as_name,
                  spawn=spawn, fork=fork, dry=dry)
    for ln in r.get("lines") or []:
        print(ln)
    if not r.get("ok"):
        sys.stderr.write((r.get("err") or "move failed") + "\n")
        return 1
    return 0


def _move_run(caller, name, dev, addr=None, as_name=None, spawn=False,
              fork=False, dry=False):
    """The move orchestration, shared by BOTH faces: the CLI passes the socket
    client as `caller`, the daemon passes its own op dispatch. One
    implementation, so `homi move` and the MCP `move` tool can never drift.
    Returns {ok, err?, lines[], ...}."""
    report = []
    # Names/devices flow into ssh + rsync REMOTE paths (passed through the remote
    # login shell). Validate strictly so a crafted name can't inject a command.
    NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
    DEV_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    if not NAME_RE.match(name):
        return {"ok": False, "err": "invalid agent name\n", "lines": report}
    if not DEV_RE.match(dev):
        return {"ok": False, "err": "invalid device name\n", "lines": report}
    if as_name is not None and not NAME_RE.match(as_name):
        return {"ok": False, "err": "invalid --as name (want [a-z0-9][a-z0-9._-])\n", "lines": report}
    if addr is not None and (addr.startswith("-") or "\n" in addr
                             or not re.match(r"^[A-Za-z0-9._@-]+$", addr)):
        return {"ok": False, "err": "invalid --addr\n", "lines": report}
    if fork and not as_name:
        return {"ok": False, "err": ("--fork needs --as <new-name>: two live claimants of one " "name on two devices would diverge silently\n"), "lines": report}

    pre = caller({"op": "premove", "name": name})
    if not pre.get("ok"):
        return {"ok": False, "err": ((pre.get("err") or "premove failed")), "lines": report}
    if pre.get("live") and not fork:
        return {"ok": False, "err": ("%s has a LIVE session here — stop it first, or --fork " "--as <new-name> to copy a snapshot\n" % name), "lines": report}
    transcript = _find_transcript(name)
    sid = os.path.basename(transcript)[:-6] if transcript else None
    lproj = _last_cwd(transcript) if transcript else None

    # Resolve the ssh address: explicit --addr, or the link's stored addr.
    if not addr:
        st = caller({"op": "status"})
        addr = ((st.get("links") or {}).get(dev) or {}).get("addr")
    if not addr:
        return {"ok": False, "err": ("no ssh address for %s (link it with --addr, or pass --addr here)" % dev), "lines": report}

    if dry:
        report.append("agent     : %s%s" % (name, " (LIVE - fork)" if pre.get("live") else ""))
        report.append("transcript: %s" % (transcript or "(none - mailbox-only identity)"))
        report.append("mailbox   : %s lines, cursor %s" % (pre.get("lines"), pre.get("cursor")))
        report.append("target    : %s via %s" % (dev, addr))
        report.append("mode      : %s" % ("fork -> %s" % as_name if fork else "move (depart+arrive)"))
        return {"ok": True, "lines": report, "dry": True}

    # 1. probe the target: remote home + communicate + its homi state root.
    rc, out, err = _ssh_run(addr, "printf 'H:%s\\n' \"$HOME\"; "
                                  "printf 'C:%s\\n' \"$(command -v communicate || "
                                  "ls \"$HOME/.local/bin/communicate\" 2>/dev/null | head -1)\"; "
                                  "printf 'S:%s\\n' \"$(communicate homi statepath 2>/dev/null)\"")
    if rc != 0:
        return {"ok": False, "err": ("cannot reach %s (%s): %s" % (dev, addr, err)), "lines": report}
    rhome = rcomm = rstate = ""
    for ln in out.splitlines():
        if ln.startswith("H:"):
            rhome = ln[2:]
        elif ln.startswith("C:"):
            rcomm = ln[2:]
        elif ln.startswith("S:"):
            rstate = ln[2:]
    if not rhome or not rcomm:
        return {"ok": False, "err": ("target %s lacks communicate on PATH — install it there first" % dev), "lines": report}
    if not rstate:
        rstate = rhome + "/.local/state/communicate/homi"

    target_name = as_name or name
    ssh_e = "ssh -o BatchMode=yes -o ConnectTimeout=8"

    # 2. transcript first (big + safe: the origin still owns the name).
    if transcript:
        rproj = lproj or rhome
        home = os.path.expanduser("~")
        if rproj.startswith(home) and rhome != home:
            rproj = rhome + rproj[len(home):]
        rdir = "%s/.claude/projects/%s" % (rhome, _slug(rproj))
        rc, _, err = _ssh_run(addr, "mkdir -p %s %s" % (_shq(rdir), _shq(rproj)))
        if rc != 0:
            return {"ok": False, "err": ("target prep failed: %s" % err), "lines": report}
        srcs = [transcript]
        side = os.path.join(os.path.dirname(transcript), sid)
        if os.path.isdir(side):
            srcs.append(side)
        rc = subprocess.run(["rsync", "-az", "-s", "-e", ssh_e] + srcs
                            + ["%s:%s/" % (addr, rdir)]).returncode
        if rc != 0:
            return {"ok": False, "err": ("transcript rsync failed\n"), "lines": report}
        if as_name:
            rec = json.dumps({"type": "custom-title", "customTitle": as_name,
                              "sessionId": sid})
            _ssh_run(addr, "printf '%s\\n' %s >> %s"
                     % ("%s", _shq(rec), _shq("%s/%s.jsonl" % (rdir, sid))))

    if not fork:
        # 3. depart: atomically release + proxy home=dev. From here, new mail
        #    routes over the link (the target auto-claims on first delivery).
        dep = caller({"op": "depart", "name": name, "device": dev})
        if not dep.get("ok"):
            return {"ok": False, "err": ((dep.get("err") or "depart failed")), "lines": report}

    # 4. mailbox (now frozen at the origin): stage + merge on the target.
    merged = {"merged_delivered": 0, "merged_undelivered": 0}
    if os.path.exists(pre["mailbox"]):
        staged = "%s/staging-%s-inbox.jsonl" % (rstate, target_name)
        rc, _, err = _ssh_run(addr, "mkdir -p %s" % _shq(rstate))
        rc = subprocess.run(["rsync", "-az", "-s", "-e", ssh_e, pre["mailbox"],
                             "%s:%s" % (addr, staged)]).returncode
        if rc != 0:
            return {"ok": False, "err": ("mailbox rsync failed (mail stays at origin; " "identity already departed)\n"), "lines": report}
        rc, out, err = _ssh_run(addr, "%s homi arrive %s --staged %s --cursor %s"
                                % (_shq(rcomm), _shq(target_name), _shq(staged),
                                   int(pre.get("cursor") or 0)), timeout=60)
        try:
            merged = json.loads(out.splitlines()[-1]) if out else merged
        except Exception:
            pass
        _ssh_run(addr, "rm -f %s" % _shq(staged))
        if rc != 0:
            return {"ok": False, "err": ("arrive on %s failed: %s %s" % (dev, out, err)), "lines": report}
    else:
        # No mailbox file — still claim the name on the target.
        _ssh_run(addr, "%s homi arrive %s --staged /dev/null --cursor 0"
                 % (_shq(rcomm), _shq(target_name)))

    report.append("moved %s -> %s%s" % (name, dev,
          (" as %s" % as_name) if as_name else ""))
    if transcript:
        report.append("  transcript: %s:%s/" % (dev, rdir))
    report.append("  mailbox   : +%s delivered, +%s undelivered (deduped)"
          % (merged.get("merged_delivered"), merged.get("merged_undelivered")))
    if not fork:
        report.append("  address   : %s now proxies here -> %s (mail keeps flowing)" % (name, dev))

    # 5. optionally resume it in a seat over the cross-device seat plane.
    if spawn and sid:
        r = caller({"op": "seat", "sub": "spawn",
                   "cmd": "claude --resume %s" % sid,
                   "cwd": rproj if transcript else None,
                   "device": dev, "name": target_name})
        if r.get("ok"):
            report.append("  seat      : resumed in %s" % r.get("seat"))
        else:
            report.append("  seat      : not spawned (%s) — resume there: claude --resume %s"
                  % (r.get("err"), sid))
    elif sid:
        report.append("  resume    : (on %s) cd %s && claude --resume %s"
              % (dev, rproj if transcript else "~", sid))
    return {"ok": True, "lines": report, "name": name, "device": dev,
            "as": as_name, "merged": merged}


def _call(req, timeout=10.0):
    path = os.path.join(state_root(), "homi.sock")
    # Carry the control credential if one exists (harmless when the daemon isn't
    # enforcing it; required once a fleet link is active). Local file read —
    # the 0700 state dir means only this uid can see it.
    if "auth" not in req:
        try:
            with open(os.path.join(state_root(), "control.token")) as f:
                tok = f.read().strip()
            if tok:
                req = dict(req, auth=tok)
        except OSError:
            pass
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
    if op == "agents":
        r = _call({"op": "agents"})
        if "--json" in args:
            print(json.dumps(r, indent=1))
        else:
            for a in r.get("agents", []):
                seat = ("  seat:" + a["seat"]) if a.get("seat") else ""
                print("%-22s %-6s %-8s%s" % (a["name"], a["kind"],
                                             a.get("state") or "-", seat))
        return 0 if r.get("ok") else 1
    if op == "wait":
        if not args:
            sys.stderr.write("usage: communicate homi wait <name> [--timeout SEC] [--json]\n")
            return 1
        to = 60.0
        if "--timeout" in args:
            to = float(args[args.index("--timeout") + 1])
        name = [a for a in args if not a.startswith("--") and a != str(to)][0]
        r = _call({"op": "wait", "name": name, "timeout": to}, timeout=to + 15)
        if "--json" in args:
            print(json.dumps(r))
        elif r.get("ok"):
            for m in r.get("messages", []):
                print("%s: %s" % (m.get("from_name") or "?", m.get("text", "")))
        else:
            sys.stderr.write("timeout\n")
        return 0 if r.get("ok") else 1
    if op in ("claim", "release"):
        boxed = "--boxed" in args
        worktree = "--worktree" in args
        cwd = None
        if "--cwd" in args:
            i = args.index("--cwd")
            try:
                cwd = args[i + 1]
            except IndexError:
                sys.stderr.write("--cwd needs a path\n")
                return 1
            args = args[:i] + args[i + 2:]
        args = [a for a in args if a not in ("--boxed", "--worktree")]
        if not args:
            sys.stderr.write("usage: communicate homi %s <name> "
                             "[--cwd DIR] [--worktree] [--boxed]\n" % op)
            return 1
        req = {"op": op, "name": args[0]}
        if op == "claim":
            if boxed:
                req["boxed"] = True
            if cwd:
                req["cwd"] = cwd
            if worktree:
                req["worktree"] = True
        r = _call(req)
        if r.get("ok"):
            if r.get("boxed"):
                print("claimed %s (boxed). Publish these from the container:" % args[0])
                print("  --publish-socket %s:/run/homi/agent.sock" % r.get("publish_in"))
                print("  --publish-socket %s:/run/homi/outbox.sock" % r.get("publish_out"))
            else:
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
    if op == "ask":
        frm, timeout, want_json = "asker", 240.0, False
        rest = []
        i = 0
        while i < len(args):
            a = args[i]
            if a == "--from" and i + 1 < len(args):
                frm = args[i + 1]; i += 2; continue
            if a == "--timeout" and i + 1 < len(args):
                timeout = float(args[i + 1]); i += 2; continue
            if a == "--json":
                want_json = True; i += 1; continue
            rest.append(a); i += 1
        if len(rest) < 2:
            sys.stderr.write("usage: communicate homi ask <name> <question...> "
                             "[--from NAME] [--timeout SEC] [--json]\n")
            return 1
        # The blocking ask holds the control connection; give the socket slack.
        r = _call({"op": "ask", "to": rest[0], "text": " ".join(rest[1:]),
                   "from": frm, "timeout": timeout}, timeout=timeout + 15)
        if want_json:
            print(json.dumps(r))
        elif r.get("ok"):
            print(r.get("reply", ""))
        else:
            sys.stderr.write((r.get("err") or "failed") + "\n")
        return 0 if r.get("ok") else 1
    if op == "reply":
        frm = ""
        if "--from" in args:
            i = args.index("--from")
            try:
                frm = args[i + 1]
            except IndexError:
                sys.stderr.write("--from needs a value\n"); return 1
            args = args[:i] + args[i + 2:]
        if len(args) < 2:
            sys.stderr.write("usage: communicate homi reply <token> <answer...> [--from NAME]\n")
            return 1
        r = _call({"op": "reply", "token": args[0], "text": " ".join(args[1:]),
                   "from": frm})
        if r.get("ok"):
            print("replied" + (" (resolved)" if r.get("resolved") else ""))
            return 0
        sys.stderr.write((r.get("err") or "failed") + "\n")
        return 1
    if op == "group":
        frm = "cli"
        if "--from" in args:
            i = args.index("--from")
            try:
                frm = args[i + 1]
            except IndexError:
                sys.stderr.write("--from needs a value\n"); return 1
            args = args[:i] + args[i + 2:]
        if len(args) < 2:
            sys.stderr.write("usage: communicate homi group <n1,n2,...> <message...> [--from NAME]\n")
            return 1
        names = [x for x in args[0].split(",") if x]
        r = _call({"op": "group", "names": names, "text": " ".join(args[1:]),
                   "from": frm})
        print(json.dumps(r.get("results", {})))
        return 0 if r.get("ok") else 1
    if op == "notify":
        frm = ""
        if "--from" in args:
            i = args.index("--from")
            try:
                frm = args[i + 1]
            except IndexError:
                sys.stderr.write("--from needs a value\n"); return 1
            args = args[:i] + args[i + 2:]
        if not args:
            sys.stderr.write("usage: communicate homi notify <reason...> [--from NAME]\n")
            return 1
        r = _call({"op": "notify", "reason": " ".join(args), "from": frm})
        print("notified" if r.get("ok") else (r.get("err") or "failed"))
        return 0 if r.get("ok") else 1
    if op in ("spawn", "fan", "consult"):
        # --cli claude|codex|<command>, or a raw command after --. adopt (via
        # /rename) defaults on for claude (a cc-socks peer), off otherwise.
        cli = None
        cmd = None
        cwd = prefix = None
        n = 1
        timeout = 120.0
        want_json = "--json" in args
        rest = []
        i = 0
        while i < len(args):
            a = args[i]
            if a == "--cli" and i + 1 < len(args):
                cli = args[i + 1]; i += 2; continue
            if a == "--cwd" and i + 1 < len(args):
                cwd = args[i + 1]; i += 2; continue
            if a == "--prefix" and i + 1 < len(args):
                prefix = args[i + 1]; i += 2; continue
            if a == "--n" and i + 1 < len(args):
                n = int(args[i + 1]); i += 2; continue
            if a == "--timeout" and i + 1 < len(args):
                timeout = float(args[i + 1]); i += 2; continue
            if a == "--":
                cmd = " ".join(args[i + 1:]); break
            if a == "--json":
                i += 1; continue
            rest.append(a); i += 1
        # Resolve the launch command + adopt default from --cli.
        adopt = False
        if cmd is None and cli:
            if cli == "claude":
                cmd = os.environ.get("HOMI_CLAUDE_CMD", "claude"); adopt = True
                # A homi-spawned worker must be able to RECEIVE homi mail as
                # turns without a human clicking through a held-message dialog —
                # scoped to this agent's process, never the user's settings.
                if "crossSessionInbound" not in cmd:
                    cmd += " --settings '" + json.dumps(
                        {"crossSessionInbound": "accept"}) + "'"
            elif cli == "codex":
                cmd = os.environ.get("HOMI_CODEX_CMD", "codex"); adopt = False
            else:
                cmd = cli  # a raw command name
        if not cmd:
            sys.stderr.write("provide --cli claude|codex|<cmd> or `-- <command>`\n")
            return 1
        if op == "spawn":
            if not rest:
                sys.stderr.write("usage: communicate homi spawn <name> --cli claude|codex "
                                 "[--cwd DIR] [--json]\n")
                return 1
            req = {"op": "spawn", "name": rest[0], "cmd": cmd, "cwd": cwd,
                   "adopt": adopt}
            r = _call(req, timeout=60)
            print(json.dumps(r) if want_json
                  else (("%s -> %s" % (r.get("name"), r.get("seat")))
                        if r.get("ok") else (r.get("err") or "failed")))
            return 0 if r.get("ok") else 1
        if op == "fan":
            req = {"op": "fan", "n": n, "cmd": cmd,
                   "prefix": prefix or (rest[0] if rest else "worker"),
                   "cwd": cwd, "adopt": adopt}
            r = _call(req, timeout=120)
            print(json.dumps(r.get("group", [])) if not want_json else json.dumps(r))
            return 0 if r.get("ok") else 1
        if op == "consult":
            if not rest:
                sys.stderr.write("usage: communicate homi consult <question...> "
                                 "--cli claude|codex [--timeout SEC] [--json]\n")
                return 1
            nm = "consult-" + (cli or "peer")
            req = {"op": "consult", "name": nm, "cmd": cmd,
                   "text": " ".join(rest), "timeout": timeout, "adopt": adopt}
            r = _call(req, timeout=timeout + 30)
            print(json.dumps(r) if want_json
                  else (r.get("reply", "") if r.get("ok") else (r.get("err") or "failed")))
            return 0 if r.get("ok") else 1
    if op == "statepath":
        print(state_root())
        return 0
    if op == "premove":
        if not args:
            sys.stderr.write("usage: communicate homi premove <name> [--json]\n")
            return 1
        r = _call({"op": "premove", "name": args[0]})
        print(json.dumps(r))
        return 0 if r.get("ok") else 1
    if op == "depart":
        if len(args) < 2:
            sys.stderr.write("usage: communicate homi depart <name> <device>\n")
            return 1
        r = _call({"op": "depart", "name": args[0], "device": args[1]})
        print(json.dumps(r))
        return 0 if r.get("ok") else 1
    if op == "arrive":
        name = args[0] if args else ""
        staged = cursor = None
        if "--staged" in args:
            staged = args[args.index("--staged") + 1]
        if "--cursor" in args:
            cursor = int(args[args.index("--cursor") + 1])
        if not name or staged is None:
            sys.stderr.write("usage: communicate homi arrive <name> --staged <inbox.jsonl> [--cursor N]\n")
            return 1
        r = _call({"op": "arrive", "name": name, "staged": staged,
                   "cursor": cursor or 0})
        print(json.dumps(r))
        return 0 if r.get("ok") else 1
    if op == "move":
        return _cli_move(args)
    if op == "seat":
        if not args:
            sys.stderr.write("usage: communicate homi seat "
                             "{ls|spawn|send|read|state|wait|respond|bind|kill} ...\n")
            return 1
        sub, sargs = args[0], args[1:]
        req = {"op": "seat", "sub": sub}
        sock_to = 12.0
        if sub == "ls":
            r = _call(req)
            for s in (r.get("seats") or []):
                print("%-7s %-12s %s" % (s["seat"], s["cmd"], s["title"]))
            return 0 if r.get("ok") else 1
        if sub == "spawn":
            cwd = winname = dev = None
            rest = []
            i = 0
            while i < len(sargs):
                if sargs[i] == "--cwd" and i + 1 < len(sargs):
                    cwd = sargs[i + 1]; i += 2; continue
                if sargs[i] == "--name" and i + 1 < len(sargs):
                    winname = sargs[i + 1]; i += 2; continue
                if sargs[i] == "--device" and i + 1 < len(sargs):
                    dev = sargs[i + 1]; i += 2; continue
                rest.append(sargs[i]); i += 1
            if not rest:
                sys.stderr.write("usage: communicate homi seat spawn <command...> "
                                 "[--cwd DIR] [--name WIN] [--device DEV]\n")
                return 1
            req.update({"cmd": " ".join(rest), "cwd": cwd, "name": winname,
                        "device": dev})
            r = _call(req, timeout=40)
            print(r.get("seat") if r.get("ok") else (r.get("err") or "failed"))
            return 0 if r.get("ok") else 1
        if sub in ("send",):
            if len(sargs) < 2:
                sys.stderr.write("usage: communicate homi seat send <seat> <text...>\n")
                return 1
            req.update({"seat": sargs[0], "text": " ".join(sargs[1:])})
            r = _call(req)
            if r.get("ok"):
                print("sent")
                return 0
            sys.stderr.write((r.get("err") or "failed") + "\n")
            return 2 if r.get("sent") else 1
        if sub == "read":
            raw = "--raw" in sargs
            lines = 40
            if "--lines" in sargs:
                lines = int(sargs[sargs.index("--lines") + 1])
            seatid = [a for a in sargs if not a.startswith("--")
                      and a != str(lines)][0] if sargs else ""
            req.update({"seat": seatid, "lines": lines, "raw": raw})
            r = _call(req)
            if r.get("ok"):
                print(r.get("screen", ""))
                return 0
            sys.stderr.write((r.get("err") or "failed") + "\n")
            return 1
        if sub == "state":
            req["seat"] = sargs[0] if sargs else ""
            r = _call(req)
            print(r.get("state") if r.get("ok") else (r.get("err") or "failed"))
            return 0 if r.get("ok") else 1
        if sub == "wait":
            to = 120.0
            if "--timeout" in sargs:
                to = float(sargs[sargs.index("--timeout") + 1])
            req.update({"seat": sargs[0] if sargs else "", "timeout": to})
            r = _call(req, timeout=to + 15)
            print(json.dumps({k: r.get(k) for k in ("ok", "state", "sawbusy")}))
            return 0 if r.get("ok") else 1
        if sub == "respond":
            req.update({"seat": sargs[0] if sargs else "",
                        "decision": "deny" if "--deny" in sargs else "allow"})
            r = _call(req)
            print(r.get("responded") if r.get("ok") else (r.get("err") or "failed"))
            return 0 if r.get("ok") else 1
        if sub == "bind":
            if len(sargs) < 2:
                sys.stderr.write("usage: communicate homi seat bind <seat> <name>\n")
                return 1
            req.update({"seat": sargs[0], "name": sargs[1]})
            r = _call(req)
            print("bound" if r.get("ok") else (r.get("err") or "failed"))
            return 0 if r.get("ok") else 1
        if sub in ("kill", "interrupt"):
            req["seat"] = sargs[0] if sargs else ""
            r = _call(req)
            print("ok" if r.get("ok") else (r.get("err") or "failed"))
            return 0 if r.get("ok") else 1
        sys.stderr.write("unknown seat op: %s\n" % sub)
        return 1
    if op in ("grant", "ungrant"):
        if len(args) < 2:
            sys.stderr.write("usage: communicate homi %s <fleet> <name>\n" % op)
            return 1
        r = _call({"op": "grant" if op == "grant" else "revoke-grant",
                   "fleet": args[0], "name": args[1]})
        if r.get("ok"):
            print("%s: %s" % (r["fleet"], ", ".join(r.get("granted") or []) or "(none)"))
            return 0
        sys.stderr.write((r.get("err") or "failed") + "\n")
        return 1
    if op == "grants":
        r = _call({"op": "grants"})
        for f, names in sorted((r.get("grants") or {}).items()):
            print("%-20s %s" % (f, ", ".join(names) or "(none)"))
        return 0 if r.get("ok") else 1
    if op == "card":
        r = _call({"op": "card"})
        if r.get("ok"):
            print(json.dumps(r["card"], indent=1) if "--json" in args
                  else _encode_card(r["card"]))
            return 0
        sys.stderr.write((r.get("err") or "failed") + "\n")
        return 1
    if op == "federate":
        return _cli_federate(args)
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
        if "--allow-seats" in args:
            req["allow_seats"] = True
        if "--revoke-seats" in args:
            req["allow_seats"] = False
        if "--fleet" in args:
            req["fleet"] = True
        for flag, key in (("--remote-in", "remote_in"),
                          ("--identity-file", "identity_file"),
                          ("--key-fp", "key_fp")):
            if flag in args:
                try:
                    req[key] = args[args.index(flag) + 1]
                except IndexError:
                    sys.stderr.write("%s needs a value\n" % flag)
                    return 1
        r = _call(req)
        if r.get("ok") and r.get("cmd"):
            print(" ".join(r["cmd"]))
            return 0
        if r.get("ok"):
            seatnote = ""
            if "allow_seats" in r:
                seatnote = " (seats %s)" % ("granted" if r["allow_seats"] else "denied")
            print("%sed %s%s" % (op, args[0], seatnote))
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
