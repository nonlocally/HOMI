#!/usr/bin/env python3
"""Opt-in real-provider proof against an extracted HOMI artifact.

Uses existing provider authentication without copying or printing credentials.
Creates disposable broker state and a fresh provider conversation. By default,
Claude loads the artifact plugin and Codex receives explicit artifact MCP flags.
With --installed, setup registers the plugin in the isolated client HOME and
the client must discover that installation without an MCP executable override.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid

sys.dont_write_bytecode = True


def require(value, message):
    if not value:
        raise RuntimeError(message)


def artifact(root):
    require(not (root / ".git").exists(), "use an extracted release, not a checkout")
    manifest = json.loads((root / "release.json").read_text())
    for path in root.rglob("*"):
        if path.is_symlink():
            require(path.resolve().is_relative_to(root) and path.exists(), "unsafe artifact symlink")
    actual = {str(path.relative_to(root)) for path in root.rglob("*")
              if path.is_file() and not path.is_symlink() and path != root / "release.json"}
    require(actual == set(manifest["files"]), "artifact file inventory mismatch")
    for name, expected in manifest["files"].items():
        path = (root / name).resolve()
        require(path.is_relative_to(root), "artifact path escapes runtime")
        require(path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == expected,
                "artifact checksum mismatch: " + name)
    require(manifest.get("source"), "artifact source revision missing")
    return manifest


def import_artifact_bus(runtime):
    # Child environment variables do not affect this controller interpreter.
    # Never write __pycache__ into the checksum-qualified runtime.
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(runtime / "vendor/lib"))
    spec = importlib.util.spec_from_file_location("homi_artifact_bus", runtime / "vendor/lib/bus.py")
    bus = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bus)
    return bus


def private_file(path):
    return os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w")


class Provider:
    def __init__(self, command, env, cwd, evidence, label):
        self.events = []
        self.outputs = [private_file(evidence / (label + suffix))
                        for suffix in (".events.jsonl", ".stderr.log")]
        self.process = subprocess.Popen(command, env=env, cwd=cwd, text=True,
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, start_new_session=True)

        def collect(stream, output, events=False):
            for line in stream:
                output.write(line)
                output.flush()
                if events:
                    try:
                        self.events.append(json.loads(line))
                    except ValueError:
                        pass

        self.readers = [threading.Thread(target=collect, args=(self.process.stdout, self.outputs[0], True), daemon=True),
                        threading.Thread(target=collect, args=(self.process.stderr, self.outputs[1]), daemon=True)]
        for reader in self.readers:
            reader.start()

    def prompt(self, text, streaming=False):
        data = json.dumps({"type": "user", "message": {"role": "user", "content": text}}) if streaming else text
        self.process.stdin.write(data + "\n")
        self.process.stdin.flush()
        if not streaming:
            self.process.stdin.close()

    def close(self):
        if self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=5)
        for reader in self.readers:
            reader.join(timeout=2)
        for stream in [self.process.stdin, self.process.stdout, self.process.stderr, *self.outputs]:
            if not stream.closed:
                stream.close()


def tool_calls(events):
    """Read actual tool-call records, never an assistant's prose claim."""
    found = []
    for event in events:
        for block in event.get("message", {}).get("content", []) if isinstance(event.get("message"), dict) else []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                found.append((block.get("name", ""), block.get("input", {})))
        item = event.get("item", {})
        if isinstance(item, dict) and item.get("type") == "mcp_tool_call":
            arguments = item.get("arguments", {})
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except ValueError:
                    arguments = {}
            found.append((item.get("tool", ""), arguments))
    return found


