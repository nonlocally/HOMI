#!/usr/bin/env python3
"""Unit tests for the seat driver's fail-closed respond logic (review I-4/M-1)
and the menu-block parser — no tmux needed, pure logic against captured screens."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))
import homi_seat

pass_ = [0]
fail_ = [0]


def ok(m):
    pass_[0] += 1
    print("ok   " + m)


def bad(m):
    fail_[0] += 1
    print("FAIL " + m)


class Fake(homi_seat.SeatDriver):
    def __init__(self, screen):
        super().__init__()
        self.screen = screen
        self.keys = []

    def _pane_exists(self, seat):
        return True

    def _capture(self, seat, lines=None):
        s = self.screen.split("\n")
        return s[-lines:] if lines else s

    def _tmux(self, *a, **k):
        self.keys.append(a)

        class R:
            returncode = 0
            stdout = ""
            stderr = ""
        return R()

    def state(self, seat):
        return "approval"


HELD = ("  Message body (this is what will be delivered):\n"
        "  «do a thing»\n"
        "❯ Deny — drop it and tell the sender it was declined\n"
        "  Deliver this message to Claude")

# I-4: deny on a menu whose highlighted row is already Deny must Enter in place,
# never step toward the affirmative "Deliver" row.
d = Fake(HELD)
r = d.respond("%1", "deny")
downs = [k for k in d.keys if len(k) > 2 and "Down" in k]
if r.get("ok") and not downs:
    ok("deny confirms the highlighted Deny row without moving toward Deliver")
else:
    bad("deny moved toward the affirmative row: %s" % (d.keys,))

# The prose line "this is what will be delivered" must NOT be parsed as an option.
block = d._menu_block(HELD.split("\n"))
texts = [b["text"] for b in block]
if not any("what will be delivered" in t for t in texts):
    ok("prose above the menu is not parsed as an option")
else:
    bad("prose leaked into the menu block: %s" % texts)

# allow with no affirmative option => fail closed (never a bare Enter).
d2 = Fake("  Choose:\n❯ Cancel\n  Abort")
r = d2.respond("%1", "allow")
if not r.get("ok") and not any(("Enter",) == k[-1:] for k in d2.keys):
    ok("allow fails closed when no affirmative option exists (no Enter pressed)")
else:
    bad("allow did not fail closed: %s / %s" % (r, d2.keys))

# deny with no deny option => fail closed too.
d3 = Fake("  Proceed?\n❯ Yes, allow\n  Yes, and don't ask again")
r = d3.respond("%1", "deny")
if not r.get("ok"):
    ok("deny fails closed when no deny option exists")
else:
    bad("deny did not fail closed: %s" % r)

print("\npass=%d fail=%d" % (pass_[0], fail_[0]))
sys.exit(1 if fail_[0] else 0)
