"""
title: Agent Dispatch
author: aadarwal
description: Discover agents on the communicate bus and dispatch work to specialists — codex agents synchronously, and remote Claude sessions via a mailbox reply-capture round trip.
version: 0.2.0
"""

import json
import os
import re
import socket
import subprocess
import time

from pydantic import BaseModel, Field

REPO_ROOT_HINT = "/Users/aadarwal/src/aadarwal/communicate-owui"
DEFAULT_SOCK_DIR = os.environ.get("XDG_RUNTIME_DIR", "/tmp") + "/cc-socks"
DEFAULT_STATE = os.path.expanduser("~/.local/state/communicate/openwebui")
_WRAP = re.compile(r"<cross-session-message[^>]*>\s*(.*?)\s*</cross-session-message>",
                   re.DOTALL)


def _host_short_name():
    return socket.gethostname().split(".")[0].lower()


def _unwrap(text):
    m = _WRAP.search(text or "")
    return m.group(1).strip() if m else (text or "").strip()


class Tools:
    class Valves(BaseModel):
        communicate_path: str = Field(
            default=os.path.join(REPO_ROOT_HINT, "bin", "communicate"),
            description="Absolute path to the communicate CLI",
        )
        timeout_seconds: int = Field(
            default=300, description="Max seconds to wait for a specialist reply"
        )
        self_devices: str = Field(
            default="aadarshs-mac-air-2",
            description="Comma-separated device names that mean THIS machine (tailnet name etc.); dispatch to them runs locally instead of over ssh",
        )
        orchestrator_socket: str = Field(
            default=os.path.join(DEFAULT_SOCK_DIR, "orchestrator.sock"),
            description="The orchestrator's own peer socket (return address for Claude-agent replies)",
        )
        mailbox_path: str = Field(
            default=os.path.join(DEFAULT_STATE, "mailbox.jsonl"),
            description="JSONL file the mailbox daemon appends inbound replies to",
        )
        sessions_dir: str = Field(
            default=os.path.expanduser("~/.claude/sessions"),
            description="Claude sessions dir where the orchestrator sidecar is planted",
        )

    def __init__(self):
        self.valves = self.Valves()

    def _run(self, args, timeout, env=None):
        return subprocess.run(
            [self.valves.communicate_path] + args,
            capture_output=True, text=True, timeout=timeout, env=env,
        )

    def _cc_peer_py(self):
        return os.path.join(os.path.dirname(os.path.dirname(self.valves.communicate_path)),
                            "lib", "cc_peer.py")

    def _ensure_mailbox(self):
        """Start the orchestrator mailbox daemon if its socket isn't live."""
        sock = self.valves.orchestrator_socket
        try:
            c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            c.settimeout(0.5)
            c.connect(sock)
            c.close()
            return True  # already listening
        except OSError:
            pass
        os.makedirs(os.path.dirname(self.valves.mailbox_path), exist_ok=True)
        os.makedirs(DEFAULT_SOCK_DIR, exist_ok=True)
        try:
            os.chmod(DEFAULT_SOCK_DIR, 0o700)
        except OSError:
            pass
        subprocess.Popen(
            ["python3", self._cc_peer_py(), "mailbox",
             "--socket", sock, "--name", "orchestrator",
             "--sessions-dir", self.valves.sessions_dir,
             "--mailbox", self.valves.mailbox_path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        for _ in range(20):
            try:
                c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                c.settimeout(0.5); c.connect(sock); c.close()
                return True
            except OSError:
                time.sleep(0.25)
        return False

    def _ask_claude(self, name, entry, message):
        """Message a Claude session (local or bridged) and capture its reply
        from the orchestrator mailbox."""
        if not self._ensure_mailbox():
            return "ERROR: could not start the orchestrator mailbox daemon."
        # Resolve the target socket; if not reachable locally, bridge it in.
        who = self._run(["whereis", name], 20)
        if who.returncode != 0 or "\t" not in (who.stdout or ""):
            device = (entry.get("device") or "").strip()
            if not device or device.lower() in self._self_name_set():
                return (f"'{name}' is not reachable and has no remote device to bridge. "
                        "Bridge it first: communicate claude bridge <device> <session>.")
            env = dict(os.environ, CLAUDE_CODE_MESSAGING_SOCKET=self.valves.orchestrator_socket)
            br = self._run(["claude", "bridge", device, name], 60, env=env)
            if br.returncode != 0:
                return (f"ERROR: could not bridge '{name}' on {device}: "
                        f"{br.stderr.strip()[-400:]}")
            who = self._run(["whereis", name], 20)
            if who.returncode != 0 or "\t" not in (who.stdout or ""):
                return f"ERROR: '{name}' still not resolvable after bridging."
        sock = who.stdout.strip().split("\t")[-1]
        # Record mailbox position, send with the orchestrator as reply address.
        try:
            before = sum(1 for _ in open(self.valves.mailbox_path)) if \
                os.path.exists(self.valves.mailbox_path) else 0
        except OSError:
            before = 0
        body = (message + "\n\n(You are being messaged by the peer 'orchestrator'. "
                "When done, reply to 'orchestrator' with your answer.)")
        env = dict(os.environ, CLAUDE_CODE_MESSAGING_SOCKET=self.valves.orchestrator_socket)
        snd = self._run(["send", sock, "--as", "orchestrator", "--", body], 30, env=env)
        if snd.returncode != 0:
            return f"ERROR sending to '{name}': {snd.stderr.strip()[-400:]}"
        # Poll the mailbox for a new line.
        deadline = time.time() + self.valves.timeout_seconds
        while time.time() < deadline:
            try:
                lines = open(self.valves.mailbox_path).read().splitlines()
            except OSError:
                lines = []
            if len(lines) > before:
                for ln in lines[before:]:
                    try:
                        obj = json.loads(ln)
                    except Exception:
                        continue
                    return f"[reply from {name}]\n" + _unwrap(obj.get("text", ""))
            time.sleep(1)
        return (f"'{name}' received the message but did not reply within "
                f"{self.valves.timeout_seconds}s. A remote interactive session must take a "
                "turn (or approve the peer message) before it can answer.")

    def _self_name_set(self):
        names = {"local", "localhost", _host_short_name()}
        names.update(d.strip().lower() for d in self.valves.self_devices.split(",") if d.strip())
        return names

    def _directory(self):
        out = self._run(["directory", "--json"], 30)
        if out.returncode != 0:
            raise RuntimeError(out.stderr.strip()[-500:] or "communicate directory failed")
        return json.loads(out.stdout)

    def list_agents(self) -> str:
        """List every agent in the communicate registry: name, kind, live/reachable now, dispatchable from here, and what it is for. Call this before choosing where to send work."""
        try:
            rows = self._directory()
        except Exception as e:
            return f"ERROR listing agents: {e}"
        if not rows:
            return "No agents in the registry."
        lines = []
        for r in rows:
            name = r.get("name", "?")
            kind = r.get("kind", "?")
            live = "LIVE" if r.get("live") else "offline"
            dispatchable = (kind == "codex" and bool(r.get("workdir"))) or \
                (kind in ("claude", "claude-code") and bool(r.get("device")))
            summary = ""
            f = r.get("file")
            if f:
                path = os.path.join(os.path.dirname(os.path.dirname(self.valves.communicate_path)), f)
                try:
                    body = open(path, encoding="utf-8").read().split("---", 2)[-1]
                    paras = [p.strip().replace("\n", " ") for p in body.split("\n\n")
                             if p.strip() and not p.strip().startswith("#")]
                    summary = (paras[0][:300]) if paras else ""
                except OSError:
                    pass
            lines.append(
                f"- {name} [{kind}, {live}, "
                f"{'dispatchable via ask_agent' if dispatchable else 'NOT dispatchable from this surface'}]"
                + (f": {summary}" if summary else "")
            )
        return "\n".join(lines)

    def ask_agent(self, name: str, message: str) -> str:
        """Send a self-contained task brief to a specialist agent by name and return its reply. Works for codex agents (synchronous) and remote Claude sessions (bridged in, reply captured via the orchestrator mailbox). The agent does NOT see this chat - include all needed context in the message."""
        try:
            rows = self._directory()
        except Exception as e:
            return f"ERROR: could not read the agent directory: {e}"
        entry = next((r for r in rows if r.get("name") == name), None)
        if entry is None:
            return f"ERROR: no agent named '{name}'. Call list_agents for the roster."
        kind = entry.get("kind")
        if kind in ("claude", "claude-code"):
            return self._ask_claude(name, entry, message)
        if kind != "codex" or not entry.get("workdir"):
            return (f"'{name}' is listed but not dispatchable: kind={kind}, "
                    "no workdir/device to reach it by.")
        device = (entry.get("device") or "local").lower()
        if device in self._self_name_set():
            device = "local"
        args = ["codex", "ask", device, "--dir", entry["workdir"],
                "--thread", f"peer-{name}", "--auto", "--", message]
        try:
            out = self._run(args, self.valves.timeout_seconds)
        except subprocess.TimeoutExpired:
            return (f"TIMEOUT: '{name}' did not reply within {self.valves.timeout_seconds}s. "
                    "For long solves the specialist should submit and return a task id - "
                    "consider re-asking with that instruction.")
        if out.returncode != 0 and not out.stdout.strip():
            return (f"ERROR from '{name}': {out.stderr.strip()[-800:] or 'no output'}\n"
                    "Check `communicate status` in the terminal.")
        return out.stdout.strip()
