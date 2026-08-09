"""
title: Agent Dispatch
author: aadarwal
description: Discover agents on the communicate bus and dispatch work to codex specialists (synchronous request/response).
version: 0.1.0
"""

import json
import os
import socket
import subprocess

from pydantic import BaseModel, Field

REPO_ROOT_HINT = "/Users/aadarwal/src/aadarwal/communicate-owui"


def _host_short_name():
    return socket.gethostname().split(".")[0].lower()


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

    def __init__(self):
        self.valves = self.Valves()

    def _run(self, args, timeout):
        return subprocess.run(
            [self.valves.communicate_path] + args,
            capture_output=True, text=True, timeout=timeout,
        )

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
            dispatchable = kind == "codex" and bool(r.get("workdir"))
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
        """Send a self-contained task brief to a codex specialist agent by name and return its reply. The specialist has its own persistent memory but does NOT see this chat - include all needed context in the message."""
        try:
            rows = self._directory()
        except Exception as e:
            return f"ERROR: could not read the agent directory: {e}"
        entry = next((r for r in rows if r.get("name") == name), None)
        if entry is None:
            return f"ERROR: no agent named '{name}'. Call list_agents for the roster."
        if entry.get("kind") != "codex" or not entry.get("workdir"):
            return (f"'{name}' is listed but not dispatchable from this surface (v1): "
                    f"kind={entry.get('kind')}. It may be reachable from a Claude session instead.")
        device = (entry.get("device") or "local").lower()
        self_names = {"local", "localhost", _host_short_name()}
        self_names.update(d.strip().lower() for d in self.valves.self_devices.split(",") if d.strip())
        if device in self_names:
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
