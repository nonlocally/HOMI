#!/usr/bin/env python3
"""Daemon-side unit tests for the branches a live daemon will not reproduce on
demand: _do_restart's ORDER and its refusal to kill what it has not measured as
its own (tmux happily falls back to a default cwd when the recorded one is
gone, so a real tmux cannot be made to fail a spawn); _call against a daemon
that goes quiet; and the check-then-write race in _do_describe."""
import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))
import homi  # noqa: E402

pass_ = [0]
fail_ = [0]


def ok(m):
    pass_[0] += 1
    print("ok   " + m)


def bad(m):
    fail_[0] += 1
    print("FAIL " + m)


class Rig(homi.Homi):
    """A Homi with only what _do_restart touches — no sockets, no daemon."""

    def __init__(self, identities, spawn_ok=True, surfaces=None, kill_ok=True):
        self.mu = threading.RLock()
        self.identities = identities
        self.surfaces = surfaces or {}
        self.spawn_ok = spawn_ok
        self.kill_ok = kill_ok
        self.killed = []
        self.spawned = []
        self.persisted = 0

    def log(self, *a):
        pass

    def _persist_identities(self):
        self.persisted += 1

    def _measure_surface(self, seat):
        return self.surfaces.get(seat)

    def _do_seat(self, sub, req):
        if sub == "spawn":
            self.spawned.append(req)
            if not self.spawn_ok:
                return {"ok": False, "err": "no such directory"}
            return {"ok": True, "seat": "%99"}
        if sub == "kill":
            self.killed.append(req.get("seat"))
            if not self.kill_ok:
                return {"ok": False, "err": "device not linked"}
            return {"ok": True}
        return {"ok": False, "err": "unexpected op %s" % sub}

    def _do_seat_bind(self, seat, name):
        with self.mu:
            self.identities[name]["seat"] = seat
        return {"ok": True}


def ident(seat, cwd="/tmp", spawned_at=100.0):
    return {"kind": "local", "seat": seat,
            "supervision": {"cmd": "bash", "cli": None, "cwd": cwd,
                            "spawned_at": spawned_at}}


def live(seat):
    return {"driver": "tmux", "handle": seat, "state": "idle",
            "measured_at": 1.0}


# 1. A failed respawn must leave the agent with the surface it had. Kill-first
#    would have left it with no surface at all AND ok:false.
r = Rig({"a": ident("%1")}, spawn_ok=False, surfaces={"%1": live("%1")})
res = r._do_restart("a")
if not res.get("ok") and not r.killed and r.identities["a"]["seat"] == "%1":
    ok("a failed respawn kills nothing and keeps the original seat")
else:
    bad("failed respawn: res=%s killed=%s seat=%s"
        % (res, r.killed, r.identities["a"]["seat"]))

# 2. The happy path: spawn, bind, THEN kill the old seat; spawned_at refreshed.
r = Rig({"a": ident("%1")}, surfaces={"%1": live("%1")})
res = r._do_restart("a")
if (res.get("ok") and r.killed == ["%1"] and r.identities["a"]["seat"] == "%99"
        and r.identities["a"]["supervision"]["spawned_at"] > 100.0):
    ok("a successful restart binds the new seat, retires the old, restamps spawned_at")
else:
    bad("happy path: res=%s killed=%s ident=%s" % (res, r.killed, r.identities["a"]))

# 3. A seat measured DEAD is not killed — there is nothing there, and the id may
#    now name somebody else's pane (tmux restarts ids at %0 with its server).
r = Rig({"a": ident("%1")},
        surfaces={"%1": {"driver": "tmux", "handle": "%1", "state": "dead",
                         "measured_at": 1.0}})
res = r._do_restart("a")
if res.get("ok") and not r.killed:
    ok("a dead seat is not killed (nothing to kill, and the id may be recycled)")
else:
    bad("dead seat: res=%s killed=%s" % (res, r.killed))

# 4. UNKNOWN is not a licence to kill: an unmeasurable seat (tmux timed out) is
#    left alone. Never act on what you have not measured.
r = Rig({"a": ident("%1")},
        surfaces={"%1": {"driver": "tmux", "handle": "%1", "state": "unknown",
                         "measured_at": 1.0}})
