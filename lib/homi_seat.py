#!/usr/bin/env python3
"""homi_seat — the seat plane: drive interactive surfaces (tmux panes) as a
first-class peer of the message plane.

A *seat* is an occupied, drivable surface — a tmux pane running a shell, an
agent CLI, or an ssh session into a cluster. Seats are the explicit escape
hatch for things you cannot mailbox (HPC login nodes, REPLs, TUIs). This module
is a CLEAN-ROOM reimplementation of the anu `pane` bin's two hard-won
disciplines, ported from the behavioral spec in docs/studies/, not its code:

  * the state classifier — dead > approval > busy > booting > idle, using the
    signals that actually survive answer-streaming (the CLI title spinner
    animates while working; claude reports its version as pane_current_command,
    codex reports "codex"; the idle footer and the "esc to interrupt" hint);
  * the send discipline — sanitize, stage literally with `send-keys -l`, submit
    with a SEPARATE retried Enter (a welded C-m gets absorbed into a paste and
    never submits; re-sending the TEXT could duplicate a submitted prompt, so we
    never do), and verify by the composer's `[Pasted Content` marker, returning
    an explicit "unconfirmed" instead of lying about delivery.

tmux is the source of truth for a seat's existence and screen; the daemon keeps
only light metadata (spawn cmd, bound identity). All state is queried live.
Stdlib only. Works against the ambient tmux server, or a throwaway one via
$HOMI_TMUX_SOCKET (tests).
"""
import os
import re
import subprocess
import time

# Jittered sampling intervals defeat a fixed-period spinner aliasing to "static".
_JITTER = (0.11, 0.17, 0.23)
_SANITIZE = {ord(c): None for c in
             "".join(chr(i) for i in range(0, 32)) + chr(127)}
_PASTE_MARKER = "[Pasted Content"
# An agent CLI reports either "codex" or its own version string as the command.
_VERSION_CMD = re.compile(r"^\d+\.\d+")
_SHELLS = {"bash", "zsh", "sh", "fish", "dash"}
# Transport commands whose pane_current_command stays constant whether the far
# end is idle or working — for these, fall back to screen-change like a shell.
_TRANSPORTS = {"ssh", "mosh", "et", "sshpass", "sftp"}
# Busy: the interrupt hint the CLI shows while it works (may be overwritten
# by streamed text mid-answer — hence the animation check as a second signal).
_BUSY_HINT = re.compile(r"esc to interrupt|to interrupt\)|interrupt\b", re.I)
# Approval: a permission ASK and a confirm/choice AFFORDANCE must BOTH be
# present (a lone "yes" in prose is not an approval prompt).
_PERM_ASK = re.compile(
    r"do you want|would you like|allow this|proceed\?|permission to|"
    r"grant access|approve|confirm|\(y/n\)|\[y/n\]", re.I)
_AFFORD = re.compile(r"❯\s*\d|›\s*\d|^\s*\d[.)]\s|\(y/n\)|\[y/n\]|\by/n\b", re.M)
# Secret shapes to mask on read unless --raw.
_SECRETS = [
    # Distinctive credential prefixes — matched anywhere (a leading word char,
    # e.g. `tok_sk-…`, must not defeat masking).
    re.compile(r"(sk-[A-Za-z0-9_-]{12,}|ghp_[A-Za-z0-9]{20,}|"
               r"xox[baprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{12,})"),
    re.compile(r"(?i)(bearer|authorization:|token=|api[_-]?key=?)\s*"
               r"([A-Za-z0-9._~+/=-]{12,})"),
]


class SeatError(Exception):
    pass


