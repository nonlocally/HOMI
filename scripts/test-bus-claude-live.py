#!/usr/bin/env python3
"""Opt-in real Claude smoke: CLI registration -> HTTP broker -> worker -> model.

Requires an authenticated local Claude CLI and makes real model requests. All
bus state, listener paths, and the Claude session are disposable. Existing
agents are only listed; none receive messages or have their routes changed.
"""
import argparse
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import bus


REMOTE_SENDER = r'''
import json, platform, socket, sys, urllib.request
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None
opener = urllib.request.build_opener(NoRedirect(), urllib.request.ProxyHandler({}))
def request(op, token=None, **payload):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request("https://bus.communicate.sh/v1", headers=headers,
        data=json.dumps(dict(payload, op=op)).encode())
    with opener.open(req, timeout=20) as response:
        raw = response.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError("response too large")
    result = json.loads(raw)
    if not result.get("ok"):
        raise ValueError("broker rejected fixture")
    return result
try:
    payload = json.load(sys.stdin)
    metadata = {"hostname": socket.gethostname(), "platform": platform.system()}
    participant = request("redeem", invite=payload["invite"], device=payload["device"],
                          device_metadata=metadata)
    # These records travel only through the parent's captured SSH pipe. Emit
    # enrollment immediately so cleanup still has its principal after failure.
    print(json.dumps({"stage": "enrolled", "participant": participant}), flush=True)
    identity = request("identify", participant["token"], session_key="fixture:" + payload["session_id"],
        name="disposable-remote-HTTPS-fixture", kind="claude", status="offline",
        description="Temporary HTTPS deployment API fixture on a separate device; no agent process.")
    sent = request("send", participant["token"], sender=identity["id"], target=payload["target"],
        bus="general", message="Echo this exact token in your assistant answer: " + payload["nonce"])
    print(json.dumps({"stage": "sent", "identity": identity, "sent": sent, "device_metadata": metadata}), flush=True)
except Exception:
    print(json.dumps({"stage": "error"}), flush=True)
    raise SystemExit(1)
'''


def remote_sender(host, payload, cleanup_principals):
    """Run only an in-memory stdlib API fixture; all capabilities use stdin."""
    command = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
               "-o", "ConnectionAttempts=1", "-o", "StrictHostKeyChecking=yes", "-o", "UpdateHostKeys=no",
               host, "python3 -c " + shlex.quote(REMOTE_SENDER)]
    output, returncode = "", None
    try:
        result = subprocess.run(command, input=json.dumps(payload), capture_output=True, text=True, timeout=85)
        output, returncode = result.stdout, result.returncode
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
    participant, delivered = None, None
    for line in output.splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if record.get("stage") == "enrolled":
            participant = record.get("participant")
            if isinstance(participant, dict) and isinstance(participant.get("principal"), str):
                cleanup_principals.append(participant["principal"])
        elif record.get("stage") == "sent":
            delivered = record
    if returncode != 0 or not participant or not delivered:
        raise RuntimeError("remote HTTPS sender fixture could not complete")
    require(delivered.get("device_metadata", {}).get("hostname") != socket.gethostname(),
            "HTTPS sender executed on the SSH-selected second device")
    return participant, delivered["identity"], delivered["sent"]


def hosted_github_cookie(secret, identity):
    """Operator-signed role fixture; this does not perform GitHub OAuth."""
    origin = "https://bus.communicate.sh"
    expires = int(time.time()) + 120
    material = "communicate/github-identity/v1\0%d\0aadarwal\0%s" % (identity["id"], identity["reader"])
    credential_hash = hashlib.sha256(material.encode()).hexdigest()
    payload = {"v": "gh1", "origin": origin, "iat": expires - 43200, "exp": expires,
               "login": "aadarwal", "id": identity["id"], "reader": identity["reader"],
               "credentialHash": credential_hash}
    def encoded(data):
        return base64.urlsafe_b64encode(data).decode().rstrip("=")
    value = encoded(json.dumps(payload, separators=(",", ":")).encode())
    signature = encoded(hmac.new(secret.encode(), ("communicate/github/gh1/v1\0%s\0%s" % (origin, value)).encode(),
                                hashlib.sha256).digest())
    return "__Host-communicate_github=gh1.%s.%s" % (value, signature)


