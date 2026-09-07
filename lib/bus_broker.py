#!/usr/bin/env python3
"""Communicate's explicit bus registry and message gateway (stdlib only).

The HTTP listener is loopback-only. Remote clients reach it through a TLS
reverse proxy; no agent socket, shell, or local administration credential is
published by this module. Every data operation authenticates independently.
"""
import base64
import getpass
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
import unicodedata
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
MAX_SSO_REPLAYS = 4096
MAX_VIEW_HEADER = 8192
MESSAGE_TTL = 86400
LIVE_TTL = 45
LEASE_TTL = 60
BUS_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,62}\Z")
USER_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,62}\Z")
DEVICE_FIELDS = {"hostname": 253, "platform": 64, "tailscale_hostname": 253, "tailscale_dns_name": 253}
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


def _user(value):
    if not isinstance(value, str) or not USER_RE.fullmatch(value):
        raise BusError("invalid user handle")
    return value


def _device_metadata(value):
    if not isinstance(value, dict) or any(key not in DEVICE_FIELDS for key in value):
        raise BusError("invalid device metadata")
    return {key: _text(text, key, DEVICE_FIELDS[key]) for key, text in value.items()}


def _unique_object(pairs):
    """Reject duplicate JSON fields at the gateway's authorization boundary."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _canonical_uuid(value, version=None):
    if not isinstance(value, str):
        return False
    try:
        parsed = uuid.UUID(value)
        return str(parsed) == value and (version is None or parsed.version == version)
    except ValueError:
        return False


class Broker:
    """One durable broker; SQLite transactions serialize enrollment and leases."""

    def __init__(self, state_dir, clock=None):
        self.gateway_shared_secret = os.environ.get("BUS_GATEWAY_SHARED_SECRET")
        if self.gateway_shared_secret is not None and len(self.gateway_shared_secret) < 32:
            raise ValueError("BUS_GATEWAY_SHARED_SECRET must contain at least 32 characters")
        self.admin_readers = frozenset(reader.strip() for reader in os.environ.get("BUS_ADMIN_READERS", "").split(",")
                                      if reader.strip())
        self.openwebui_readers = os.environ.get("BUS_OPENWEBUI_READERS") == "1"
        try:
            self.reader_users = json.loads(os.environ.get("BUS_READER_USERS", "{}"))
            if (not isinstance(self.reader_users, dict) or len(self.reader_users) > MAX_PRINCIPALS
                    or any(not isinstance(reader, str) or not re.fullmatch(r"[A-Za-z0-9_.@-]{1,128}", reader)
                           for reader in self.reader_users)):
                raise ValueError()
            for user in self.reader_users.values():
                _user(user)
        except (ValueError, TypeError, BusError):
            raise ValueError("BUS_READER_USERS must map reader logins to valid user handles") from None
        self.users = sorted(set(self.reader_users.values()))
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
                CREATE TABLE IF NOT EXISTS sso_replays(jti TEXT PRIMARY KEY, expires_at REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS sso_replay_expiry ON sso_replays(expires_at);
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
                CREATE TABLE IF NOT EXISTS conversations(
                  id TEXT PRIMARY KEY, initiator TEXT NOT NULL REFERENCES agents(id),
                  published TEXT NOT NULL REFERENCES agents(id), bus TEXT NOT NULL REFERENCES buses(name),
                  expires_at REAL NOT NULL, closed INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS messages(
                  id TEXT PRIMARY KEY, sender TEXT NOT NULL REFERENCES agents(id),
                  target TEXT NOT NULL REFERENCES agents(id), bus TEXT NOT NULL,
                  message TEXT NOT NULL, status TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '',
                  created_at REAL NOT NULL, expires_at REAL NOT NULL,
                  lease TEXT, lease_until REAL, updated_at REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS message_delivery ON messages(target,status,created_at);
            """)
            # Upgrade existing hubs without changing credentials or guessing the
            # owner of an enrolled device from its self-reported display name.
            db.execute("BEGIN IMMEDIATE")
            for table, name, declaration in (("principals", "user", "TEXT"),
                                              ("principals", "device_metadata", "TEXT NOT NULL DEFAULT '{}'"),
                                              ("invites", "user", "TEXT"),
                                              ("messages", "conversation", "TEXT REFERENCES conversations(id)")):
                columns = {row[1] for row in db.execute("PRAGMA table_info(%s)" % table)}
                if name not in columns:
                    db.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, name, declaration))
            # Preserve queued payloads and their original expiry on upgrade,
            # while giving upgraded receivers the same scoped reply operation.
            for message in db.execute("SELECT * FROM messages WHERE conversation IS NULL").fetchall():
                conversation = "c_legacy_" + message["id"]
                db.execute("INSERT OR IGNORE INTO conversations VALUES(?,?,?,?,?,0)",
                           (conversation, message["sender"], message["target"], message["bus"], message["expires_at"]))
                db.execute("UPDATE messages SET conversation=? WHERE id=?", (conversation, message["id"]))
            db.execute("INSERT OR IGNORE INTO meta VALUES('server_id',?)", (uuid.uuid4().hex,))
            db.execute("INSERT OR IGNORE INTO principals(id,device,created_at,is_admin) VALUES('admin','local',?,1)",
                       (self.clock(),))
            local_user = getpass.getuser().lower()
            db.execute("UPDATE principals SET user=COALESCE(user,?) WHERE id='admin'",
                       (local_user if USER_RE.fullmatch(local_user) else "local",))
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

    def browser_session(self, reader, reader_hash, view=None):
        """Bootstrap only from the HTTP handler's authenticated gateway context."""
        if self.gateway_shared_secret is None:
            raise BusError("browser gateway is not configured", "not_found")
        self._reader_context(reader, reader_hash)
        view = self._view_assertion(reader, reader_hash, view, self.clock())
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
                    view = self._view_assertion(reader, reader_hash, view, self.clock())
                    self._existing_view_buses(db, view)
                    existing = db.execute("SELECT revoked FROM principals WHERE id=?", (principal,)).fetchone()
                    if existing is not None and existing["revoked"]:
                        raise BusError("browser access was revoked", "forbidden")
                    if existing is None and db.execute("SELECT COUNT(*) FROM principals").fetchone()[0] >= MAX_PRINCIPALS:
                        raise BusError("device enrollment limit reached", "limit")
                    admin = self._browser_admin(reader)
                    user = self._reader_user(reader)
                    db.execute("""INSERT INTO principals(id,device,created_at,is_admin,user) VALUES(?,?,?,?,?)
                                  ON CONFLICT(id) DO UPDATE SET device=excluded.device,is_admin=excluded.is_admin,user=excluded.user""",
                               (principal, "browser:" + reader, self.clock(), admin, user))
                    db.execute("DELETE FROM grants WHERE principal=?", (principal,))
                    if view is None:
                        db.execute("INSERT INTO grants VALUES(?,'general')", (principal,))
                    # Password changes replace the credential, without changing the reader's identity.
                    db.execute("DELETE FROM tokens WHERE principal=? AND digest<>?", (principal, digest))
                    db.execute("INSERT OR IGNORE INTO tokens VALUES(?,?)", (digest, principal))
                    db.execute("INSERT OR REPLACE INTO browser_credentials VALUES(?,?,?)", (digest, reader, reader_hash))
            finally:
                db.close()
        result = {"ok": True, "token": token, "user": user, "browser_session": True, "logout_url": "/_gateway/logout"}
        if view is not None and "display_name" in view:
            result["display_name"] = view["display_name"]
        return result

    def _browser_admin(self, reader):
        # Even a mistaken admin-reader setting cannot promote an OWUI viewer.
        return not reader.startswith("owui.") and reader in self.admin_readers

    def _reader_user(self, reader):
        if reader.startswith("owui."):
            if not self.openwebui_readers or not _canonical_uuid(reader[5:]):
                raise BusError("OpenWebUI browser reader is not enabled or valid", "forbidden")
            return reader
        if self.reader_users and reader not in self.reader_users:
            raise BusError("reader has no configured bus user", "forbidden")
        return _user(self.reader_users.get(reader, reader.lower()))

    def _view_assertion(self, reader, reader_hash, view, now):
        """Validate request-local directory access, never a device grant."""
        if view is None and not reader.startswith("owui."):
            return None
        try:
            if (not self.openwebui_readers or not reader.startswith("owui.") or
                    not _canonical_uuid(reader[5:]) or not isinstance(view, dict) or
                    set(view) - {"v", "reader", "reader_hash", "iat", "exp", "buses", "display_name"} or
                    len(json.dumps(view).encode("utf-8")) > MAX_VIEW_HEADER or
                    type(view.get("v")) is not int or view["v"] != 1 or
                    view.get("reader") != reader or view.get("reader_hash") != reader_hash or
                    type(view.get("iat")) is not int or type(view.get("exp")) is not int or
                    not 1 <= view["exp"] - view["iat"] <= 60 or view["iat"] > now + 5 or view["exp"] <= now):
                raise ValueError()
            buses = view.get("buses")
            if (not isinstance(buses, list) or len(buses) > 64 or
                    any(not isinstance(bus, str) or not BUS_RE.fullmatch(bus) for bus in buses) or
                    len(set(buses)) != len(buses)):
                raise ValueError()
            if "display_name" in view:
                label = view["display_name"]
                if (not isinstance(label, str) or len(label.encode("utf-8")) > 160 or
                        any(unicodedata.category(char) in ("Cc", "Cf", "Cs") for char in label)):
                    raise ValueError()
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise BusError("valid current browser view assertion required", "unauthorized") from None
        return dict(view, buses=list(buses))

    @staticmethod
    def _existing_view_buses(db, view):
        if view is not None and any(not db.execute("SELECT 1 FROM buses WHERE name=?", (bus,)).fetchone()
                                    for bus in view["buses"]):
            raise BusError("browser view assertion contains an unknown bus", "unauthorized")

    @staticmethod
    def _device_info(principal):
        return {"user": principal["user"], "device": principal["device"], "device_id": principal["id"],
                "device_metadata": json.loads(principal["device_metadata"])}

    @staticmethod
    def _reader_context(reader, reader_hash):
        if (not isinstance(reader, str) or not re.fullmatch(r"[A-Za-z0-9_.@-]{1,128}", reader)
                or not isinstance(reader_hash, str) or not re.fullmatch(r"[a-f0-9]{64}", reader_hash)):
            raise BusError("authenticated browser reader required", "unauthorized")

    def _auth(self, db, token, reader=None, reader_hash=None, view=None):
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
            view = self._view_assertion(reader, reader_hash, view, self.clock())
            self._existing_view_buses(db, view)
            p["is_admin"] = self._browser_admin(reader)
            p["user"] = self._reader_user(reader)
            if view is not None:
                p["view_buses"] = view["buses"]
                if "display_name" in view:
                    p["display_name"] = view["display_name"]
            db.execute("UPDATE principals SET is_admin=?,user=? WHERE id=?", (p["is_admin"], p["user"], p["id"]))
            if view is None and not p["is_admin"]:
                db.execute("DELETE FROM grants WHERE principal=? AND bus<>'general'", (p["id"],))
        return p

    def consume_sso(self, request):
        """Consume a gateway-verified identity handoff once, across restarts."""
        if self.gateway_shared_secret is None:
            raise BusError("SSO replay endpoint is not configured", "not_found")
        now = self.clock()
        if (not isinstance(request, dict) or set(request) != {"jti", "exp"} or
                not _canonical_uuid(request.get("jti"), version=4) or type(request.get("exp")) is not int or
                not now - 30 <= request["exp"] <= now + 90):
            raise BusError("invalid SSO replay request")
        with self.lock:
            db = self._connect()
            try:
                with db:
                    db.execute("BEGIN IMMEDIATE")
                    now = self.clock()
                    if not now - 30 <= request["exp"] <= now + 90:
                        raise BusError("invalid SSO replay request")
                    db.execute("DELETE FROM sso_replays WHERE expires_at < ?", (now,))
                    if db.execute("SELECT 1 FROM sso_replays WHERE jti=?", (request["jti"],)).fetchone():
                        raise BusError("SSO handoff already consumed", "conflict")
                    if db.execute("SELECT COUNT(*) FROM sso_replays").fetchone()[0] >= MAX_SSO_REPLAYS:
                        raise BusError("SSO replay store is full", "limit")
                    db.execute("INSERT INTO sso_replays VALUES(?,?)", (request["jti"], request["exp"] + 30))
            finally:
                db.close()
        return {"ok": True}

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
        db.execute("DELETE FROM conversations WHERE expires_at<? AND NOT EXISTS (SELECT 1 FROM messages WHERE conversation=conversations.id)",
                   (now - 7 * MESSAGE_TTL,))
        db.execute("""DELETE FROM agents WHERE last_seen<?
                      AND NOT EXISTS (SELECT 1 FROM memberships WHERE agent=agents.id)
                      AND NOT EXISTS (SELECT 1 FROM messages WHERE sender=agents.id OR target=agents.id)
                      AND NOT EXISTS (SELECT 1 FROM conversations WHERE initiator=agents.id OR published=agents.id)""",
                   (now - 7 * MESSAGE_TTL,))

    def _conversation_valid(self, db, conversation, now):
        if conversation is None or conversation["closed"] or conversation["expires_at"] <= now:
            return False
        bus = conversation["bus"]
        if not self._member(db, conversation["published"], bus):
            return False
        if bus != "general":
            return self._member(db, conversation["initiator"], bus)
        sender = db.execute("SELECT principal FROM agents WHERE id=?", (conversation["initiator"],)).fetchone()
        return bool(sender and self._granted(db, sender[0], bus))

    def _cancel_invalid(self, db, now):
        # Close permanently on loss of admission/publication; rejoining must
        # never reactivate a previously withdrawn conversation.
        for conversation in db.execute("SELECT * FROM conversations WHERE closed=0").fetchall():
            if not self._conversation_valid(db, conversation, now):
                db.execute("UPDATE conversations SET closed=1 WHERE id=?", (conversation["id"],))
        pending = db.execute("SELECT id,sender,target,bus,conversation FROM messages WHERE status IN ('accepted','leased')").fetchall()
        for m in pending:
            if m["conversation"]:
                conversation = db.execute("SELECT * FROM conversations WHERE id=?", (m["conversation"],)).fetchone()
                valid = self._conversation_valid(db, conversation, now)
            else:
                # Preserve deliveries queued by the pre-conversation broker.
                valid = self._member(db, m["sender"], m["bus"]) and self._member(db, m["target"], m["bus"])
            if not valid:
                db.execute("UPDATE messages SET status='cancelled',detail='membership changed',updated_at=? WHERE id=?",
                           (now, m["id"]))

    def handle(self, token, request, *, reader=None, reader_hash=None, view=None):
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
                    p = self._auth(db, token, reader, reader_hash, view) if token else None
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
        user = r.get("user", None if self.users else p["user"])
        if user is None:
            raise BusError("select the user who will own this device")
        user = _user(user)
        if self.users and user not in self.users:
            raise BusError("unknown invitation user", "forbidden")
        secret = secrets.token_urlsafe(32)
        db.execute("INSERT INTO invites(digest,bus,expires_at,user) VALUES(?,?,?,?)",
                   (_digest(secret), bus, now + ttl, user))
        return {"invite": secret, "bus": bus, "user": user, "expires_at": now + ttl}

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
        if invite["user"] is None and self.users:
            raise BusError("this older invitation has no user; request a new invitation", "forbidden")
        if self.users and invite["user"] not in self.users:
            raise BusError("invitation user is no longer configured", "forbidden")
        device = _text(r.get("device", "device"), "device", 128)
        metadata = _device_metadata(r.get("device_metadata", {}))
        if p is None:
            if db.execute("SELECT COUNT(*) FROM principals").fetchone()[0] >= MAX_PRINCIPALS:
                raise BusError("device enrollment limit reached", "limit")
            principal = "p_" + uuid.uuid4().hex
            token = secrets.token_urlsafe(32)
            db.execute("INSERT INTO principals(id,device,created_at,user,device_metadata) VALUES(?,?,?,?,?)",
                       (principal, device, now, invite["user"], json.dumps(metadata)))
            db.execute("INSERT INTO tokens VALUES(?,?)", (_digest(token), principal))
        else:
            principal = p["id"]
            if p["browser_reader"] is not None:
                raise BusError("enroll a device separately from a browser session", "forbidden")
            if p["user"] is not None and invite["user"] != p["user"]:
                raise BusError("invitation belongs to a different user; use a separate installation", "forbidden")
            db.execute("UPDATE principals SET user=COALESCE(user,?) WHERE id=?", (invite["user"], principal))
            if "device_metadata" in r:
                db.execute("UPDATE principals SET device_metadata=? WHERE id=?", (json.dumps(metadata), principal))
        db.execute("INSERT OR IGNORE INTO grants VALUES(?,?)", (principal, invite["bus"]))
        db.execute("UPDATE invites SET redeemed_at=?,principal=? WHERE digest=?", (now, principal, _digest(secret)))
        buses = [b[0] for b in db.execute("SELECT bus FROM grants WHERE principal=? ORDER BY bus", (principal,))]
        if p is not None and p["is_admin"]:
            buses = [b[0] for b in db.execute("SELECT name FROM buses ORDER BY name")]
        info = self._device_info(db.execute("SELECT * FROM principals WHERE id=?", (principal,)).fetchone())
        return {"token": token, "principal": principal, "buses": buses, "server_id": self.server_id, **info}

    def _op_device(self, db, p, r, now):
        if p["browser_reader"] is not None:
            raise BusError("this operation updates an enrolled device", "forbidden")
        if "user" in r or "owner" in r:
            raise BusError("device ownership is assigned by its invitation", "forbidden")
        if "device" in r:
            db.execute("UPDATE principals SET device=? WHERE id=?", (_text(r["device"], "device", 128), p["id"]))
        if "device_metadata" in r:
            db.execute("UPDATE principals SET device_metadata=? WHERE id=?",
                       (json.dumps(_device_metadata(r["device_metadata"])), p["id"]))
        return {"principal": p["id"], **self._device_info(db.execute("SELECT * FROM principals WHERE id=?", (p["id"],)).fetchone())}

    def _op_register(self, db, p, r, now):
        bus = _bus(r.get("bus", "general"))
        if not self._granted(db, p["id"], bus) or not db.execute("SELECT 1 FROM buses WHERE name=?", (bus,)).fetchone():
            raise BusError("bus membership has not been granted", "forbidden")
        return self._identify(db, p, r, now, publish=bus)

    def _op_identify(self, db, p, r, now):
        # An internal reply adapter is not directory membership. One device
        # credential admits its local agents to outbound general traffic.
        if not self._granted(db, p["id"], "general"):
            raise BusError("device has not been admitted to general", "forbidden")
        return self._identify(db, p, r, now)

    def _identify(self, db, p, r, now, publish=None):
        if p["browser_reader"] is not None:
            raise BusError("agents connect through an enrolled device", "forbidden")
        if "device_metadata" in r:
            db.execute("UPDATE principals SET device_metadata=? WHERE id=?",
                       (json.dumps(_device_metadata(r["device_metadata"])), p["id"]))
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
        if publish:
            db.execute("INSERT OR IGNORE INTO memberships VALUES(?,?)", (agent, publish))
        buses = [b[0] for b in db.execute("SELECT bus FROM memberships WHERE agent=? ORDER BY bus", (agent,))]
        return {"id": agent, "agent": agent, "name": name, "bus": publish, "buses": buses,
                "principal": p["id"], "status": status, "last_seen": now, "server_id": self.server_id,
                **self._device_info(db.execute("SELECT * FROM principals WHERE id=?", (p["id"],)).fetchone())}

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
        allowed = (sorted(p["view_buses"]) if "view_buses" in p else
                   [b[0] for b in db.execute("SELECT name FROM buses ORDER BY name") if self._granted(db, p["id"], b[0])])
        buses = []
        for bus in allowed:
            visibility = db.execute("SELECT visibility FROM buses WHERE name=?", (bus,)).fetchone()[0]
            agents = []
            for a in db.execute("""SELECT a.*,p.device,p.user,p.device_metadata FROM agents a JOIN memberships m ON m.agent=a.id
                                   JOIN principals p ON p.id=a.principal WHERE m.bus=? AND p.revoked=0
                                   ORDER BY a.name,a.id""", (bus,)):
                if not self._granted(db, a["principal"], bus):
                    continue
                memberships = [b[0] for b in db.execute("SELECT bus FROM memberships WHERE agent=? ORDER BY bus", (a["id"],))
                               if b[0] in allowed]
                row = {"id": a["id"], "name": a["name"], "kind": a["kind"], "device": a["device"],
                       "user": a["user"], "device_id": a["principal"], "device_metadata": json.loads(a["device_metadata"]),
                       "description": a["description"], "last_seen": a["last_seen"], "buses": memberships,
                       "status": a["status"] if now - a["last_seen"] <= LIVE_TTL else "offline"}
                if p["is_admin"]:
                    row["principal"] = a["principal"]
                agents.append(row)
            buses.append({"name": bus, "visibility": visibility, "agents": agents})
        result = {"server_id": self.server_id, "is_admin": bool(p["is_admin"]),
                  "principal": p["id"], "buses": buses, "ts": now, **self._device_info(p)}
        if p.get("browser_reader") is not None:
            result.update(browser_session=True, read_only=not p["is_admin"], logout_url="/_gateway/logout")
            if "display_name" in p:
                result["display_name"] = p["display_name"]
        if p["is_admin"]:
            principals = []
            for ent in db.execute("""SELECT p.* FROM principals p WHERE NOT EXISTS (
                    SELECT 1 FROM tokens t JOIN browser_credentials b ON b.digest=t.digest
                    WHERE t.principal=p.id) ORDER BY p.created_at,p.id"""):
                row = dict(ent)
                row.update(self._device_info(ent))
                row["is_admin"] = bool(row["is_admin"])
                row["revoked"] = bool(row["revoked"])
                row["buses"] = allowed if ent["is_admin"] else [b[0] for b in db.execute(
                    "SELECT bus FROM grants WHERE principal=? ORDER BY bus", (ent["id"],))]
                principals.append(row)
            result["principals"] = principals
            if self.users:
                result["users"] = [{"id": user} for user in self.users]
        return result

    def _op_members(self, db, p, r, now):
        self._admin(p)
        return self._op_snapshot(db, p, r, now)

    def _op_send(self, db, p, r, now):
        sender = self._owned(db, p, r.get("sender"))
        bus = _bus(r.get("bus", "general"))
        target = _text(r.get("target"), "target", 128)
        if not self._granted(db, p["id"], bus) or (bus != "general" and not self._member(db, sender["id"], bus)):
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
        if db.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] >= MAX_RECORDS:
            raise BusError("conversation limit reached", "limit")
        conversation = "c_" + uuid.uuid4().hex
        expires_at = now + MESSAGE_TTL
        db.execute("INSERT INTO conversations VALUES(?,?,?,?,?,0)", (conversation, sender["id"], target, bus, expires_at))
        return self._enqueue(db, sender["id"], target, bus, r.get("message"), conversation, expires_at, now)

    def _op_reply(self, db, p, r, now):
        sender = self._owned(db, p, r.get("sender"))
        mid = _text(r.get("id"), "message id", 128)
        # The message ID is not a bearer capability. The authenticated device
        # must own exactly the recipient of this already-fetched message.
        previous = db.execute("SELECT * FROM messages WHERE id=? AND target=?", (mid, sender["id"])).fetchone()
        if previous is None or previous["status"] not in ("leased", "delivered", "queued"):
            raise BusError("unknown or unavailable conversation", "not_found")
        if any(field in r for field in ("target", "bus", "conversation")):
            raise BusError("reply destination and bus are fixed by the original message")
        self._cancel_invalid(db, now)
        conversation = db.execute("SELECT * FROM conversations WHERE id=?", (previous["conversation"],)).fetchone()
        if not self._conversation_valid(db, conversation, now):
            raise BusError("conversation has ended or access changed", "not_found")
        return self._enqueue(db, sender["id"], previous["sender"], previous["bus"], r.get("message"),
                             conversation["id"], conversation["expires_at"], now)

    def _enqueue(self, db, sender, target, bus, message, conversation, expires_at, now):
        if not isinstance(message, str) or not message.strip() or len(message.encode("utf-8")) > MAX_MESSAGE or "\x00" in message:
            raise BusError("message must contain 1 to %d UTF-8 bytes" % MAX_MESSAGE)
        pending = db.execute("SELECT COUNT(*) FROM messages WHERE target=? AND status IN ('accepted','leased')", (target,)).fetchone()[0]
        if pending >= MAX_PENDING or db.execute("SELECT COUNT(*) FROM messages").fetchone()[0] >= MAX_RECORDS:
            raise BusError("recipient queue is full", "limit")
        mid = "m_" + uuid.uuid4().hex
        db.execute("""INSERT INTO messages(id,sender,target,bus,message,status,created_at,expires_at,updated_at,conversation)
                      VALUES(?,?,?,?,?,'accepted',?,?,?,?)""", (mid, sender, target, bus, message, now, expires_at, now, conversation))
        return {"id": mid, "status": "accepted", "target": target, "expires_at": expires_at,
                "conversation_expires_at": expires_at}

    def _op_poll(self, db, p, r, now):
        # Leases permit crash recovery, not payload retraction: once fetched,
        # a client already has the message. Revocation blocks future fetches
        # and acknowledgments; it cannot undo a delivery already in progress.
        agent = self._owned(db, p, r.get("agent"))
        return self._poll(db, [agent["id"]], now, limit=1)

    def _op_poll_device(self, db, p, r, now):
        agents = r.get("agents")
        if not isinstance(agents, list) or not 1 <= len(agents) <= MAX_AGENTS:
            raise BusError("agents must be a nonempty bounded list of agent ids")
        # Validate all ownership before leasing anything; no partial fetch.
        owned = list(dict.fromkeys(self._owned(db, p, agent)["id"] for agent in agents))
        return self._poll(db, owned, now, limit=32)

    def _poll(self, db, agents, now, limit):
        self._cancel_invalid(db, now)
        slots = ",".join("?" for _ in agents)
        # SQLite insertion order survives equal timestamps or a backwards wall
        # clock; neither may let a second message bypass an active target lease.
        rows = db.execute("""SELECT m.* FROM messages m WHERE m.target IN (%s) AND
                             (m.status='accepted' OR (m.status='leased' AND m.lease_until<=?))
                             AND NOT EXISTS (SELECT 1 FROM messages earlier WHERE earlier.target=m.target
                               AND earlier.status IN ('accepted','leased') AND
                               earlier.rowid<m.rowid)
                             ORDER BY m.rowid LIMIT ?""" % slots, (*agents, now, limit)).fetchall()
        messages = []
        for m in rows:
            lease = secrets.token_urlsafe(18)
            db.execute("UPDATE messages SET status='leased',lease=?,lease_until=?,updated_at=? WHERE id=?",
                       (lease, now + LEASE_TTL, now, m["id"]))
            sender = db.execute("SELECT a.id,a.name,a.kind,p.device,p.user,p.id AS device_id FROM agents a JOIN principals p ON a.principal=p.id WHERE a.id=?",
                                (m["sender"],)).fetchone()
            messages.append({"id": m["id"], "sender": dict(sender), "target": m["target"], "bus": m["bus"],
                             "message": m["message"], "created_at": m["created_at"], "expires_at": m["expires_at"], "lease": lease,
                             "reply_to": m["id"], "conversation_expires_at": m["expires_at"]})
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
        db.execute("UPDATE conversations SET closed=1 WHERE bus=? AND (initiator=? OR published=?)",
                   (bus, agent["id"], agent["id"]))
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
            if self.path == "/_bus/sso/consume":
                if any(self.headers.get_all(name) is not None for name in (
                        "X-Communicate-Bus-Reader", "X-Communicate-Bus-Reader-Hash", "X-Communicate-Bus-View",
                        "Authorization", "Cookie")):
                    self._respond(403, {"ok": False, "error": "SSO consume requires gateway-only context", "code": "forbidden"})
                    return None
                return {}
            readers = self.headers.get_all("X-Communicate-Bus-Reader", [])
            hashes = self.headers.get_all("X-Communicate-Bus-Reader-Hash", [])
            views = self.headers.get_all("X-Communicate-Bus-View", [])
            if not readers and not hashes and not views:
                return {}
            try:
                if len(readers) != 1 or len(hashes) != 1 or len(views) > 1:
                    raise BusError("authenticated browser reader required", "unauthorized")
                broker._reader_context(readers[0], hashes[0])
                view = None
                if views:
                    if len(views[0].encode("utf-8")) > MAX_VIEW_HEADER:
                        raise ValueError("oversized view assertion")
                    view = json.loads(views[0], object_pairs_hook=_unique_object)
                    # A JSON null is not an absent assertion.
                    if not isinstance(view, dict):
                        raise ValueError("invalid view assertion")
                view = broker._view_assertion(readers[0], hashes[0], view, broker.clock())
            except (BusError, ValueError, UnicodeError, RecursionError):
                self._respond(401, {"ok": False, "error": "authenticated browser reader and view required", "code": "unauthorized"})
                return None
            context = {"reader": readers[0], "reader_hash": hashes[0]}
            if view is not None:
                context["view"] = view
            return context

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
            consume = self.path == "/_bus/sso/consume"
            if self.path != "/v1" and not consume:
                return self._respond(404, {"ok": False, "error": "not found"})
            if consume and broker.gateway_shared_secret is None:
                return self._respond(404, {"ok": False, "error": "not found", "code": "not_found"})
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
                if len(self.headers.get_all("Content-Length", [])) != 1 or len(self.headers.get_all("Content-Type", [])) != 1:
                    raise ValueError("ambiguous body headers")
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_BODY:
                    return self._respond(413, {"ok": False, "error": "invalid body size", "code": "too_large"})
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise ValueError("incomplete body")
                request = json.loads(raw.decode("utf-8"), **({"object_pairs_hook": _unique_object} if consume else {}))
            except (ValueError, UnicodeError, OSError, RecursionError):
                return self._respond(400, {"ok": False, "error": "invalid JSON body", "code": "invalid_request"})
            auth = self.headers.get("Authorization", "")
            token = auth[7:] if auth.startswith("Bearer ") else None
            try:
                result = broker.consume_sso(request) if consume else broker.handle(token, request, **self.gateway_context)
            except BusError as exc:
                result = {"ok": False, "error": str(exc), "code": exc.code}
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