class SeatDriver:
    def __init__(self, session=None, log=None):
        self.session = session or os.environ.get("HOMI_SEAT_SESSION", "homi-seats")
        self.log = log or (lambda *a: None)
        # A dedicated named tmux server ("homi") by default, so seats are stable
        # regardless of the daemon's TMPDIR under launchd; HOMI_TMUX_SOCKET="" opts
        # into the ambient server (visible in the user's own tmux).
        sock = os.environ.get("HOMI_TMUX_SOCKET")
        if sock is None:
            sock = "homi"
        self._base = ["tmux"] + (["-L", sock] if sock else [])

    # -- tmux plumbing ---------------------------------------------------------
    def _tmux(self, *args, check=False, timeout=10):
        try:
            r = subprocess.run(self._base + list(args), capture_output=True,
                               text=True, timeout=timeout)
        except (subprocess.TimeoutExpired, OSError) as e:
            raise SeatError("tmux failed: %s" % e)
        if check and r.returncode != 0:
            raise SeatError((r.stderr or "tmux error").strip())
        return r

    def available(self):
        return self._tmux("has-server").returncode == 0 or True  # -L may need init

    def _pane_exists(self, seat):
        r = self._tmux("display", "-p", "-t", seat, "#{pane_id}")
        return r.returncode == 0 and r.stdout.strip() == seat

    def _field(self, seat, fmt):
        r = self._tmux("display", "-p", "-t", seat, fmt)
        return r.stdout.rstrip("\n") if r.returncode == 0 else ""

    def _ensure_session(self):
        if self._tmux("has-session", "-t", self.session).returncode != 0:
            self._tmux("new-session", "-d", "-s", self.session,
                       "-x", "220", "-y", "50", check=True)

    # -- capture ---------------------------------------------------------------
    def _capture(self, seat, lines=None):
        r = self._tmux("capture-pane", "-p", "-t", seat)
        if r.returncode != 0:
            return []
        out = r.stdout.split("\n")
        while out and out[-1] == "":
            out.pop()
        return out[-lines:] if lines else out

    @staticmethod
    def _redact(text):
        for rx in _SECRETS:
            text = rx.sub(lambda m: m.group(0)[:4] + "…REDACTED", text)
        return text

    # -- classifier: dead > approval > busy > booting > idle -------------------
    def state(self, seat):
        if not self._pane_exists(seat):
            return "dead"
        cmd = self._field(seat, "#{pane_current_command}")
        bottom = "\n".join(self._capture(seat, 14))
        if _PERM_ASK.search(bottom) and _AFFORD.search(bottom):
            return "approval"
        is_agent = (cmd == "codex" or bool(_VERSION_CMD.match(cmd))
                    or cmd in ("node", "claude"))
        if _BUSY_HINT.search(bottom):
            return "busy"
        if is_agent:
            # Agent CLI: the title spinner is the one signal that survives
            # answer-streaming. Animation => busy; frozen frame => idle.
            if self._title_animates(seat):
                return "busy"
            if not self._has_idle_footer(bottom) and self._is_booting(seat):
                return "booting"
            return "idle"
        # A shell/transport seat: pane_current_command tells us directly whether
        # a foreground process is running (bash → the shell is idle; `sleep`,
        # `make`, a build → busy). ssh/mosh keep a constant command whether the
        # far end is idle or working, so for those fall back to screen-change.
        if cmd in _SHELLS or cmd in _TRANSPORTS or cmd in ("", "tmux"):
            return "busy" if self._screen_changing(seat) else "idle"
        return "busy"  # some foreground process holds the pane

    def _title(self, seat):
        return self._field(seat, "#{pane_title}")

    def _title_animates(self, seat):
        """Two jittered samples of the leading title glyph; a change => the
        spinner is animating => busy. Claude freezes the last frame at idle, so
        a single sample cannot tell busy-frozen from idle — animation can."""
        first = self._title(seat)[:4]
        for j in _JITTER[:2]:
            time.sleep(j)
            if self._title(seat)[:4] != first:
                return True
        return False

    def _screen_changing(self, seat):
        a = "\n".join(self._capture(seat, 6))
        time.sleep(_JITTER[1])
        return "\n".join(self._capture(seat, 6)) != a

    @staticmethod
    def _has_idle_footer(bottom):
        return ("bypass permissions" in bottom or "new task?" in bottom
                or "shift+tab to cycle" in bottom)

    def _is_booting(self, seat):
        # Very young pane with no prompt/footer yet, or a resume/startup banner.
        head = "\n".join(self._capture(seat, 20))
        return ("Welcome to" in head or "· resume" in self._title(seat)
                or "Choose" in head and "resume" in head.lower())

    # -- send discipline -------------------------------------------------------
    def send(self, seat, text):
        if not self._pane_exists(seat):
            raise SeatError("no such seat: %s" % seat)
        # Flatten newlines/tabs to spaces and strip control bytes, so the
        # payload cannot drive the target's terminal.
        msg = text.replace("\n", " ").replace("\r", " ").replace("\t", " ")
        msg = msg.translate(_SANITIZE)
        # exit copy-mode if the pane is scrolled back
        if self._field(seat, "#{pane_in_mode}") == "1":
            self._tmux("send-keys", "-t", seat, "-X", "cancel")
        self._tmux("send-keys", "-t", seat, "-l", "--", msg, check=True)
        time.sleep(0.3)
        # Submit with a SEPARATE Enter, retried; break as soon as the composer's
        # paste marker clears. Never re-send the text (would duplicate a prompt).
        for attempt in range(4):
            self._tmux("send-keys", "-t", seat, "Enter")
            time.sleep(0.4)
            if not self._has_paste_marker(seat):
                return {"ok": True, "sent": True}
        # Still staged: report unconfirmed rather than claim success.
        return {"ok": False, "sent": True, "confirmed": False,
                "err": "staged but not submitted (composer still holds a paste)"}

    def _has_paste_marker(self, seat):
        return any(_PASTE_MARKER in ln for ln in self._capture(seat, 10))

    # -- spawn / read / wait / respond / interrupt -----------------------------
    def spawn(self, cmd, cwd=None, window_name=None):
        self._ensure_session()
        args = ["new-window", "-t", self.session, "-P", "-F", "#{pane_id}"]
        if cwd:
            args += ["-c", cwd]
        if window_name:
            args += ["-n", window_name]
        args += [cmd]  # the command the window runs
        r = self._tmux(*args, check=True)
        seat = r.stdout.strip()
        return {"ok": True, "seat": seat}

    def read(self, seat, lines=40, raw=False):
        if not self._pane_exists(seat):
            raise SeatError("no such seat: %s" % seat)
        text = "\n".join(self._capture(seat, lines))
        return {"ok": True, "seat": seat, "state": self.state(seat),
                "screen": text if raw else self._redact(text)}

    def wait(self, seat, timeout=120):
        """Block until the seat settles to idle. Mirrors the anu turn loop: no
        clock pressure while busy; only after we've SEEN busy does a 2-tick
        quiet+not-busy settle end the wait (so a prompt echo right after a send
        isn't mistaken for completion). If never busy within a start window,
        return the current state honestly."""
        t0 = time.time()
        sawbusy = False
        quiet = 0
        startwin = 20
        while time.time() - t0 < timeout:
            st = self.state(seat)
            if st in ("dead", "approval"):
                return {"ok": True, "state": st, "seat": seat,
                        "tail": "\n".join(self._capture(seat, 12))}
            if st == "busy":
                sawbusy, quiet = True, 0
                time.sleep(0.6)
                continue
            # idle/booting
            if not sawbusy and time.time() - t0 > startwin:
                return {"ok": True, "state": st, "seat": seat, "sawbusy": False,
                        "tail": "\n".join(self._capture(seat, 12))}
            quiet += 1
            if sawbusy and quiet >= 2:
                return {"ok": True, "state": "idle", "seat": seat, "sawbusy": True,
                        "tail": "\n".join(self._capture(seat, 12))}
            time.sleep(0.5)
        return {"ok": False, "state": self.state(seat), "seat": seat,
                "err": "timeout after %ss" % timeout,
                "tail": "\n".join(self._capture(seat, 12))}

    def respond(self, seat, decision="allow"):
        """Answer a TUI approval — FAIL CLOSED. Refuse unless the classifier
        actually sees an approval prompt; never blindly send keys into a pane.
        Picks an affirmative that is NOT an 'always/don't-ask' variant."""
        st = self.state(seat)
        if st != "approval":
            return {"ok": False, "err": "no approval prompt (state=%s)" % st}
        bottom = self._capture(seat, 16)
        if decision in ("deny", "no", "reject"):
            # Prefer an explicit deny/no option; else send 'n'.
            self._tmux("send-keys", "-t", seat, "-l", "--", "n")
            self._tmux("send-keys", "-t", seat, "Enter")
            return {"ok": True, "responded": "deny"}
        # allow: choose the first numbered option that is a plain yes (not
        # "always"/"don't ask"). If the menu is numbered, send that digit; if
        # it's a (y/n), send 'y'. Otherwise accept the highlighted default.
        choice = None
        for ln in bottom:
            m = re.search(r"[❯›]?\s*(\d)[.)]\s*(.+)", ln)
            if m and not re.search(r"always|don'?t ask|all future", m.group(2), re.I):
                if re.search(r"yes|allow|approve|proceed|ok\b", m.group(2), re.I):
                    choice = m.group(1)
                    break
        if choice:
            self._tmux("send-keys", "-t", seat, "-l", "--", choice)
            self._tmux("send-keys", "-t", seat, "Enter")
            return {"ok": True, "responded": "option %s" % choice}
        if any(re.search(r"\(y/n\)|\[y/n\]|\by/n\b", ln, re.I) for ln in bottom):
            self._tmux("send-keys", "-t", seat, "-l", "--", "y")
            self._tmux("send-keys", "-t", seat, "Enter")
            return {"ok": True, "responded": "y"}
        # last resort: accept the highlighted default with a bare Enter
        self._tmux("send-keys", "-t", seat, "Enter")
        return {"ok": True, "responded": "default"}

    def interrupt(self, seat):
        if not self._pane_exists(seat):
            return {"ok": False, "err": "no such seat"}
        self._tmux("send-keys", "-t", seat, "Escape")
        return {"ok": True}

    def kill(self, seat):
        self._tmux("kill-pane", "-t", seat)
        return {"ok": True}

    def ls(self):
        r = self._tmux("list-panes", "-a", "-F",
                       "#{pane_id}\t#{pane_current_command}\t#{pane_title}")
        seats = []
        if r.returncode == 0:
            for ln in r.stdout.splitlines():
                parts = ln.split("\t")
                if len(parts) >= 3:
                    seats.append({"seat": parts[0], "cmd": parts[1],
                                  "title": parts[2]})
        return {"ok": True, "seats": seats}
