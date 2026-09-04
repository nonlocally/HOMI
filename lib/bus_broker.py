#!/usr/bin/env python3
"""Communicate's explicit bus registry and message gateway (stdlib only).

The HTTP listener is loopback-only. Remote clients reach it through a TLS
reverse proxy; no agent socket, shell, or local administration credential is
published by this module. Every data operation authenticates independently.
"""
import base64
import hashlib
import hmac
import http.server
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import tempfile
import threading
import time
from urllib.parse import urlsplit
import uuid


MAX_BODY = 131072
MAX_MESSAGE = 32768
MAX_PENDING = 256
MAX_AGENTS = 128
MAX_TOTAL_AGENTS = 4096
MAX_PRINCIPALS = 1024
MAX_BUSES = 256
MAX_INVITES = 4096
MAX_RECORDS = 20000
MESSAGE_TTL = 86400
LIVE_TTL = 45
LEASE_TTL = 60
BUS_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,62}\Z")
ACTIVE = ("accepted", "leased")
TERMINAL = ("delivered", "queued", "failed", "expired", "cancelled")


class BusError(Exception):
    def __init__(self, message, code="invalid_request"):
        super().__init__(message)
        self.code = code


def _digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _text(value, label, limit, optional=False):
    if not isinstance(value, str) or len(value.encode("utf-8")) > limit:
        raise BusError("invalid %s" % label)
    if (not optional and not value.strip()) or any(ord(c) < 32 for c in value):
        raise BusError("invalid %s" % label)
    return value


def _bus(value):
    if not isinstance(value, str) or not BUS_RE.fullmatch(value):
        raise BusError("bus names use lowercase letters, digits, dots, dashes, or underscores")
    return value


