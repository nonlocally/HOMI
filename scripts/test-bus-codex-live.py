#!/usr/bin/env python3
"""Opt-in real Codex smoke: hidden local sender -> scoped reply -> exact thread.

Uses an authenticated Codex CLI and real model requests. Bus services/devices
are disposable; the native Codex test thread remains available for inspection.
"""
import argparse
import base64
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import bus

spec = importlib.util.spec_from_file_location("claude_live_helpers", ROOT / "scripts/test-bus-claude-live.py")
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)
require = helpers.require


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hosted", action="store_true")
    parser.add_argument("--reader-env", type=Path)
    parser.add_argument("--readers", type=Path, default=ROOT.parent / "communicate-site/readers.json")
    args = parser.parse_args()
    if args.hosted and args.reader_env is None:
        parser.error("--hosted requires --reader-env")
    if not shutil.which("codex"):
        raise RuntimeError("authenticated local Codex CLI required")
    origin, owner_api = helpers.hosted_owner(args.reader_env, args.readers) if args.hosted else (None, None)
    temp = Path(tempfile.mkdtemp(prefix="bus-codex-live-"))
    temp.chmod(0o700)
    env = dict(os.environ, COMM_STATE=str(temp / "state"), COMM_BUS_PORT="0")
    for key in ("CLAUDE_CODE_MESSAGING_SOCKET", "CODEX_THREAD_ID", "CODEX_SESSION_ID",
                "BUS_GATEWAY_SHARED_SECRET", "BUS_ADMIN_READERS", "BUS_READER_USERS"):
        env.pop(key, None)
    devices, invites = [], []
    cleanup_ok = True

    def cli(*args):
        result = subprocess.run([str(ROOT / "bin/communicate"), "bus", *args], env=env,
                                capture_output=True, text=True, timeout=45)
        if result.returncode:
            raise RuntimeError("fixture bus command failed: " + args[0])
        return json.loads(result.stdout)

    def model(command, prompt):
        result = subprocess.run(command, input=prompt, cwd=temp, env=env, capture_output=True, text=True, timeout=150)
        if result.returncode:
            raise RuntimeError("disposable Codex model command failed")
        events = []
        for line in result.stdout.splitlines():
            try:
                events.append(json.loads(line))
            except ValueError:
                continue
        return events

    try:
        events = model(["codex", "exec", "--json", "--skip-git-repo-check", "--sandbox", "read-only", "-"],
                       "This is a disposable Communicate delivery test. Reply READY only. Do not use tools or contact anyone.")
        thread = next((event.get("thread_id") for event in events if event.get("type") == "thread.started"), None)
        require(thread, "created a disposable real Codex thread")
        env["CODEX_THREAD_ID"] = thread
        if args.hosted:
            invite = owner_api("invite", bus="general", user="aadarwal", ttl=60)["invite"]
            invites.append(invite)
            code = "commbus1." + base64.urlsafe_b64encode(json.dumps({"url": origin, "invite": invite}).encode()).decode().rstrip("=")
            devices.append(cli("connect", code, "--device", "disposable-live-codex-" + thread[:8])["principal"])
        else:
            cli("list")
            config = json.loads((temp / "state/bus/client.json").read_text())
            owner = config["connections"][config["default"]]
            origin = owner["url"]
            owner_api = lambda op, **fields: bus.request(owner, op, **fields)
        invite = owner_api("invite", bus="general", ttl=60, **({"user": "aadarwal"} if args.hosted else {}))["invite"]
        invites.append(invite)
        peer = bus.request({"url": origin}, "redeem", invite=invite, device="disposable-codex-responder-fixture")
        devices.append(peer["principal"])
        conn = {"url": origin, "token": peer["token"]}
        recipient = bus.request(conn, "register", session_key="fixture:" + thread, name="disposable-responder",
                                kind="claude", status="offline")
        # No register command for the actual Codex sender: this is the user
        # requirement that general outbound communication needs no publication.
        sent = cli("send", recipient["id"], "--", "Disposable general outbound test; return the test marker through scoped reply.")
        incoming = bus.request(conn, "poll_device", agents=[recipient["id"]])["messages"][0]
        require(incoming["id"] == sent["id"], "unpublished current Codex thread initiated on general")
        snapshot = owner_api("snapshot")
        hidden = incoming["sender"]["id"]
        require(hidden not in {agent["id"] for group in snapshot["buses"] for agent in group["agents"]},
                "real Codex sender absent from directory")
        nonce = "BUS_CODEX_NONCE_" + uuid.uuid4().hex
        reply = bus.request(conn, "reply", sender=recipient["id"], id=incoming["reply_to"],
                            message="Remember this delivery test token: " + nonce + ". Do not use tools or message anyone.")
        deadline = time.monotonic() + 75
        receipt = {}
        while time.monotonic() < deadline:
            receipt = bus.request(conn, "receipt", id=reply["id"])
            if receipt.get("status") in ("queued", "failed", "cancelled", "expired"):
                break
            time.sleep(.5)
        require(receipt.get("status") == "queued", "scoped reply queued into the exact unpublished Codex thread")
        events = model(["codex", "exec", "resume", thread, "--json", "--skip-git-repo-check", "-"],
                       "Reply with exactly the latest BUS_CODEX_NONCE_ token supplied in the queued Communicate message. Do not use tools.")
        answers = [event.get("item", {}).get("text", "") for event in events
                   if event.get("type") == "item.completed" and event.get("item", {}).get("type") == "agent_message"]
        require(any(nonce in answer for answer in answers), "real Codex model consumed the scoped reply and returned its nonce")
        print("Native disposable test thread: " + thread, flush=True)
    finally:
        if owner_api:
            for principal in devices:
                try:
                    owner_api("revoke", principal=principal)
                except Exception:
                    cleanup_ok = False
            for invite in invites:
                try:
                    owner_api("invite_revoke", invite=invite)
                except Exception:
                    pass
        subprocess.run([str(ROOT / "bin/communicate"), "bus", "stop"], env=env, capture_output=True, timeout=20)
        shutil.rmtree(temp)
    require(cleanup_ok, "temporary Codex device principals revoked and isolated services stopped")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("FAIL " + (str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__), flush=True)
        raise SystemExit(1)