def hosted_owner(env_file, github_users_file):
    """Mint short-lived GitHub admission in memory, independently of OAuth."""
    secret = None
    for line in env_file.read_text().splitlines():
        name, separator, value = line.partition("=")
        if separator and name.strip() == "GITHUB_SESSION_SECRET":
            value = value.strip()
            secret = json.loads(value) if value.startswith('"') else value.strip("'")
    if (not isinstance(secret, str) or not 32 <= len(secret) <= 512
            or not all(33 <= ord(character) <= 126 for character in secret)):
        raise RuntimeError("valid protected GitHub-session signing secret required for hosted mode")
    users = json.loads(github_users_file.read_text())
    identity = users.get("aadarwal") if isinstance(users, dict) else None
    if (not isinstance(identity, dict) or type(identity.get("id")) is not int
            or identity["id"] <= 0 or identity.get("reader") != "aadarsh"):
        raise RuntimeError("reviewed owner GitHub identity required for hosted mode")
    origin = "https://bus.communicate.sh"
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    opener = urllib.request.build_opener(NoRedirect(), urllib.request.ProxyHandler({}))
    def request(path, token=None, body=None):
        headers = {"Cookie": hosted_github_cookie(secret, identity), "Origin": origin}
        if token:
            headers["Authorization"] = "Bearer " + token
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        try:
            with opener.open(urllib.request.Request(origin + path, headers=headers, data=data), timeout=20) as response:
                result = json.loads(response.read(2 * 1024 * 1024))
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and body and body.get("op") == "invite_revoke":
                return {"ok": True, "already_unusable": True}
            raise RuntimeError("hosted owner admission/API request failed") from None
        except Exception:
            raise RuntimeError("hosted owner admission/API request failed") from None
        if not result.get("ok"):
            raise RuntimeError("hosted owner API rejected fixture request")
        return result
    bootstrap = request("/_bus/session")
    def owner_api(op, **payload):
        return request("/v1", bootstrap["token"], dict(payload, op=op))
    require(owner_api("snapshot").get("is_admin"), "hosted signed-session owner role (not real GitHub OAuth)")
    return origin, owner_api


def require(value, label):
    if not value:
        raise RuntimeError(label)
    print("PASS " + label, flush=True)


