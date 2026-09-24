#!/usr/bin/env python3
"""The explicit, reversible, owned schedule for HOMI snapshots.

    homi-snapshot schedule preview|install|uninstall|status
                           [--platform darwin|linux] [--home PATH] [--runtime PATH]

Profile installation never installs a schedule. This command does, on request:
it writes a one-shot launchd agent (macOS) or a systemd user timer (Linux) that
runs `homi-snapshot run` at 03:00, 09:00, 15:00 and 21:00, plus the stable
wrapper ~/.local/bin/homi-snapshot, and loads the job through the service
manager. Every file is owned through the profile ledger (profiles/manage.py:
the same ownership.json, lock, conflict rules and atomic writes), so
`homi profile status` lists them and nothing unowned is ever replaced.
Uninstall unloads the job first, then removes only unedited owned files.
Output is JSON. Nothing here reads or moves snapshots.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import time

LABEL = "com.communicate.homi.snapshots"
UNIT = "communicate-homi-snapshots"
HOURS = [3, 9, 15, 21]
HERE = Path(__file__).resolve().parent


class Conflict(Exception):
    pass


def load_manage(explicit=None):
    """The profile installer, imported for its ownership machinery: beside this
    module in a source tree, the installed HOMI release's copy, or an explicit
    path. Never a second installer."""
    candidates = [Path(p) for p in [explicit, os.environ.get("HOMI_PROFILES_MANAGE")] if p]
    candidates.append(HERE.parents[2] / "manage.py")
    data = Path(os.environ.get("COMMUNICATE_DATA") or Path.home() / ".local/share/communicate")
    candidates.append(data / "current/vendor/profiles/manage.py")
    for path in candidates:
        if path.is_file():
            spec = importlib.util.spec_from_file_location("homi_profiles_manage", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    raise Conflict("profile installer (profiles/manage.py) not found; run from a HOMI release or pass --manage PATH")


def quote_unit(value):
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'


class Schedule:
    def __init__(self, manage, home, platform, runtime=None):
        self.manage = manage
        self.profile = manage.Profile(home)
        self.home = self.profile.home
        self.platform = platform
        self.runtime = Path(runtime).resolve() if runtime else self.installed_runtime()
        self.wrapper = self.home / ".local/bin/homi-snapshot"
        self.log_dir = self.home / "Library/Logs"

    def installed_runtime(self):
        payload = self.profile.record.get("payload")
        if payload and (Path(payload) / "runtime/modules/snapshots/homi-snapshot").is_file():
            return Path(payload) / "runtime"
        raise Conflict("no installed workstation profile carries the snapshot module; install the profile first "
                       "(homi profile install --terminal) or pass --runtime PATH")

    def wrapper_text(self):
        active = self.profile.config / "active.sh"
        return ("#!/usr/bin/env bash\n"
                "# HOMI snapshots. Owned by the profile ledger (homi-snapshot schedule install); do not edit.\n"
                'if [ "${BASH_VERSINFO[0]:-0}" -lt 4 ]; then\n'
                "  for b in /opt/homebrew/bin/bash /usr/local/bin/bash; do\n"
                '    [ ! -x "$b" ] || exec "$b" "$0" "$@"\n'
                "  done\n"
                '  echo "HOMI snapshots need Bash 4+" >&2; exit 1\n'
                "fi\n"
                f"[ ! -f {self.manage.quote(active)} ] || . {self.manage.quote(active)}\n"
                f'exec "${{HOMI_PROFILE_RUNTIME:-{self.runtime}}}/modules/snapshots/homi-snapshot" "$@"\n')

    def unit_paths(self):
        if self.platform == "darwin":
            return {"job": self.home / "Library/LaunchAgents" / f"{LABEL}.plist"}
        base = Path(os.environ.get("XDG_CONFIG_HOME") or self.home / ".config") / "systemd/user"
        return {"service": base / f"{UNIT}.service", "timer": base / f"{UNIT}.timer"}

    def render(self):
        out = self.unit_paths()
        files = {self.wrapper: (self.wrapper_text(), 0o755)}
        if self.platform == "darwin":
            plist = {
                "Label": LABEL,
                "ProgramArguments": [str(self.wrapper), "run"],
                "EnvironmentVariables": {
                    "HOME": str(self.home),
                    "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
                    # The interactive tmux server's default socket dir, and the
                    # UTF-8 locale tss needs for tab-separated tmux formats.
                    "TMUX_TMPDIR": "/tmp",
                    "LANG": "en_US.UTF-8",
                    "LC_ALL": "en_US.UTF-8",
                },
                # Calendar firings are wall-clock anchored (no drift across
                # sleeps and reloads); a missed one runs once on the next wake.
                # A one-shot backup, not a daemon: no KeepAlive, no RunAtLoad.
                "StartCalendarInterval": [{"Hour": h, "Minute": 0} for h in HOURS],
                "ProcessType": "Background",
                "StandardOutPath": str(self.log_dir / "homi-snapshot.out.log"),
                "StandardErrorPath": str(self.log_dir / "homi-snapshot.err.log"),
            }
            files[out["job"]] = (plistlib.dumps(plist, sort_keys=False).decode(), 0o644)
        else:
            files[out["service"]] = (
                "[Unit]\nDescription=HOMI workspace snapshot\n\n[Service]\nType=oneshot\n"
                f"ExecStart={quote_unit(self.wrapper)} run\n"
                "Environment=LANG=en_US.UTF-8\nEnvironment=LC_ALL=en_US.UTF-8\nEnvironment=TMUX_TMPDIR=/tmp\n", 0o644)
            hours = ",".join("%02d" % h for h in HOURS)
            files[out["timer"]] = (
                "[Unit]\nDescription=HOMI workspace snapshot every 6 hours\n\n[Timer]\n"
                f"OnCalendar=*-*-* {hours}:00:00\nPersistent=true\nUnit={UNIT}.service\n\n"
                "[Install]\nWantedBy=timers.target\n", 0o644)
        return files

    def plan(self, entries):
        """Each target with the action the ownership rules allow: an existing
        file is only ever replaced when the ledger owns it and its bytes are
        still the ones recorded."""
        rows = []
        for path, (content, mode) in self.render().items():
            row = {"path": str(path), "mode": mode, "content": content}
            try:
                self.profile.check_parent(path)
                before = self.manage.snapshot(path)
                prev = entries.get(str(path))
                if before["kind"] != "absent" and (not prev or before.get("hash") != prev.get("installed_hash")):
                    raise Conflict("existing file is not owned by this schedule; move it aside or uninstall its owner")
                row["before"] = before
                row["action"] = ("unchanged" if before.get("hash") == self.manage.digest(content.encode())
                                 else "update" if prev else "install")
            except (Conflict, self.manage.Conflict, UnicodeError) as error:
                row["action"], row["reason"] = "conflict", str(error)
            rows.append(row)
        return rows

    def load_commands(self):
        if self.platform == "darwin":
            job = self.unit_paths()["job"]
            return [["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"],
                    ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(job)],
                    ["launchctl", "enable", f"gui/{os.getuid()}/{LABEL}"]]
        return [["systemctl", "--user", "daemon-reload"], ["systemctl", "--user", "enable", "--now", f"{UNIT}.timer"]]

    def unload_commands(self):
        if self.platform == "darwin":
            return [["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"]]
        return [["systemctl", "--user", "disable", "--now", f"{UNIT}.timer"], ["systemctl", "--user", "daemon-reload"]]

    def loaded(self):
        if self.platform == "darwin":
            cmd = ["launchctl", "print", f"gui/{os.getuid()}/{LABEL}"]
        else:
            cmd = ["systemctl", "--user", "is-active", f"{UNIT}.timer"]
        if not shutil.which(cmd[0]):
            return None
        return subprocess.run(cmd, capture_output=True, text=True, timeout=15).returncode == 0

    def run(self, cmd, tolerate=False):
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0 and not tolerate:
            raise Conflict("%s failed: %s" % (" ".join(cmd), (result.stderr or result.stdout).strip()[:300]))
        return result.returncode == 0

    def preview(self):
        rows = self.plan(self.profile.record.get("entries", {}))
        return {"read_only": True, "platform": self.platform, "label": LABEL, "hours": HOURS,
                "files": [{k: v for k, v in r.items() if k != "before"} for r in rows],
                "load": [" ".join(c) for c in self.load_commands()],
                "no_automatic_actions": ["profile installation never installs this schedule",
                                         "no snapshot is taken by install"]}

    def install(self):
        with self.profile.lock():
            record = json.loads(self.profile.ledger.read_text()) if self.profile.ledger.exists() else {"entries": {}}
            entries = dict(record.get("entries", {}))
            record_entries_before = dict(entries)
            rows = self.plan(entries)
            conflicts = [r for r in rows if r["action"] == "conflict"]
            if conflicts:
                raise Conflict("; ".join(r["path"] + ": " + r["reason"] for r in conflicts))
            if self.platform == "darwin":
                self.log_dir.mkdir(parents=True, exist_ok=True)
            rollback = []
            try:
                for r in rows:
                    path = Path(r["path"])
                    if self.manage.snapshot(path) != r["before"]:
                        raise Conflict(f"changed since preview: {path}")
                    rollback.append((path, path.read_bytes() if path.exists() else None,
                                     r["before"].get("mode", r["mode"])))
                    if r["action"] != "unchanged":
                        self.manage.atomic(path, r["content"].encode(), r["before"].get("mode", r["mode"]))
                    entries[str(path)] = {"kind": "file",
                                          "original": entries[str(path)]["original"] if entries.get(str(path))
                                          else {"kind": "absent"},
                                          "installed_hash": self.manage.digest(r["content"].encode()),
                                          "block": None, "owner": "snapshots-schedule"}
                record["entries"] = entries
                record["updated_at"] = time.time()
                self.manage.atomic(self.profile.ledger, (json.dumps(record, indent=2) + "\n").encode())
            except Exception:
                for path, data, mode in reversed(rollback):
                    if data is None:
                        path.unlink(missing_ok=True)
                    else:
                        self.manage.atomic(path, data, mode)
                raise
            # The service manager, explicitly and only here. A load that fails
            # leaves nothing behind: the files and their ledger entries go back
            # to what they were, so install is reversible as a whole.
            try:
                commands = self.load_commands()
                self.run(commands[0], tolerate=True)          # a previous load of ours, if any
                for cmd in commands[1:]:
                    self.run(cmd, tolerate=cmd[1] == "enable")
                loaded = self.loaded()
                if loaded is False:
                    raise Conflict("the service manager did not report the schedule loaded; nothing was left installed")
            except Exception:
                for cmd in self.unload_commands():
                    self.run(cmd, tolerate=True)
                for path, data, mode in reversed(rollback):
                    if data is None:
                        path.unlink(missing_ok=True)
                    else:
                        self.manage.atomic(path, data, mode)
                record["entries"] = dict(record_entries_before)
                self.manage.atomic(self.profile.ledger, (json.dumps(record, indent=2) + "\n").encode())
                raise
        return {"ok": True, "label": LABEL, "platform": self.platform, "hours": HOURS,
                "files": [{"path": r["path"], "action": r["action"]} for r in rows], "loaded": loaded,
                "note": "no snapshot was taken now; the first runs at the next 03/09/15/21 firing "
                        "(homi-snapshot run takes one immediately)"}

    def uninstall(self):
        with self.profile.lock():
            record = json.loads(self.profile.ledger.read_text()) if self.profile.ledger.exists() else {"entries": {}}
            entries = dict(record.get("entries", {}))
            mine = [Path(p) for p in list(self.unit_paths().values()) + [self.wrapper] if str(p) in entries]
            conflicts, actions = [], []
            for path in mine:
                now = self.manage.snapshot(path)
                if now["kind"] == "absent":
                    actions.append((path, now))
                    continue
                if now["kind"] != "file" or now.get("hash") != entries[str(path)]["installed_hash"]:
                    conflicts.append({"path": str(path), "reason": "owned file was replaced or edited; left untouched"})
                else:
                    actions.append((path, now))
            if conflicts:
                return {"ok": False, "conflicts": conflicts, "changed": 0}
            for cmd in self.unload_commands():
                self.run(cmd, tolerate=True)
            removed = []
            for path, before in actions:
                if before["kind"] != "absent":
                    if self.manage.snapshot(path) != before:
                        raise Conflict(f"target changed during uninstall: {path}")
                    path.unlink()
                entries.pop(str(path), None)
                removed.append(str(path))
            record["entries"] = entries
            record["updated_at"] = time.time()
            self.manage.atomic(self.profile.ledger, (json.dumps(record, indent=2) + "\n").encode())
        return {"ok": True, "label": LABEL, "removed": removed, "unloaded": self.loaded() in (False, None),
                "preserved": "local snapshots, the log, the archive, private configuration"}

    def status(self):
        entries = self.profile.record.get("entries", {})
        files = {}
        for path in list(self.unit_paths().values()) + [self.wrapper]:
            now = self.manage.snapshot(path)
            prev = entries.get(str(path))
            files[str(path)] = ("missing" if now["kind"] == "absent" else
                                "owned" if prev and now.get("hash") == prev.get("installed_hash") else
                                "owned-but-edited" if prev else "unowned")
        return {"read_only": True, "label": LABEL, "platform": self.platform, "loaded": self.loaded(),
                "schedule": ", ".join("%02d:00" % h for h in HOURS), "files": files}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["preview", "install", "uninstall", "status"], nargs="?", default="preview")
    ap.add_argument("--platform", choices=["darwin", "linux"], default="darwin" if sys.platform == "darwin" else "linux")
    ap.add_argument("--home", default=str(Path.home()), help="installation home; use an isolated home for qualification")
    ap.add_argument("--runtime", help="the installed profile runtime holding modules/snapshots (default: the ledger's payload)")
    ap.add_argument("--manage", help="explicit path to profiles/manage.py")
    args = ap.parse_args(argv)
    try:
        manage = load_manage(args.manage)
        schedule = Schedule(manage, args.home, args.platform, args.runtime)
        result = getattr(schedule, args.command)()
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok", True) else 1
    except (Conflict, OSError, ValueError, subprocess.SubprocessError) as error:
        print(json.dumps({"ok": False, "error": str(error)}), file=sys.stderr)
        return 1
    except Exception as error:  # a Conflict raised by the imported installer
        if type(error).__name__ == "Conflict":
            print(json.dumps({"ok": False, "error": str(error)}), file=sys.stderr)
            return 1
        raise


if __name__ == "__main__":
    sys.exit(main())
