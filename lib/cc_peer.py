#!/usr/bin/env python3
"""cc_peer — speak the Claude Code peer ("cc-socks") wire protocol.

Two roles:

  serve  Stand up a unix socket that Claude's discovery will list as a native
         peer. Each inbound message is handed to a Codex agent (via the sibling
         `communicate codex ask`, which runs `codex exec` over ssh), and the
         reply is written back to the sender's socket so it appears in their
         Claude session as a cross-session message.

  send   Raw-inject a single user message into any peer socket (the programmatic
         equivalent of the binary's own hint:
             echo '{"type":"user","message":{...}}' | socat - UNIX-CONNECT:<sock>)

Wire facts (reverse-engineered + verified live):
  * Messages are newline-delimited JSON written to the peer's unix socket.
  * An inbound user message is: {"type":"user","message":{"role":"user",
    "content": <string | [content-blocks]>}, "from":"uds:<socket-path>", ...}.
  * `from` is "uds:" + an absolute .sock path; a reply/receipt is delivered by
    connecting to that path and writing another user message.
  * Liveness discovery merely connects to the socket; accepting is enough.
"""
import argparse, json, os, signal, socket, subprocess, sys, threading, time, uuid


def _extract_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                parts.append(b.get("text", ""))
            elif isinstance(b, str):
                parts.append(b)
        return "".join(parts)
    return "" if content is None else str(content)


def _addr_from(uds):
    if isinstance(uds, str) and uds.startswith("uds:"):
        return uds[4:]
    return None


def _xml_attr(s):
    return (str(s).replace("&", "&amp;").replace('"', "&quot;")
            .replace("<", "&lt;").replace(">", "&gt;"))


def _wrap(text, from_sock, from_name):
    """Wrap a reply body as a cross-session-message so the receiver renders it
    with proper `from` / `from-name` attribution (matches PCr() in the binary).
    Delivery works with a raw string too, but the wrapper gives us identity."""
    # Never let the body forge the closing tag.
    body = text.replace("</cross-session-message", "</ cross-session-message")
    attrs = 'from="uds:%s"' % _xml_attr(from_sock)
    if from_name:
        attrs += ' from-name="%s"' % _xml_attr(from_name)
    return "<cross-session-message %s>\n%s\n</cross-session-message>" % (attrs, body)


def deliver(to_sock, text, from_sock, from_name=None, orig_msg_id=None):
    """Write one user message to `to_sock` so it lands in that Claude session.

    content MUST be a nonempty plain string (the listener hard-rejects
    non-strings); session_id is deliberately omitted (a mismatched one is
    dropped by the receiver)."""
    content = _wrap(text, from_sock, from_name) if from_name is not None else text
    if not content:
        return
    msg = {
        "type": "user",
        "message": {"role": "user", "content": content},
        "priority": "next",
        "from": "uds:" + from_sock,
        "msg_id": uuid.uuid4().hex,
    }
    data = (json.dumps(msg) + "\n").encode("utf-8")
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(10)
    try:
        s.connect(to_sock)
        s.sendall(data)
    finally:
        s.close()


def _read_line(conn, timeout=0.4):
    conn.settimeout(timeout)
    buf = b""
    try:
        while b"\n" not in buf:
            chunk = conn.recv(65536)
            if not chunk:
                break
            buf += chunk
            if len(buf) > 8 * 1024 * 1024:
                break
    except socket.timeout:
        pass
    except OSError:
        pass
    return buf


def _handle(conn, opts):
    raw = _read_line(conn)
    try:
        conn.close()
    except OSError:
        pass
    line = raw.split(b"\n", 1)[0].strip()
    if not line:
        return  # liveness probe or empty; nothing to do
    try:
        msg = json.loads(line.decode("utf-8", "replace"))
    except Exception:
        return
    if msg.get("type") != "user":
        return
    text = _extract_text((msg.get("message") or {}).get("content"))
    sender = _addr_from(msg.get("from"))
    if not text or not sender:
        return

    # Hand the message to the Codex agent on the target device. Reuse the
    # hardened `communicate codex ask` (stdin-safe, thread-continuity). Passing
    # text as a single argv element keeps it injection-safe.
    cmd = [opts.communicate, "codex", "ask", opts.device,
           "--thread", "peer-" + opts.name]
    if opts.dir:
        cmd += ["--dir", opts.dir]
    if opts.auto:
        cmd += ["--auto"]
    cmd += ["--", text]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=opts.timeout)
        reply = (out.stdout or "").strip() or ("[codex error] " + (out.stderr or "").strip()[:500])
    except subprocess.TimeoutExpired:
        reply = "[codex timed out after %ss]" % opts.timeout
    except Exception as e:  # pragma: no cover
        reply = "[peer error] %s" % e

    try:
        deliver(sender, reply, opts.socket, from_name=opts.name, orig_msg_id=msg.get("msg_id"))
    except Exception as e:
        sys.stderr.write("deliver failed: %s\n" % e)


