"""
title: Agent Dispatch
author: aadarwal
description: Discover agents on the homi roster and dispatch work to them by mail — one durable ask/reply round trip per specialist, local or across devices.
version: 0.3.0
"""

import json
import os
import re
import subprocess

from pydantic import BaseModel, Field

REPO_ROOT_HINT = "/Users/aadarwal/src/aadarwal/communicate"
_WRAP = re.compile(r"<cross-session-message[^>]*>\s*(.*?)\s*</cross-session-message>",
                   re.DOTALL)


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
        caller_name: str = Field(
            default="orchestrator",
            description="The homi identity replies are addressed to (this surface's own name on the bus)",
        )

    def __init__(self):
        self.valves = self.Valves()

    def _run(self, args, timeout, env=None):
        return subprocess.run(
            [self.valves.communicate_path] + args,
            capture_output=True, text=True, timeout=timeout, env=env,
        )

    def _homi_json(self, args, timeout):
        """Run a `communicate homi …` subcommand that speaks --json and return
        the parsed object. NEVER raises: a missing CLI, a timeout, a crash or
        unparseable output all come back as {"ok": False, "err": …} so the
        model gets a sentence it can act on instead of a stack trace."""
        label = " ".join(args[:2])
        try:
            out = self._run(args, timeout)
        except subprocess.TimeoutExpired:
            return {"ok": False,
                    "err": "`%s` did not return within %ss" % (label, timeout)}
        except OSError as e:
            return {"ok": False, "err": "cannot run %s: %s"
                                        % (self.valves.communicate_path, e)}
        text = (out.stdout or "").strip()
        if not text:
            return {"ok": False, "err": "`%s` produced no output (exit %s): %s"
                    % (label, out.returncode, (out.stderr or "").strip()[-300:])}
        try:
            obj = json.loads(text)
        except ValueError:
            return {"ok": False, "err": "`%s` returned unparseable output: %s"
                                        % (label, text[:300])}
        if not isinstance(obj, dict):
            return {"ok": False, "err": "`%s` returned %s, expected an object"
                                        % (label, type(obj).__name__)}
        if out.returncode != 0 and obj.get("ok") is None:
            # A non-zero exit with valid JSON that never said ok: trust the exit.
            obj = dict(obj, ok=False,
                       err=obj.get("err") or (out.stderr or "").strip()[-300:]
                       or "exit %s" % out.returncode)
        return obj

    def _directory(self):
        """Agents on the homi roster, with their MEASURED liveness. Returns
        (rows, err) — the old registry/*.md read is gone; homi's roster is the
        source of truth, and `state` is the field it actually measures."""
        data = self._homi_json(["homi", "agents", "--json"], 30)
        if not data.get("ok"):
            return [], (data.get("err") or "communicate homi agents failed")
        rows = []
        for a in data.get("agents", []):
            card = a.get("card") or {}
            surface = a.get("surface") or {}
            rows.append({
                "name": a.get("name"),
                "kind": a.get("kind"),                # local | proxy (routing)
                "device": a.get("home") or data.get("device"),
                "state": a.get("state"),              # live | stored | None
                "seat": surface.get("state"),         # the pane, if it has one
                "undelivered": a.get("undelivered", 0),
                "what": card.get("what", ""),
            })
        return rows, None

    @staticmethod
    def _dispatchable(row):
        """Measured, never assumed. Dispatchable NOW means homi just measured a
        session answering on this identity's socket. Everything else on the
        roster is still MAILABLE (homi stores and forwards), but nobody is
        listening — promising a reply would be advertising what we have not
        measured."""
        return row.get("state") == "live"

    @staticmethod
    def _reach(row):
        if row.get("state") == "live":
            return "LIVE, dispatchable via ask_agent"
        if row.get("kind") == "proxy":
            return ("remote on %s, liveness not measurable from here; mail is "
                    "held for it" % (row.get("device") or "?"))
        return "not live now; mail is stored until it runs"

    def list_agents(self) -> str:
        """List every agent on the homi roster: name, where it lives, whether homi just measured it live (and so dispatchable now), and what it is for. Call this before choosing where to send work."""
        rows, err = self._directory()
        if err:
            return f"ERROR listing agents: {err}"
        if not rows:
            return "No agents in the homi roster."
        lines = []
        for r in rows:
            extra = ""
            if r.get("seat"):
                extra += f", seat {r['seat']}"
            if r.get("undelivered"):
                extra += f", {r['undelivered']} unread"
            summary = r.get("what") or ""
            lines.append(
                f"- {r.get('name', '?')} [{r.get('kind', '?')}, {self._reach(r)}{extra}]"
                + (f": {summary}" if summary else "")
            )
        return "\n".join(lines)

    def ask_agent(self, name: str, message: str) -> str:
        """Send a self-contained task brief to an agent by name and return its reply. The ask travels as durable homi mail — same round trip for a local specialist and one on another device — so a message is never lost even when the agent is not live. The agent does NOT see this chat: include all needed context in the message."""
        rows, err = self._directory()
        if err:
            return f"ERROR: could not read the agent directory: {err}"
        entry = next((r for r in rows if r.get("name") == name), None)
        if entry is None:
            return f"ERROR: no agent named '{name}'. Call list_agents for the roster."
        timeout = int(self.valves.timeout_seconds)
        r = self._homi_json(
            ["homi", "ask", name, message,
             "--from", self.valves.caller_name,
             "--timeout", str(timeout), "--json"],
            timeout + 30,
        )
        if r.get("ok"):
            return f"[reply from {r.get('from') or name}]\n" + _unwrap(r.get("reply") or "")
        e = str(r.get("err") or "failed")
        if "timeout" in e.lower():
            why = ("" if self._dispatchable(entry)
                   else " It was not measured LIVE when asked (%s)." % self._reach(entry))
            return (f"'{name}' did not reply within {timeout}s.{why} The message is "
                    f"durably stored in its mailbox — homi holds it until the agent "
                    f"next runs, so do NOT resend blindly; ask again later, or pick "
                    f"an agent list_agents shows as LIVE.")
        return f"ERROR asking '{name}': {e}"