def roster():
    result = subprocess.run([str(ROOT / "bin/communicate"), "agents", "--json"],
                            capture_output=True, text=True, check=True, timeout=15)
    return {(row["name"], row["type"], row["via"], row["socket"])
            for row in json.loads(result.stdout)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hosted", action="store_true", help="use bus.communicate.sh instead of an isolated local broker")
    parser.add_argument("--reader-env", type=Path, help="protected GITHUB_SESSION_SECRET file for hosted signing-role fixtures")
    parser.add_argument("--github-users", "--readers", dest="github_users", type=Path,
                        default=ROOT.parent / "communicate-site/github-users.json",
                        help="reviewed GitHub allowlist; --readers is a compatibility alias")
    parser.add_argument("--sender-host", help="existing authorized SSH alias for a second-device HTTPS API sender fixture")
    args = parser.parse_args()
    if args.hosted and args.reader_env is None:
        parser.error("--hosted requires --reader-env")
    if args.sender_host and (not args.hosted or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.@-]*", args.sender_host)):
        parser.error("--sender-host requires --hosted and a plain SSH host alias")
    owner_url, owner_api = hosted_owner(args.reader_env, args.github_users) if args.hosted else (None, None)
    cleanup_principals, cleanup_invites = [], []
    remote_device_label = None
    if not shutil.which("claude"):
        print("SKIP authenticated local Claude CLI required")
        return
    before = roster()
    session_id = str(uuid.uuid4())
    nonce = "BUS_CLAUDE_NONCE_" + uuid.uuid4().hex
    temp = Path(tempfile.mkdtemp(prefix="bus-claude-live-"))
    temp.chmod(0o700)
    # Claude reads its normal authentication, but receives no user settings,
    # MCP servers, tools, hooks, or persistent conversation from this fixture.
    env = dict(os.environ, XDG_RUNTIME_DIR=str(temp), COMM_STATE=str(temp / "state"), COMM_BUS_PORT="0")
    for key in ("CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDECODE", "CODEX_THREAD_ID", "CODEX_SESSION_ID",
                "BUS_GATEWAY_SHARED_SECRET", "BUS_ADMIN_READERS", "BUS_READER_USERS"):
        env.pop(key, None)
    session_dir = Path(env.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude"))) / "sessions"
    command = ["claude", "--print", "--input-format", "stream-json", "--output-format", "stream-json",
               "--verbose", "--session-id", session_id, "--name", "bus-live-disposable-" + session_id[:8],
               "--no-session-persistence", "--tools", "", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
               "--setting-sources", "", "--settings", '{"crossSessionInbound":"accept","disableAllHooks":true}']
    process = subprocess.Popen(command, cwd=temp, env=env, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    events, errors = [], []
    def collect(stream, output):
        for line in stream:
            output.append(line)
    readers = [threading.Thread(target=collect, args=(process.stdout, events), daemon=True),
               threading.Thread(target=collect, args=(process.stderr, errors), daemon=True)]
    for reader in readers:
        reader.start()
    def cli(*args):
        result = subprocess.run([str(ROOT / "bin/communicate"), "bus", *args], env=env,
                                capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise RuntimeError("fixture bus command failed")
        return json.loads(result.stdout)
    try:
        initial = {"type": "user", "message": {"role": "user", "content":
                   "You are a disposable messaging test agent. Do not use tools or contact anyone. Reply READY only. "
                   "For each later message, echo its BUS_CLAUDE_NONCE_ token exactly in your assistant answer."}}
        process.stdin.write(json.dumps(initial) + "\n")
        process.stdin.flush()
        sidecar = None
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and process.poll() is None:
            for path in session_dir.glob("*.json"):
                try:
                    data = json.loads(path.read_text())
                except (OSError, ValueError):
                    continue
                if data.get("sessionId") == session_id:
                    sidecar = data
            if sidecar:
                break
            time.sleep(.1)
        require(sidecar and Path(sidecar["messagingSocketPath"]).is_relative_to(temp), "disposable native Claude listener")
        env["CLAUDE_CODE_MESSAGING_SOCKET"] = sidecar["messagingSocketPath"]
        if args.hosted:
            invitation = owner_api("invite", bus="general", user="aadarwal", ttl=60)
            cleanup_invites.append(invitation["invite"])
            code = "commbus1." + base64.urlsafe_b64encode(json.dumps({
                "url": owner_url, "invite": invitation["invite"]}).encode()).decode().rstrip("=")
            device = cli("connect", code, "--device", "disposable-live-claude-" + session_id[:8])
            cleanup_principals.append(device["principal"])
        registration = cli("register", "--name", "disposable-native-claude")
        require(registration.get("status") == "live", "CLI registered the actual Claude session")
        config = json.loads((temp / "state/bus/client.json").read_text())
        if not args.hosted:
            owner = config["connections"][config["default"]]
            owner_url = owner["url"]
            owner_api = lambda op, **payload: bus.request(owner, op, **payload)
        invitation = owner_api("invite", bus="general", ttl=60, **({"user": "aadarwal"} if args.hosted else {}))
        cleanup_invites.append(invitation["invite"])
        if args.sender_host:
            remote_device_label = "deployment HTTPS fixture on " + args.sender_host + " " + session_id[:8]
            participant, identity, sent = remote_sender(args.sender_host, {
                "invite": invitation["invite"], "target": registration["id"], "nonce": nonce,
                "session_id": session_id, "device": remote_device_label,
            }, cleanup_principals)
        else:
            participant = bus.request({"url": owner_url}, "redeem", invite=invitation["invite"], device="disposable-test-sender")
            cleanup_principals.append(participant["principal"])
            sender = {"url": owner_url, "token": participant["token"]}
            identity = bus.request(sender, "identify", session_key="fixture:" + session_id,
                                   name="disposable-hidden-sender", kind="claude", status="offline")
            sent = bus.request(sender, "send", sender=identity["id"], target=registration["id"], bus="general",
                               message="Echo this exact token in your assistant answer: " + nonce)
        conn = {"url": owner_url, "token": participant["token"]}
        require(sent.get("status") == "accepted", "HTTP broker accepted hidden sender message")
        receipt = None
        deadline = time.monotonic() + 100
        responded = False
        while time.monotonic() < deadline and process.poll() is None:
            receipt = bus.request(conn, "receipt", id=sent["id"])
            for line in list(events):
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("type") == "assistant" and nonce in json.dumps(event.get("message", {})):
                    responded = True
            if responded and receipt.get("status") == "delivered":
                break
            time.sleep(.25)
        require(receipt and receipt.get("status") == "delivered", "worker acknowledged native socket delivery")
        require(responded, "real Claude model echoed the broker-delivered nonce")
        snapshot = owner_api("snapshot")
        require(identity["id"] not in {agent["id"] for group in snapshot["buses"] for agent in group["agents"]},
                "outbound sender stayed absent from the bus roster")
    finally:
        cleanup_ok = True
        if owner_api:
            if remote_device_label:
                # A connection loss after redemption can prevent SSH from
                # returning its principal. Recover only this uniquely named
                # fixture, never another existing device.
                try:
                    cleanup_principals.extend(principal["id"] for principal in owner_api("snapshot").get("principals", [])
                                              if principal.get("device") == remote_device_label and principal.get("user") == "aadarwal")
                except Exception:
                    cleanup_ok = False
            for principal in dict.fromkeys(cleanup_principals):
                try:
                    owner_api("revoke", principal=principal)
                except Exception:
                    cleanup_ok = False
            for invite in cleanup_invites:
                # A redeemed invite is already invalid; revoke is best-effort
                # only for invitations not consumed before a failed connect.
                try:
                    owner_api("invite_revoke", invite=invite)
                except bus.BusError as exc:
                    cleanup_ok = cleanup_ok and exc.code == "not_found"
                except Exception:
                    cleanup_ok = False
        subprocess.run([str(ROOT / "bin/communicate"), "bus", "stop"], env=env,
                       capture_output=True, timeout=20)
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=8)
        for reader in readers:
            reader.join(2)
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()
        shutil.rmtree(temp)
    after = roster()
    preserved = before.intersection(after)
    print("PASS existing legacy routing entries still present: %d (of %d observed before; other sessions may end independently)" %
          (len(preserved), len(before)), flush=True)
    require(process.poll() is not None, "disposable Claude and isolated bus services stopped")
    require(cleanup_ok, "temporary device principals revoked")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("FAIL " + (str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__), flush=True)
        raise SystemExit(1)