class Broker:
    """One durable broker; SQLite transactions serialize enrollment and leases."""

    def __init__(self, state_dir, clock=None):
        self.gateway_shared_secret = os.environ.get("BUS_GATEWAY_SHARED_SECRET")
        if self.gateway_shared_secret is not None and len(self.gateway_shared_secret) < 32:
            raise ValueError("BUS_GATEWAY_SHARED_SECRET must contain at least 32 characters")
        self.admin_readers = frozenset(reader.strip() for reader in os.environ.get("BUS_ADMIN_READERS", "").split(",")
                                      if reader.strip())
        self.root = Path(state_dir).expanduser().absolute()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.root.is_symlink() or self.root.stat().st_uid != os.getuid():
            raise ValueError("bus state directory must be owned by this user, not a symlink")
        os.chmod(self.root, 0o700)
        self.clock = clock or time.time
        self.lock = threading.RLock()
        self.db_path = self.root / "bus.sqlite3"
        token_path = self.root / "admin.token"
        # Publish a completely written file atomically: simultaneous cold
        # starts must never observe an empty token or rotate each other's key.
        if not token_path.exists():
            fd, staging = tempfile.mkstemp(prefix=".admin-", dir=self.root)
            try:
                with os.fdopen(fd, "w") as out:
                    out.write(secrets.token_urlsafe(32) + "\n")
                    out.flush()
                    os.fsync(out.fileno())
                try:
                    os.link(staging, token_path)
                except FileExistsError:
                    pass
            finally:
                os.unlink(staging)
        if token_path.is_symlink() or token_path.stat().st_uid != os.getuid():
            raise ValueError("unsafe bus admin token file")
        os.chmod(token_path, 0o600)
        self.admin_token = token_path.read_text().strip()
        if len(self.admin_token) < 32:
            raise ValueError("invalid bus admin token file")
        if self.db_path.is_symlink():
            raise ValueError("unsafe bus database file")
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS principals(
                  id TEXT PRIMARY KEY, device TEXT NOT NULL, created_at REAL NOT NULL,
                  revoked INTEGER NOT NULL DEFAULT 0, is_admin INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS tokens(
                  digest TEXT PRIMARY KEY, principal TEXT NOT NULL REFERENCES principals(id));
                CREATE TABLE IF NOT EXISTS browser_credentials(
                  digest TEXT PRIMARY KEY REFERENCES tokens(digest) ON DELETE CASCADE,
                  reader TEXT NOT NULL, reader_hash TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS buses(name TEXT PRIMARY KEY, visibility TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS grants(
                  principal TEXT NOT NULL REFERENCES principals(id),
                  bus TEXT NOT NULL REFERENCES buses(name), PRIMARY KEY(principal,bus));
                CREATE TABLE IF NOT EXISTS invites(
                  digest TEXT PRIMARY KEY, bus TEXT NOT NULL REFERENCES buses(name),
                  expires_at REAL NOT NULL, redeemed_at REAL, principal TEXT);
                CREATE TABLE IF NOT EXISTS agents(
                  id TEXT PRIMARY KEY, principal TEXT NOT NULL REFERENCES principals(id),
                  session_key TEXT NOT NULL, name TEXT NOT NULL, kind TEXT NOT NULL,
                  description TEXT NOT NULL, status TEXT NOT NULL, last_seen REAL NOT NULL,
                  UNIQUE(principal,session_key));
                CREATE TABLE IF NOT EXISTS memberships(
                  agent TEXT NOT NULL REFERENCES agents(id),
                  bus TEXT NOT NULL REFERENCES buses(name), PRIMARY KEY(agent,bus));
                CREATE TABLE IF NOT EXISTS messages(
                  id TEXT PRIMARY KEY, sender TEXT NOT NULL REFERENCES agents(id),
                  target TEXT NOT NULL REFERENCES agents(id), bus TEXT NOT NULL,
                  message TEXT NOT NULL, status TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '',
                  created_at REAL NOT NULL, expires_at REAL NOT NULL,
                  lease TEXT, lease_until REAL, updated_at REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS message_delivery ON messages(target,status,created_at);
            """)
            db.execute("INSERT OR IGNORE INTO meta VALUES('server_id',?)", (uuid.uuid4().hex,))
            db.execute("INSERT OR IGNORE INTO principals VALUES('admin','local',?,0,1)", (self.clock(),))
            db.execute("INSERT OR IGNORE INTO buses VALUES('general','open')")
            db.execute("INSERT OR IGNORE INTO tokens VALUES(?,'admin')", (_digest(self.admin_token),))
            self.server_id = db.execute("SELECT value FROM meta WHERE key='server_id'").fetchone()[0]
        db.close()
        os.chmod(self.db_path, 0o600)

    def _connect(self):
        db = sqlite3.connect(str(self.db_path), timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def browser_session(self, reader, reader_hash):
        """Bootstrap only from the HTTP handler's authenticated gateway context."""
        if self.gateway_shared_secret is None:
            raise BusError("browser gateway is not configured", "not_found")
        self._reader_context(reader, reader_hash)
        principal = "web_" + _digest(reader)
        material = json.dumps(["communicate-browser-v1", self.server_id, reader, reader_hash],
                              separators=(",", ":")).encode("utf-8")
        token = "web1." + base64.urlsafe_b64encode(hmac.new(self.admin_token.encode("utf-8"), material,
                                                         hashlib.sha256).digest()).decode("ascii").rstrip("=")
        digest = _digest(token)
        with self.lock:
            db = self._connect()
            try:
                with db:
                    db.execute("BEGIN IMMEDIATE")
                    existing = db.execute("SELECT revoked FROM principals WHERE id=?", (principal,)).fetchone()
                    if existing is not None and existing["revoked"]:
                        raise BusError("browser access was revoked", "forbidden")
                    if existing is None and db.execute("SELECT COUNT(*) FROM principals").fetchone()[0] >= MAX_PRINCIPALS:
                        raise BusError("device enrollment limit reached", "limit")
                    admin = reader in self.admin_readers
                    db.execute("""INSERT INTO principals(id,device,created_at,is_admin) VALUES(?,?,?,?)
                                  ON CONFLICT(id) DO UPDATE SET device=excluded.device,is_admin=excluded.is_admin""",
                               (principal, "browser:" + reader, self.clock(), admin))
                    db.execute("DELETE FROM grants WHERE principal=?", (principal,))
                    db.execute("INSERT INTO grants VALUES(?,'general')", (principal,))
                    # Password changes replace the credential, without changing the reader's identity.
                    db.execute("DELETE FROM tokens WHERE principal=? AND digest<>?", (principal, digest))
                    db.execute("INSERT OR IGNORE INTO tokens VALUES(?,?)", (digest, principal))
                    db.execute("INSERT OR REPLACE INTO browser_credentials VALUES(?,?,?)", (digest, reader, reader_hash))
            finally:
                db.close()
        return {"ok": True, "token": token, "browser_session": True, "logout_url": "/_gateway/logout"}

    @staticmethod
    def _reader_context(reader, reader_hash):
        if (not isinstance(reader, str) or not re.fullmatch(r"[A-Za-z0-9_.@-]{1,128}", reader)
                or not isinstance(reader_hash, str) or not re.fullmatch(r"[a-f0-9]{64}", reader_hash)):
            raise BusError("authenticated browser reader required", "unauthorized")

    def _auth(self, db, token, reader=None, reader_hash=None):
        if not isinstance(token, str) or not 32 <= len(token) <= 512:
            raise BusError("authentication required", "unauthorized")
        p = db.execute("""SELECT p.*,b.reader AS browser_reader,b.reader_hash AS browser_hash
                          FROM principals p JOIN tokens t ON p.id=t.principal
                          LEFT JOIN browser_credentials b ON b.digest=t.digest
                          WHERE t.digest=? AND p.revoked=0""", (_digest(token),)).fetchone()
        if p is None:
            raise BusError("authentication required", "unauthorized")
        p = dict(p)
        if p["browser_reader"] is not None:
            self._reader_context(reader, reader_hash)
            if (self.gateway_shared_secret is None
                    or not secrets.compare_digest(reader, p["browser_reader"])
                    or not secrets.compare_digest(reader_hash, p["browser_hash"])):
                raise BusError("browser reader session does not match", "unauthorized")
            p["is_admin"] = reader in self.admin_readers
            db.execute("UPDATE principals SET is_admin=? WHERE id=?", (p["is_admin"], p["id"]))
            if not p["is_admin"]:
                db.execute("DELETE FROM grants WHERE principal=? AND bus<>'general'", (p["id"],))
        return p

    @staticmethod
    def _admin(p):
        if not p["is_admin"]:
            raise BusError("administrator access required", "forbidden")

    @staticmethod
    def _granted(db, principal, bus):
        return db.execute("""SELECT 1 FROM principals p WHERE p.id=? AND p.revoked=0
          AND (p.is_admin=1 OR EXISTS(SELECT 1 FROM grants g WHERE g.principal=p.id AND g.bus=?))""",
                          (principal, bus)).fetchone() is not None

    def _member(self, db, agent, bus):
        row = db.execute("SELECT principal FROM agents WHERE id=?", (agent,)).fetchone()
        return bool(row and self._granted(db, row[0], bus) and db.execute(
            "SELECT 1 FROM memberships WHERE agent=? AND bus=?", (agent, bus)).fetchone())

    @staticmethod
    def _owned(db, p, agent, admin=False):
        if not isinstance(agent, str):
            raise BusError("unknown or unavailable agent", "not_found")
        row = db.execute("SELECT * FROM agents WHERE id=?", (agent,)).fetchone()
        if row is None or (row["principal"] != p["id"] and not (admin and p["is_admin"])):
            raise BusError("unknown or unavailable agent", "not_found")
        return row

    def _expire(self, db, now):
        db.execute("""UPDATE messages SET status='expired',detail='message expired',updated_at=?
                      WHERE status IN ('accepted','leased') AND expires_at<=?""", (now, now))
        # Retain receipts for seven days, with finite message and invite storage.
        db.execute("DELETE FROM messages WHERE updated_at<? AND status NOT IN ('accepted','leased')",
                   (now - 7 * MESSAGE_TTL,))
        db.execute("DELETE FROM invites WHERE expires_at<?", (now - MESSAGE_TTL,))

    def _cancel_invalid(self, db, now):
        pending = db.execute("SELECT id,sender,target,bus FROM messages WHERE status IN ('accepted','leased')").fetchall()
        for m in pending:
            if not self._member(db, m["sender"], m["bus"]) or not self._member(db, m["target"], m["bus"]):
                db.execute("UPDATE messages SET status='cancelled',detail='membership changed',updated_at=? WHERE id=?",
                           (now, m["id"]))

    def handle(self, token, request, *, reader=None, reader_hash=None):
        """Handle one bounded request, returning an ordinary JSON-safe result."""
        try:
            if not isinstance(request, dict) or not isinstance(request.get("op"), str):
                raise BusError("request must contain an operation")
            if len(json.dumps(request).encode("utf-8")) > MAX_BODY:
                raise BusError("request too large", "too_large")
            with self.lock:
                db = self._connect()
                try:
                    db.execute("BEGIN IMMEDIATE")
                    now = self.clock()
                    p = self._auth(db, token, reader, reader_hash) if token else None
                    if p is not None and p["browser_reader"] is not None and not p["is_admin"] and request["op"] != "snapshot":
                        raise BusError("this browser reader has read-only directory access", "forbidden")
                    if request["op"] == "redeem":
                        result = self._redeem(db, p, token, request, now)
                        self._expire(db, now)
                    else:
                        if p is None:
                            raise BusError("authentication required", "unauthorized")
                        self._expire(db, now)
                        fn = getattr(self, "_op_" + request["op"], None)
                        if fn is None:
                            raise BusError("unknown operation")
                        result = fn(db, p, request, now)
                    db.commit()
                    return {"ok": True, **result}
                except Exception:
                    db.rollback()
                    raise
                finally:
                    db.close()
        except BusError as exc:
            return {"ok": False, "error": str(exc), "code": exc.code}
        except (TypeError, ValueError, OverflowError, RecursionError):
            return {"ok": False, "error": "invalid request", "code": "invalid_request"}

    def _op_create(self, db, p, r, now):
        self._admin(p)
        bus = _bus(r.get("bus"))
        if (not db.execute("SELECT 1 FROM buses WHERE name=?", (bus,)).fetchone()
                and db.execute("SELECT COUNT(*) FROM buses").fetchone()[0] >= MAX_BUSES):
            raise BusError("bus limit reached", "limit")
        db.execute("INSERT OR IGNORE INTO buses VALUES(?,'private')", (bus,))
        return {"bus": bus}

    def _op_invite(self, db, p, r, now):
        self._admin(p)
        bus = _bus(r.get("bus", "general"))
        if not db.execute("SELECT 1 FROM buses WHERE name=?", (bus,)).fetchone():
            raise BusError("unknown bus", "not_found")
        ttl = r.get("ttl", 3600)
        if isinstance(ttl, bool) or not isinstance(ttl, (int, float)) or not 60 <= ttl <= 604800:
            raise BusError("invite ttl must be 60 to 604800 seconds")
        if db.execute("SELECT COUNT(*) FROM invites WHERE redeemed_at IS NULL AND expires_at>?", (now,)).fetchone()[0] >= MAX_INVITES:
            raise BusError("outstanding invitation limit reached", "limit")
        secret = secrets.token_urlsafe(32)
        db.execute("INSERT INTO invites(digest,bus,expires_at) VALUES(?,?,?)",
                   (_digest(secret), bus, now + ttl))
        return {"invite": secret, "bus": bus, "expires_at": now + ttl}

    def _op_invite_revoke(self, db, p, r, now):
        self._admin(p)
        secret = _text(r.get("invite"), "invite", 512)
        invitation = db.execute("SELECT * FROM invites WHERE digest=?", (_digest(secret),)).fetchone()
        if invitation is None or invitation["redeemed_at"] is not None:
            raise BusError("unknown or already redeemed invitation", "not_found")
        db.execute("UPDATE invites SET expires_at=MIN(expires_at,?) WHERE digest=?", (now, _digest(secret)))
        return {"revoked": True, "bus": invitation["bus"]}

    def _redeem(self, db, p, token, r, now):
        secret = _text(r.get("invite"), "invite", 512)
        invite = db.execute("SELECT * FROM invites WHERE digest=? AND redeemed_at IS NULL AND expires_at>?",
                            (_digest(secret), now)).fetchone()
        if invite is None:
            raise BusError("invite is invalid, expired, or already used", "forbidden")
        device = _text(r.get("device", "device"), "device", 128)
        if p is None:
            if db.execute("SELECT COUNT(*) FROM principals").fetchone()[0] >= MAX_PRINCIPALS:
                raise BusError("device enrollment limit reached", "limit")
            principal = "p_" + uuid.uuid4().hex
            token = secrets.token_urlsafe(32)
            db.execute("INSERT INTO principals(id,device,created_at) VALUES(?,?,?)", (principal, device, now))
            db.execute("INSERT INTO tokens VALUES(?,?)", (_digest(token), principal))
        else:
            principal = p["id"]
        db.execute("INSERT OR IGNORE INTO grants VALUES(?,?)", (principal, invite["bus"]))
        db.execute("UPDATE invites SET redeemed_at=?,principal=? WHERE digest=?", (now, principal, _digest(secret)))
        buses = [b[0] for b in db.execute("SELECT bus FROM grants WHERE principal=? ORDER BY bus", (principal,))]
        if p is not None and p["is_admin"]:
            buses = [b[0] for b in db.execute("SELECT name FROM buses ORDER BY name")]
        return {"token": token, "principal": principal, "buses": buses, "server_id": self.server_id}

    def _op_register(self, db, p, r, now):
        bus = _bus(r.get("bus", "general"))
        if not self._granted(db, p["id"], bus) or not db.execute("SELECT 1 FROM buses WHERE name=?", (bus,)).fetchone():
            raise BusError("bus membership has not been granted", "forbidden")
        session_key = _text(r.get("session_key"), "session_key", 256)
        name = _text(r.get("name"), "name", 128)
        kind = r.get("kind", "claude")
        if kind not in ("claude", "codex"):
            raise BusError("kind must be claude or codex")
        description = _text(r.get("description", ""), "description", 2048, optional=True)
        status = r.get("status", "queueable" if kind == "codex" else "live")
        if status not in ("live", "queueable", "offline"):
            raise BusError("status must be live, queueable, or offline")
        existing = db.execute("SELECT id FROM agents WHERE principal=? AND session_key=?", (p["id"], session_key)).fetchone()
        if existing:
            agent = existing[0]
            db.execute("UPDATE agents SET name=?,kind=?,description=?,status=?,last_seen=? WHERE id=?",
                       (name, kind, description, status, now, agent))
        else:
            count = db.execute("SELECT COUNT(*) FROM agents WHERE principal=?", (p["id"],)).fetchone()[0]
            if count >= MAX_AGENTS or db.execute("SELECT COUNT(*) FROM agents").fetchone()[0] >= MAX_TOTAL_AGENTS:
                raise BusError("agent registration limit reached", "limit")
            agent = "a_" + uuid.uuid4().hex
            db.execute("INSERT INTO agents VALUES(?,?,?,?,?,?,?,?)",
                       (agent, p["id"], session_key, name, kind, description, status, now))
        db.execute("INSERT OR IGNORE INTO memberships VALUES(?,?)", (agent, bus))
        buses = [b[0] for b in db.execute("SELECT bus FROM memberships WHERE agent=? ORDER BY bus", (agent,))]
        return {"id": agent, "agent": agent, "name": name, "bus": bus, "buses": buses,
                "principal": p["id"], "status": status, "last_seen": now, "server_id": self.server_id}

    def _op_heartbeat(self, db, p, r, now):
        rows = r.get("agents")
        if not isinstance(rows, list) or len(rows) > MAX_AGENTS:
            raise BusError("agents must be a bounded list")
        for entry in rows:
            if not isinstance(entry, dict) or entry.get("status") not in ("live", "queueable", "offline"):
                raise BusError("invalid heartbeat")
            agent = self._owned(db, p, entry.get("id"))
            db.execute("UPDATE agents SET status=?,last_seen=? WHERE id=?", (entry["status"], now, agent["id"]))
        return {"updated": len(rows), "ts": now}

    def _op_snapshot(self, db, p, r, now):
        allowed = [b[0] for b in db.execute("SELECT name FROM buses ORDER BY name") if self._granted(db, p["id"], b[0])]
        buses = []
        for bus in allowed:
            visibility = db.execute("SELECT visibility FROM buses WHERE name=?", (bus,)).fetchone()[0]
            agents = []
            for a in db.execute("""SELECT a.*,p.device FROM agents a JOIN memberships m ON m.agent=a.id
                                   JOIN principals p ON p.id=a.principal WHERE m.bus=? AND p.revoked=0
                                   ORDER BY a.name,a.id""", (bus,)):
                if not self._granted(db, a["principal"], bus):
                    continue
                memberships = [b[0] for b in db.execute("SELECT bus FROM memberships WHERE agent=? ORDER BY bus", (a["id"],))
                               if b[0] in allowed]
                row = {"id": a["id"], "name": a["name"], "kind": a["kind"], "device": a["device"],
                       "description": a["description"], "last_seen": a["last_seen"], "buses": memberships,
                       "status": a["status"] if now - a["last_seen"] <= LIVE_TTL else "offline"}
                if p["is_admin"]:
                    row["principal"] = a["principal"]
                agents.append(row)
            buses.append({"name": bus, "visibility": visibility, "agents": agents})
        result = {"server_id": self.server_id, "is_admin": bool(p["is_admin"]),
                  "principal": p["id"], "buses": buses, "ts": now}
        if p.get("browser_reader") is not None:
            result.update(browser_session=True, read_only=not p["is_admin"], logout_url="/_gateway/logout")
        if p["is_admin"]:
            principals = []
            for ent in db.execute("""SELECT p.* FROM principals p WHERE NOT EXISTS (
                    SELECT 1 FROM tokens t JOIN browser_credentials b ON b.digest=t.digest
                    WHERE t.principal=p.id) ORDER BY p.created_at,p.id"""):
                row = dict(ent)
                row["is_admin"] = bool(row["is_admin"])
                row["revoked"] = bool(row["revoked"])
                row["buses"] = allowed if ent["is_admin"] else [b[0] for b in db.execute(
                    "SELECT bus FROM grants WHERE principal=? ORDER BY bus", (ent["id"],))]
                principals.append(row)
            result["principals"] = principals
        return result

    def _op_members(self, db, p, r, now):
        self._admin(p)
        return self._op_snapshot(db, p, r, now)

    def _op_send(self, db, p, r, now):
        sender = self._owned(db, p, r.get("sender"))
        bus = _bus(r.get("bus", "general"))
        target = _text(r.get("target"), "target", 128)
        message = r.get("message")
        if not isinstance(message, str) or not message.strip() or len(message.encode("utf-8")) > MAX_MESSAGE or "\x00" in message:
            raise BusError("message must contain 1 to %d UTF-8 bytes" % MAX_MESSAGE)
        if not self._member(db, sender["id"], bus):
            raise BusError("unknown or unavailable recipient", "not_found")
        candidates = db.execute("""SELECT a.id FROM agents a JOIN memberships m ON m.agent=a.id
                                  WHERE m.bus=? AND (a.id=? OR a.name=?) ORDER BY a.id""", (bus, target, target)).fetchall()
        ids = [c[0] for c in candidates if self._member(db, c[0], bus)]
        if target in ids:
            ids = [target]
        if not ids:
            raise BusError("unknown or unavailable recipient", "not_found")
        if len(ids) != 1:
            raise BusError("recipient name is ambiguous; use its agent id", "ambiguous")
        self._cancel_invalid(db, now)
        target = ids[0]
        pending = db.execute("SELECT COUNT(*) FROM messages WHERE target=? AND status IN ('accepted','leased')", (target,)).fetchone()[0]
        if pending >= MAX_PENDING or db.execute("SELECT COUNT(*) FROM messages").fetchone()[0] >= MAX_RECORDS:
            raise BusError("recipient queue is full", "limit")
        mid = "m_" + uuid.uuid4().hex
        db.execute("""INSERT INTO messages(id,sender,target,bus,message,status,created_at,expires_at,updated_at)
                      VALUES(?,?,?,?,?,'accepted',?,?,?)""", (mid, sender["id"], target, bus, message, now, now + MESSAGE_TTL, now))
        return {"id": mid, "status": "accepted", "target": target, "expires_at": now + MESSAGE_TTL}

    def _op_poll(self, db, p, r, now):
        # Leases permit crash recovery, not payload retraction: once fetched,
        # a client already has the message. Revocation blocks future fetches
        # and acknowledgments; it cannot undo a delivery already in progress.
        agent = self._owned(db, p, r.get("agent"))
        self._cancel_invalid(db, now)
        rows = db.execute("""SELECT * FROM messages WHERE target=? AND
                             (status='accepted' OR (status='leased' AND lease_until<=?))
                             ORDER BY created_at,id LIMIT 1""", (agent["id"], now)).fetchall()
        messages = []
        for m in rows:
            lease = secrets.token_urlsafe(18)
            db.execute("UPDATE messages SET status='leased',lease=?,lease_until=?,updated_at=? WHERE id=?",
                       (lease, now + LEASE_TTL, now, m["id"]))
            sender = db.execute("SELECT a.id,a.name,a.kind,p.device FROM agents a JOIN principals p ON a.principal=p.id WHERE a.id=?",
                                (m["sender"],)).fetchone()
            messages.append({"id": m["id"], "sender": dict(sender), "target": m["target"], "bus": m["bus"],
                             "message": m["message"], "created_at": m["created_at"], "expires_at": m["expires_at"], "lease": lease})
        return {"messages": messages, "lease_seconds": LEASE_TTL}

    def _op_ack(self, db, p, r, now):
        agent = self._owned(db, p, r.get("agent"))
        status = r.get("status")
        if status not in ("delivered", "queued", "failed"):
            raise BusError("invalid delivery status")
        detail = _text(r.get("detail", ""), "detail", 1024, optional=True)
        mid = _text(r.get("id"), "message id", 128)
        lease = _text(r.get("lease"), "delivery lease", 128)
        m = db.execute("SELECT * FROM messages WHERE id=? AND target=?", (mid, agent["id"])).fetchone()
        if m is None or not m["lease"] or not secrets.compare_digest(m["lease"], lease):
            raise BusError("unknown or unavailable message", "not_found")
        self._cancel_invalid(db, now)
        m = db.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
        if m["status"] in ("delivered", "queued", "failed") and m["status"] == status:
            return {"id": mid, "status": status}
        if m["status"] != "leased" or m["lease_until"] <= now:
            raise BusError("delivery lease is no longer active", "conflict")
        db.execute("UPDATE messages SET status=?,detail=?,updated_at=? WHERE id=?", (status, detail, now, mid))
        return {"id": mid, "status": status}

    def _op_receipt(self, db, p, r, now):
        mid = _text(r.get("id"), "message id", 128)
        self._cancel_invalid(db, now)
        m = db.execute("""SELECT m.* FROM messages m JOIN agents s ON s.id=m.sender JOIN agents t ON t.id=m.target
                          WHERE m.id=? AND (s.principal=? OR t.principal=?)""", (mid, p["id"], p["id"])).fetchone()
        if m is None:
            raise BusError("unknown or unavailable message", "not_found")
        return {"id": mid, "status": m["status"], "detail": m["detail"],
                "created_at": m["created_at"], "updated_at": m["updated_at"], "expires_at": m["expires_at"]}

    def _op_leave(self, db, p, r, now):
        agent = self._owned(db, p, r.get("agent"), admin=True)
        bus = _bus(r.get("bus", "general"))
        db.execute("DELETE FROM memberships WHERE agent=? AND bus=?", (agent["id"], bus))
        self._cancel_invalid(db, now)
        return {"agent": agent["id"], "bus": bus, "left": True}

    def _op_revoke(self, db, p, r, now):
        self._admin(p)
        principal = _text(r.get("principal"), "principal", 128)
        if db.execute("""SELECT 1 FROM tokens t JOIN browser_credentials b ON b.digest=t.digest
                         WHERE t.principal=?""", (principal,)).fetchone():
            raise BusError("reader access is managed in the gateway roster", "forbidden")
        target = db.execute("SELECT * FROM principals WHERE id=?", (principal,)).fetchone()
        if target is None or target["is_admin"]:
            raise BusError("unknown or non-revocable principal", "not_found")
        db.execute("UPDATE principals SET revoked=1 WHERE id=?", (principal,))
        db.execute("DELETE FROM grants WHERE principal=?", (principal,))
        db.execute("DELETE FROM memberships WHERE agent IN (SELECT id FROM agents WHERE principal=?)", (principal,))
        db.execute("UPDATE agents SET status='offline' WHERE principal=?", (principal,))
        self._cancel_invalid(db, now)
        return {"principal": principal, "revoked": True}


def handler_factory(broker, assets_dir=None):
    """HTTP handler for a loopback server; static UI never receives credentials."""
    asset_root = Path(assets_dir).resolve() if assets_dir else Path(__file__).resolve().parent

    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = "CommunicateBus/1"
        protocol_version = "HTTP/1.0"

        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def parse_request(self):
            if not super().parse_request():
                return False
            self.gateway_context = self._gateway_context()
            return self.gateway_context is not None

        def _gateway_context(self):
            if broker.gateway_shared_secret is None:
                # Browser identity headers are never trusted in ordinary local mode.
                return {}
            secrets_sent = self.headers.get_all("X-Communicate-Bus-Gateway", [])
            if (len(secrets_sent) != 1 or not secrets.compare_digest(
                    secrets_sent[0].encode("utf-8"), broker.gateway_shared_secret.encode("utf-8"))):
                self._respond(401, {"ok": False, "error": "trusted gateway required", "code": "unauthorized"})
                return None
            readers = self.headers.get_all("X-Communicate-Bus-Reader", [])
            hashes = self.headers.get_all("X-Communicate-Bus-Reader-Hash", [])
            if not readers and not hashes:
                return {}
            try:
                if len(readers) != 1 or len(hashes) != 1:
                    raise BusError("authenticated browser reader required", "unauthorized")
                broker._reader_context(readers[0], hashes[0])
            except BusError:
                self._respond(401, {"ok": False, "error": "authenticated browser reader required", "code": "unauthorized"})
                return None
            return {"reader": readers[0], "reader_hash": hashes[0]}

        def log_message(self, fmt, *args):
            # Tokens, invitation codes, and message text never enter access logs.
            return

        def _respond(self, status, data, mime="application/json; charset=utf-8"):
            payload = json.dumps(data).encode("utf-8") if isinstance(data, dict) else data
            script_policy = "'self'"
            if mime.startswith("text/html"):
                scripts = re.findall(rb"<script\b[^>]*>(.*?)</script\s*>", payload, flags=re.DOTALL | re.IGNORECASE)
                script_policy += "".join(" 'sha256-%s'" % base64.b64encode(hashlib.sha256(script).digest()).decode("ascii")
                                         for script in scripts if script.strip())
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src %s; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'" % script_policy)
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/_bus/session":
                if broker.gateway_shared_secret is None:
                    return self._respond(404, {"ok": False, "error": "not found", "code": "not_found"})
                try:
                    result = broker.browser_session(**self.gateway_context) if self.gateway_context else None
                    if result is None:
                        raise BusError("authenticated browser reader required", "unauthorized")
                except BusError as exc:
                    return self._respond(403 if exc.code == "forbidden" else 401,
                                         {"ok": False, "error": str(exc), "code": exc.code})
                return self._respond(200, result)
            if path == "/health":
                return self._respond(200, {"ok": True})
            if path in ("/", "/index.html"):
                candidate = asset_root / "bus_ui.html"
            elif path.startswith("/assets/"):
                candidate = (asset_root / path.lstrip("/")).resolve()
                if asset_root / "assets" not in candidate.parents:
                    return self._respond(404, {"ok": False, "error": "not found"})
            else:
                return self._respond(404, {"ok": False, "error": "not found"})
            if not candidate.is_file() or candidate.stat().st_size > 2 * 1024 * 1024:
                return self._respond(404, {"ok": False, "error": "not found"})
            mime = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                    ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml"}.get(candidate.suffix, "application/octet-stream")
            self._respond(200, candidate.read_bytes(), mime)

        def do_POST(self):
            if self.path != "/v1":
                return self._respond(404, {"ok": False, "error": "not found"})
            origin = self.headers.get("Origin")
            if origin:
                try:
                    parsed = urlsplit(origin)
                    safe_origin = (parsed.scheme in ("http", "https") and parsed.netloc == self.headers.get("Host")
                                   and parsed.path in ("", "/") and not parsed.query and not parsed.fragment)
                except ValueError:
                    safe_origin = False
                if not safe_origin:
                    return self._respond(403, {"ok": False, "error": "cross-origin requests are not allowed", "code": "forbidden"})
            if self.headers.get("Transfer-Encoding") or self.headers.get("Content-Type", "").split(";", 1)[0].strip() != "application/json":
                return self._respond(400, {"ok": False, "error": "application/json body required", "code": "invalid_request"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_BODY:
                    return self._respond(413, {"ok": False, "error": "invalid body size", "code": "too_large"})
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise ValueError("incomplete body")
                request = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeError, OSError, RecursionError):
                return self._respond(400, {"ok": False, "error": "invalid JSON body", "code": "invalid_request"})
            auth = self.headers.get("Authorization", "")
            token = auth[7:] if auth.startswith("Bearer ") else None
            try:
                result = broker.handle(token, request, **self.gateway_context)
            except Exception:
                return self._respond(500, {"ok": False, "error": "broker operation failed", "code": "internal_error"})
            status = 200 if result.get("ok") else {"unauthorized": 401, "forbidden": 403, "not_found": 404,
                "conflict": 409, "ambiguous": 409, "limit": 429, "too_large": 413}.get(result.get("code"), 400)
            self._respond(status, result)

    return Handler


def _check_loopback(host):
    if host != "localhost":
        try:
            if not ipaddress.ip_address(host).is_loopback:
                raise ValueError("bus broker must bind to loopback; use an HTTPS reverse proxy")
        except ValueError:
            raise ValueError("bus broker must bind to loopback; use an HTTPS reverse proxy") from None


class BusHTTPServer(http.server.ThreadingHTTPServer):
    """Bound concurrent requests, including clients that never finish headers."""
    daemon_threads = True
    request_queue_size = 32

    def __init__(self, address, handler, max_connections=32):
        _check_loopback(address[0])
        if ":" in address[0]:
            import socket
            self.address_family = socket.AF_INET6
        self.slots = threading.BoundedSemaphore(max_connections)
        super().__init__(address, handler)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            try:
                request.settimeout(0.1)
                request.sendall(b"HTTP/1.0 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


def serve(broker, host="127.0.0.1", port=0, assets_dir=None):
    """Run the backend on loopback only. TLS and remote exposure are explicit."""
    server = BusHTTPServer((host, port), handler_factory(broker, assets_dir))
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()