def _sidecar_obj(sock, name, pid):
    now = int(time.time() * 1000)
    return {
        "pid": pid,                       # a LIVE pid -> the discovery sweep never reaps us
        "sessionId": str(uuid.uuid4()),
        "cwd": "-",
        "startedAt": now,
        "version": "communicate-peer",
        "peerProtocol": 1,
        "kind": "interactive",
        "entrypoint": "cli",
        "messagingSocketPath": sock,
        "name": name,
        "status": "idle",
        "updatedAt": now,
        "statusUpdatedAt": now,
    }


def serve(opts):
    if os.path.exists(opts.socket):
        os.unlink(opts.socket)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(opts.socket)
    os.chmod(opts.socket, 0o600)
    srv.listen(64)

    pid = os.getpid()
    sidecar = os.path.join(opts.sessions_dir, "%d.json" % pid) if opts.sessions_dir else None
    stop = threading.Event()

    def plant():
        if not sidecar:
            return
        try:
            tmp = sidecar + ".tmp"
            with open(tmp, "w") as f:
                json.dump(_sidecar_obj(opts.socket, opts.name, pid), f)
            os.replace(tmp, sidecar)
        except Exception as e:  # pragma: no cover
            sys.stderr.write("plant failed: %s\n" % e)

    def cleanup(*_a):
        stop.set()
        for p in (sidecar, opts.socket):
            try:
                if p and os.path.exists(p):
                    os.unlink(p)
            except OSError:
                pass
        os._exit(0)

    def replanter():
        while not stop.wait(3):
            plant()

    plant()
    threading.Thread(target=replanter, daemon=True).start()
    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup)
    sys.stderr.write("cc_peer serving %s (pid %d) -> codex@%s; sidecar=%s\n"
                     % (opts.name, pid, opts.device, sidecar))
    while True:
        try:
            conn, _ = srv.accept()
        except KeyboardInterrupt:
            cleanup()
        except OSError:
            continue
        threading.Thread(target=_handle, args=(conn, opts), daemon=True).start()


def recv(opts):
    """Listen on a socket and print the body of the first real message that
    arrives (ignoring empty liveness probes), then exit. For tests."""
    if os.path.exists(opts.socket):
        os.unlink(opts.socket)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(opts.socket)
    os.chmod(opts.socket, 0o600)
    srv.listen(8)
    srv.settimeout(opts.timeout)
    try:
        while True:
            try:
                conn, _ = srv.accept()
            except socket.timeout:
                sys.stderr.write("recv: timed out\n")
                return 2
            raw = _read_line(conn, timeout=1.0)
            try:
                conn.close()
            except OSError:
                pass
            line = raw.split(b"\n", 1)[0].strip()
            if not line:
                continue
            try:
                msg = json.loads(line.decode("utf-8", "replace"))
            except Exception:
                continue
            if msg.get("type") != "user":
                continue
            sys.stdout.write(_extract_text((msg.get("message") or {}).get("content")))
            sys.stdout.flush()
            return 0
    finally:
        try:
            os.unlink(opts.socket)
        except OSError:
            pass


def main():
    ap = argparse.ArgumentParser(prog="cc_peer")
    sub = ap.add_subparsers(dest="role", required=True)

    s = sub.add_parser("serve")
    s.add_argument("--socket", required=True)
    s.add_argument("--device", required=True)
    s.add_argument("--name", required=True)
    s.add_argument("--communicate", required=True, help="path to the communicate CLI")
    s.add_argument("--sessions-dir", dest="sessions_dir", default="",
                   help="Claude sessions dir; the daemon plants+maintains its own sidecar here")
    s.add_argument("--dir", default="")
    s.add_argument("--auto", action="store_true")
    s.add_argument("--timeout", type=int, default=180)

    d = sub.add_parser("send")
    d.add_argument("--to", required=True, help="target socket path")
    d.add_argument("--text", required=True)
    d.add_argument("--from", dest="from_sock", required=True, help="our socket path (reply address)")
    d.add_argument("--name", default=None, help="from-name attribution to render")

    r = sub.add_parser("recv")
    r.add_argument("--socket", required=True)
    r.add_argument("--timeout", type=float, default=60)

    a = ap.parse_args()
    if a.role == "serve":
        serve(a)
    elif a.role == "recv":
        sys.exit(recv(a))
    else:
        deliver(a.to, a.text, a.from_sock, from_name=a.name)


if __name__ == "__main__":
    main()