res = r._do_restart("a")
if res.get("ok") and not r.killed:
    ok("an unmeasurable (unknown) seat is not killed")
else:
    bad("unknown seat: res=%s killed=%s" % (res, r.killed))

# 5. The recycled-pane case: the stored id is live, but another identity is
#    bound to it now. Killing it would take out a different agent.
r = Rig({"a": ident("%1"), "b": ident("%1")}, surfaces={"%1": live("%1")})
res = r._do_restart("a")
if res.get("ok") and not r.killed and r.identities["b"]["seat"] == "%1":
    ok("a seat another identity is bound to is never killed")
else:
    bad("recycled seat: res=%s killed=%s" % (res, r.killed))

# 6. No supervision record => nothing to restart, and nothing killed.
r = Rig({"a": {"kind": "local", "seat": "%1"}}, surfaces={"%1": live("%1")})
res = r._do_restart("a")
if not res.get("ok") and not r.killed and not r.spawned:
    ok("no supervision record: refuses without touching the seat")
else:
    bad("no supervision: res=%s killed=%s spawned=%s" % (res, r.killed, r.spawned))

# 7. previous_killed must report what _do_seat("kill", ...) actually did, not
#    whether the call raised. _do_seat returns {"ok": False, ...} on failure
#    (e.g. a device-qualified seat that is no longer linked) rather than
#    raising, so a bare try/except around the call cannot see the failure.
#    A failed retirement of the OLD seat must not fail a restart whose NEW
#    seat came up fine -- but it must not be reported as a kill that happened.
r = Rig({"a": ident("%1")}, surfaces={"%1": live("%1")}, kill_ok=False)
res = r._do_restart("a")
if res.get("ok") and r.killed == ["%1"] and res.get("previous_killed") is False:
    ok("a failed kill is reported as previous_killed=False, restart still ok")
else:
    bad("failed kill: res=%s killed=%s" % (res, r.killed))

# 8. _do_describe must write through the entry it validated. Taking self.mu
#    twice let a concurrent release land between the check and the write, and
#    the second lookup raised KeyError — which reached the agent as
#    {"ok": false, "err": "'name'"}. The release is staged here by having the
#    clock tick pull the identity out mid-call, at exactly that window.
class Describer(homi.Homi):
    def __init__(self):
        self.mu = threading.Lock()
        self.identities = {"a": {"kind": "local", "card": None}}
        self.persisted = 0

    def log(self, *a):
        pass

    def _persist_identities(self):
        self.persisted += 1


d = Describer()
_real_time = homi.time.time


def _release_mid_call():
    homi.time.time = _real_time          # only the first tick releases
    d.identities.pop("a", None)          # a concurrent `homi release a`
    return _real_time()


homi.time.time = _release_mid_call
try:
    res = d._do_describe("a", what="I prove things")
finally:
    homi.time.time = _real_time
if res.get("ok") and (res.get("card") or {}).get("what") == "I prove things":
    ok("describe writes through the entry it checked (no KeyError on a race)")
else:
    bad("describe raced with release: %s" % (res,))

# 9. _call must not die with a traceback when the daemon accepts the connection
#    and then says nothing (a large fleet, or one unresponsive pane, now that
#    `agents`/`status` measure every seat through tmux).
import shutil          # noqa: E402
import socket          # noqa: E402
import tempfile        # noqa: E402

tmp = tempfile.mkdtemp(prefix="homi-call-")
try:
    root = os.path.join(tmp, "homi")
    os.makedirs(root)
    os.environ["COMM_STATE"] = tmp
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(os.path.join(root, "homi.sock"))
    srv.listen(4)
    res = homi._call({"op": "status"}, timeout=0.4)
    if isinstance(res, dict) and res.get("ok") is False and res.get("err"):
        ok("a silent daemon returns an error dict: %s" % res["err"])
    else:
        bad("silent daemon produced %r" % (res,))
    srv.close()
finally:
    os.environ.pop("COMM_STATE", None)
    shutil.rmtree(tmp, ignore_errors=True)

print("\npass=%d fail=%d" % (pass_[0], fail_[0]))
sys.exit(1 if fail_[0] else 0)
