"""The device bridge: reach a phone from the board.

This is the narrowest thing that can work, because of what it is: an HTTP
request turning into a command on someone's real personal device. Three
boundaries, all enforced here rather than at the call site:

  which device   only names already in the fabric's roster, and only names
                 that are shaped like device names — never anything that
                 could become an ssh flag or a shell fragment.
  which verb     a small allowlist of READ and TOUCH verbs. Deciding verbs
                 (`msg`, `voice`) are deliberately absent: the cockpit is for
                 seeing and touching, the agent is for deciding.
  which args     each verb declares the shape of its own arguments.

Nothing is ever assembled into a shell string. `ssh` is exec'd with a list,
so a hostile argument is an argument, not a command.
"""
import re
import shlex
import subprocess
import sys

# Read verbs and touch verbs. `msg` and `voice` are NOT here on purpose —
# they are how an agent decides to act, and that belongs to the agent's own
# reviewed path (and its ledger), not to an HTTP endpoint.
VERBS = {
    "notifs": ("--limit", "--app", "--since", "--json"),
    "screen": ("--out",),
    "log": ("--limit", "--kind", "--json"),
    "ui": ("--grep",),
    "find": ("--id",),
    "tap": (),
    "key": (),
    "type": (),
    "foreground": (),
    "battery": (),
}

_DEVICE = re.compile(r"\A[a-z0-9][a-z0-9._-]{0,62}\Z")
_SAFE_ARG = re.compile(r"\A[A-Za-z0-9 ._:@,+/-]{0,200}\Z")
_INT = re.compile(r"\A-?\d{1,6}\Z")
_KEYCODE = re.compile(r"\A(KEYCODE_)?[A-Z0-9_]{1,32}\Z")


def valid_device(name):
    """A device name must look like a device name.

    The danger is not only shell metacharacters: ssh reads a leading '-' as a
    flag, and `-oProxyCommand=...` turns a hostname into arbitrary execution.
    So the name must start with an alphanumeric, and may then contain only
    dot, dash, underscore.
    """
    if not isinstance(name, str) or not name:
        return False
    return bool(_DEVICE.match(name))


def known(name, roster):
    """Reachable only if it is a device this fabric already knows."""
    return valid_device(name) and name in set(roster or ())


def allowed(verb):
    return verb in VERBS


def _check_args(verb, args):
    args = [str(a) for a in (args or [])]
    if verb == "tap":
        if len(args) != 2 or not all(_INT.match(a) for a in args):
            raise ValueError("tap takes exactly two integers")
        return args
    if verb == "key":
        if len(args) != 1 or not _KEYCODE.match(args[0]):
            raise ValueError("key takes one keycode")
        return args
    for a in args:
        if not _SAFE_ARG.match(a):
            raise ValueError("unsafe argument: %r" % a)
        # "-" is the stdout convention, not an option — `screen --out -`
        # streams the picture rather than leaving it on the device's disk.
        if a == "-":
            continue
        if a.startswith("-") and a not in VERBS[verb] and not _INT.match(a):
            raise ValueError("unexpected option: %r" % a)
    return args


def build(device, verb, args=None, user="aadarwal", timeout=30):
    """The exact argv to run.

    THE REMOTE COMMAND IS ONE PRE-QUOTED STRING, and that is the whole point.
    Passing ssh several trailing words looks safe — it is a Python list, no
    shell locally — but ssh JOINS them with spaces and the far side's login
    shell re-splits the result. Local argv boundaries do not survive the hop.
    A value like `5 --from /etc/passwd` therefore arrived on the phone as a
    separate flag, turning a read-only verb into arbitrary file read through
    the HTTP bridge. Quoting each word here, and handing ssh a single
    argument, is what actually preserves the boundaries.
    """
    if not valid_device(device):
        raise ValueError("bad device name")
    if not allowed(verb):
        raise ValueError("verb not exposed over the bridge")
    args = _check_args(verb, args)
    remote = " ".join(shlex.quote(w) for w in (["phone", verb] + args))
    return [
        "ssh",
        "-o", "BatchMode=yes",              # never prompt, never hang
        "-o", "ConnectTimeout=8",
        "-o", "StrictHostKeyChecking=accept-new",
        "%s@%s" % (user, device),
        remote,
    ]


def run(device, verb, args=None, user="aadarwal", timeout=30, binary=False):
    """Run a bridged verb. Returns (ok, out). Never raises on device trouble —
    a phone that is asleep, off-network, or has lost adb is the normal case,
    not an exception."""
    try:
        argv = build(device, verb, args, user=user)
    except ValueError as e:
        return False, str(e)
    try:
        r = subprocess.run(argv, capture_output=True, timeout=timeout,
                           text=not binary)
    except subprocess.TimeoutExpired:
        return False, "device did not answer in time"
    except OSError as e:
        return False, "could not reach the device (%s)" % e
    err = ""
    if r.stderr:
        try:
            err = (r.stderr.decode("utf-8", "replace") if binary
                   else r.stderr).strip()
        except Exception:
            err = ""
    if r.returncode != 0:
        return False, (err or "the device refused (exit %d)" % r.returncode)
    # A command can SUCCEED and still have said something that matters —
    # most importantly "could not write the action ledger". Discarding
    # stderr on success silently defeated exactly the warning the ledger
    # exists to raise, but only on the HTTP path. Surface it.
    if err:
        sys.stderr.write("device %s %s: %s\n" % (device, verb, err))
        sys.stderr.flush()
    return True, r.stdout