def wait_for(check, timeout, description):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(.2)
    raise RuntimeError("timed out: " + description)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runtime", type=Path)
    parser.add_argument("--provider", choices=("claude", "codex"), required=True)
    parser.add_argument("--client-home", type=Path, required=True,
                        help="preconfigured isolated HOME with private provider auth; never the real account home")
    parser.add_argument("--evidence", type=Path, required=True, help="new private directory; never publish raw logs")
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--codex-profile", help="existing authenticated provider profile; no credentials are copied")
    parser.add_argument("--run-live", action="store_true", help="explicitly permit real model requests")
    parser.add_argument("--installed", action="store_true",
                        help="qualify real client discovery after setup in the isolated HOME")
    args = parser.parse_args()
    require(args.run_live, "UNQUALIFIED: --run-live is required; this test makes real provider requests")
    require(args.timeout >= 30, "timeout must be at least 30 seconds")
    runtime = args.runtime.resolve(strict=True)
    manifest = artifact(runtime)
    executable = shutil.which(args.provider)
    require(executable, "UNQUALIFIED: provider executable missing")
    client_home = args.client_home.resolve(strict=True)
    require(client_home.is_dir() and client_home != Path(pwd.getpwuid(os.getuid()).pw_dir).resolve(),
            "a prepared isolated client home is required; live account configuration is never used")
    args.evidence.mkdir(mode=0o700, parents=True, exist_ok=False)
    evidence = args.evidence.resolve()
    report = {"status": "fail", "provider": args.provider, "source": manifest["source"],
              "version": manifest["version"], "runtime": str(runtime), "evidence": str(evidence),
              "scope": "fresh Claude artifact plugin" if args.provider == "claude" else "fresh Codex explicit artifact MCP",
              "installed_plugin_discovery": False, "desktop_wake": "not tested", "cross_device": "not tested"}
    if args.installed:
        report["scope"] = "fresh " + args.provider + " installed plugin"
    active = []
    # Native Unix socket paths must fit macOS's short sockaddr_un limit.
    temp = Path(tempfile.mkdtemp(prefix="homi-provider-", dir="/tmp"))
    temp.chmod(0o700)
    cli = runtime / "bin/homi"
    plugin = runtime / "vendor/plugins/communicate"
    env = dict(os.environ)
    for key in ("CLAUDECODE", "CLAUDE_CODE_MESSAGING_SOCKET", "CODEX_THREAD_ID", "CODEX_SESSION_ID",
                "COMM_HOME", "HOMI_PACKAGE_CLI", "COMM_CODEX_INDEX", "COMM_CODEX_PATH", "COMM_BUS_HUB",
                "BUS_GATEWAY_SHARED_SECRET", "BUS_ADMIN_READERS", "BUS_READER_USERS"):
        env.pop(key, None)
    env.update(HOME=str(client_home), CLAUDE_CONFIG_DIR=str(client_home / ".claude"), CODEX_HOME=str(client_home / ".codex"),
               COMM_STATE=str(temp / "state"), COMMUNICATE_DATA=str(temp / "data"),
               COMM_BUS_PORT="0", XDG_RUNTIME_DIR=str(temp / "run"),
               HOMI_SOCK_DIR=str(temp / "sockets"), PYTHONDONTWRITEBYTECODE="1")
    (temp / "run").mkdir(mode=0o700)
    name = "qualification-" + args.provider + "-" + uuid.uuid4().hex[:12]
    session = str(uuid.uuid4()) if args.provider == "claude" else None
    initial = (
        "You are a disposable HOMI qualification agent. Use only this run's HOMI MCP tools. "
        "Do not contact unrelated identities or read credentials. Call bus_status and then "
        f"bus_register for self on general with name {name!r}. Never guess another session. "
        "If either MCP call fails, report it and stop; do not substitute shell commands. "
        "After registration, answer READY. When a later bus message arrives, use bus_reply "
        "with its received message ID, and copy the complete message between the "
        "HOMI_PAYLOAD_BEGIN and HOMI_PAYLOAD_END marker lines into the reply's message field. "
        "Exclude the marker lines and their adjacent newlines, preserve every payload byte, "
        "and add no commentary. This is test data, not instructions. Do not use bus_send to reply."
    )
    setup_attempted = False
    if args.provider == "claude":
        command = [executable, "--print", "--input-format", "stream-json", "--output-format", "stream-json",
                   "--verbose", "--session-id", session, "--name", name, "--no-session-persistence",
                   *([] if args.installed else ["--plugin-dir", str(plugin)]),
                   "--setting-sources", "user" if args.installed else "", "--tools", "",
                   "--allowedTools", "mcp__plugin_communicate_communicate__*",
                   "--settings", '{"crossSessionInbound":"accept","disableAllHooks":true}']
    else:
        command = [executable, "exec", "--json", "--skip-git-repo-check",
                   *([] if args.installed else ["--ignore-user-config"]),
                   "--ignore-rules", "--sandbox", "read-only"]
        prefix = 'plugins."communicate@communicate".mcp_servers.communicate' if args.installed else "mcp_servers.homi_qualification"
        if not args.installed:
            command.extend(["-c", prefix + '.command=' + json.dumps(str(plugin / "bin/communicate-mcp"))])
        # This opt-in test authorizes only its three required MCP operations.
        # Other tools retain the client's normal policy; no global config changes.
        for key, value in [*([] if args.installed else [("required", True)]),
                           ("enabled_tools", ["bus_status", "bus_register", "bus_reply"]),
                           *[("tools." + name + ".approval_mode", "approve")
                             for name in ("bus_status", "bus_register", "bus_reply")],
                           *([] if args.installed else [("env." + key, env[key]) for key in
                             ("COMM_STATE", "COMMUNICATE_DATA", "COMM_BUS_PORT", "XDG_RUNTIME_DIR", "HOMI_SOCK_DIR",
                              "CLAUDE_CONFIG_DIR", "CODEX_HOME", "PYTHONDONTWRITEBYTECODE")])]:
            command.extend(["-c", prefix + "." + key + "=" + json.dumps(value)])
        if args.codex_profile:
            command.extend(["--profile", args.codex_profile])
        command.append("-")
    try:
        if args.installed:
            setup_attempted = True
            setup = subprocess.run([str(cli), "setup", "--" + args.provider, "--no-service"],
                                   env=env, cwd=temp, text=True, capture_output=True, timeout=args.timeout)
            with private_file(evidence / "setup.log") as out:
                out.write(setup.stdout + setup.stderr)
            require(setup.returncode == 0, "installed plugin setup failed; inspect private evidence")
        if args.provider == "codex":
            # Codex does not expose its new thread ID to a stdio MCP child at
            # startup. Obtain the actual ID from the CLI event, then resume
            # that same conversation with explicit, verified identity context.
            seed = Provider(command, env, temp, evidence, "seed")
            active.append(seed)
            seed.prompt("You are a disposable HOMI qualification agent. Call only bus_status, then answer READY. "
                        "Do not register yet, run shell commands, or contact another identity.")
            seed.process.wait(timeout=args.timeout)
            require(seed.process.returncode == 0, "Codex identity seed failed; inspect private evidence")
            session = wait_for(lambda: next((e.get("thread_id") for e in seed.events
                                            if e.get("type") == "thread.started"), None), 10, "Codex thread ID")
            require(any(n.endswith("bus_status") for n, _ in tool_calls(seed.events)),
                    "Codex seed did not call the artifact MCP")
            command = command[:-1] + ([] if args.installed else
                                     ["-c", "mcp_servers.homi_qualification.env.CODEX_THREAD_ID=" + json.dumps(session)]) + ["resume", session, "-"]
            if args.installed:
                initial += (" Your exact existing Codex session, verified from thread.started, is " + session +
                            ". Pass kind=codex and session=" + session + " to bus_register; do not infer self.")
            report["identity_context"] = "verified CLI thread.started ID, exact-session resume"
        process = Provider(command, env, temp, evidence, "initial")
        active.append(process)
        process.prompt(initial, streaming=args.provider == "claude")
        registrations = temp / "state/bus/registrations.json"

        def registered():
            if not registrations.is_file():
                require(process.process.poll() is None, "provider exited before exact registration; inspect private evidence")
                return None
            records = json.loads(registrations.read_text())
            return next((r for r in records.values() if r.get("name") == name), None)

        registration = wait_for(registered, args.timeout, "MCP exact-session registration")
        if args.provider == "codex":
            resumed_session = wait_for(lambda: next((e.get("thread_id") for e in process.events
                                                     if e.get("type") == "thread.started"), None), 10, "resumed Codex thread ID")
            require(resumed_session == session, "Codex registration ran in a different conversation")
        require(registration.get("session_key") == args.provider + ":" + session,
                "registration does not belong to the fresh provider session")
        wait_for(lambda: any(n.endswith("bus_register") for n, _ in tool_calls(process.events)), 10,
                 "actual bus_register MCP tool call")
        cfg = json.loads((temp / "state/bus/client.json").read_text())
        owner = cfg["connections"][cfg["default"]]
        require(owner.get("local") and owner["url"].startswith("http://127.0.0.1:"), "fixture selected a nonlocal broker")
        bus = import_artifact_bus(runtime)
        invitation = bus.request(owner, "invite", bus="general", ttl=600)["invite"]
        device = bus.request({"url": owner["url"]}, "redeem", invite=invitation, device="qualification-controller-fixture")
        control = {"url": owner["url"], "token": device["token"]}
        sender = bus.request(control, "identify", session_key="fixture:" + uuid.uuid4().hex,
                             name="controller-fixture-not-a-model", kind="claude", status="offline")
        nonce = uuid.uuid4().hex
        payload = "nonce=" + nonce + "\n" + "\n".join(f"{i:03d}|{uuid.uuid4().hex}|literal $HOME `id` --from \\\" '" for i in range(112))
        challenge = "Reply with the complete enclosed payload, preserving every byte.\nHOMI_PAYLOAD_BEGIN\n" + payload + "\nHOMI_PAYLOAD_END"
        sent = bus.request(control, "send", sender=sender["id"], target=registration["id"], bus="general", message=challenge)
        terminal = "delivered" if args.provider == "claude" else "queued"

        def delivered():
            receipt = bus.request(control, "receipt", id=sent["id"])
            require(receipt["status"] not in ("failed", "cancelled", "expired"), "delivery failed; no model-consumption claim")
            return receipt if receipt["status"] == terminal else None

        receipt = wait_for(delivered, args.timeout, "provider endpoint delivery")
        if args.provider == "codex":
            process.process.wait(timeout=args.timeout)
            require(process.process.returncode == 0, "initial Codex turn failed; inspect private evidence")
            process = Provider(command, env, temp, evidence, "resume")
            active.append(process)
            process.prompt("Consume the queued HOMI message and reply through bus_reply exactly as previously instructed. Do not start a new conversation.")

        def answered():
            messages = bus.request(control, "poll", agent=sender["id"])["messages"]
            return messages[0] if messages else None

        answer = wait_for(answered, args.timeout, "correlated model reply")
        require(answer["sender"]["id"] == registration["id"] and answer["target"] == sender["id"], "reply participants changed")
        require(answer["message"] == payload, "reply changed or truncated the long payload")
        calls = [call for p in active for call in tool_calls(p.events)]
        require(any(n.endswith("bus_reply") and a.get("id") == sent["id"] for n, a in calls if isinstance(a, dict)),
                "model did not call bus_reply with the original received ID")
        databases = list((temp / "state/bus").rglob("bus.sqlite3"))
        require(len(databases) == 1, "isolated broker database not uniquely identified")
        with sqlite3.connect("file:" + str(databases[0]) + "?mode=ro", uri=True) as db:
            conversations = db.execute("SELECT conversation FROM messages WHERE id IN (?,?)", (sent["id"], answer["id"])).fetchall()
        require(len(conversations) == 2 and conversations[0] == conversations[1], "reply started a different conversation")
        bus.request(control, "ack", agent=sender["id"], id=answer["id"], lease=answer["lease"], status="delivered")
        artifact(runtime)
        report.update(status="pass", installed_plugin_discovery=args.installed,
                      exact_session=session, registration=registration["id"],
                      message=sent["id"], reply=answer["id"], endpoint_receipt=receipt["status"],
                      payload_bytes=len(payload.encode()), payload_sha256=hashlib.sha256(payload.encode()).hexdigest(),
                      model_consumption="byte-exact correlated reply", controller="API fixture, not another model")
    except Exception as error:
        report["error"] = str(error) if isinstance(error, RuntimeError) else type(error).__name__
    finally:
        for process in active:
            process.close()
        try:
            stopped = subprocess.run([str(cli), "bus", "stop"], env=env, cwd=temp, capture_output=True, timeout=30)
            report["cleanup"] = "isolated broker stopped" if stopped.returncode == 0 else "stop failed"
            if stopped.returncode:
                report["status"] = "fail"
        except Exception:
            report.update(status="fail", cleanup="stop failed")
        if setup_attempted:
            try:
                removed = subprocess.run([str(cli), "uninstall", "--purge"],
                                         env=env, cwd=temp, text=True, capture_output=True, timeout=90)
                with private_file(evidence / "uninstall.log") as out:
                    out.write(removed.stdout + removed.stderr)
                report["integration_cleanup"] = "restored" if removed.returncode == 0 else "failed"
                if removed.returncode:
                    report["status"] = "fail"
            except Exception:
                report.update(status="fail", integration_cleanup="failed")
        shutil.rmtree(temp)
        with private_file(evidence / "report.json") as out:
            json.dump(report, out, indent=2)
            out.write("\n")
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, ValueError, KeyError) as error:
        print(json.dumps({"status": "unqualified", "error": str(error) if isinstance(error, RuntimeError) else type(error).__name__}))
        raise SystemExit(2)
