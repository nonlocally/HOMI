# The wire protocol — 13 rules for speaking the bus

What any process must do to send, receive, or BE a peer. Reverse-engineered
from Claude Code's own peer messaging; communicate adds nothing proprietary.

## Sending

1. **Frame**: one newline-terminated JSON object per connection, then close:
   ```json
   {"type":"user","message":{"role":"user","content":"<nonempty string>"},
    "priority":"next","from":"uds:/abs/path/to/your.sock","msg_id":"<hex>"}
   ```
2. **content must be a nonempty plain STRING** — content-block arrays are
   rejected by the listener.
3. **Omit `session_id`** — a mismatched one silently drops the message.
4. **`from` is the reply address**: `uds:` + the absolute path of YOUR
   listening socket. No `from`, no replies.
5. **Attribution wrapper** (optional but expected between agents):
   `<cross-session-message from="uds:..." from-name="NAME">body</cross-session-message>`.
   Defang any `</cross-session-message` inside the body. Only a LEADING wrapper
   is attribution — one appearing mid-text is content: neither trust nor strip it.
   `from-name` is a claim, not a credential.

## Receiving / being a peer

6. **Sidecar schema** (`~/.claude/sessions/<pid>.json`): `pid`, `sessionId`,
   `cwd`, `startedAt`, `version`, `peerProtocol:1`, `kind:"interactive"`,
   `entrypoint`, `messagingSocketPath`, `name`, `status`, `updatedAt`.
7. **Numeric-filename rule**: discovery lists ONLY pid-shaped filenames
   (`123.json`). A `foo.json` sidecar survives sweeps but never appears in
   ListAgents. Adapters use a high numeric slot that is not a real pid.
8. **The sweep**: dead `pid` field + failed probe ⇒ sidecar unlinked. Long-lived
   adapters re-plant their sidecar every few seconds.
9. **Liveness probe**: a connect with ~250 ms timeout. Your listener must
   accept fast and tolerate empty probe connections (no data) without logging
   garbage or replying.
10. **Measured, not reported**: a socket FILE is not a listener. To check a
    peer, connect (fast EOF = dead; accepted/timeout = live) — never `test -S`.

## Environment

11. **Receipts** are sent only when `dirname(from) == dirname(receiver's
    socket)` — keep every socket in ONE directory (`/tmp/cc-socks` by default)
    so delivery receipts flow.
12. **Local-only guard**: Claude refuses non-local addresses; an ssh-forwarded
    unix socket looks local and passes — that is the entire basis of
    cross-device bridging. Never try to point it at `http(s)://`.
13. **Hygiene**: socket dir 0700 and owned by you (refuse otherwise), sockets
    0600. The filesystem is the trust boundary.
