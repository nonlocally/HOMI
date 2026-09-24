#!/usr/bin/env python3
"""The explicit, reversible, owned schedule for HOMI snapshots.

    homi-snapshot schedule preview|install|uninstall|status
        [--platform darwin|linux] [--home PATH] [--runtime PATH] [--manager PATH] [--brief]

Profile installation never installs a schedule. This command does, on request:
it writes a one-shot launchd agent (macOS) or a systemd user timer (Linux) that
runs `homi-snapshot run` at 03:00, 09:00, 15:00 and 21:00, plus the stable
wrapper ~/.local/bin/homi-snapshot, and loads the job through the service
manager. Ownership boundaries:

- The label is the compatible default only for the actual account home (from
  the password database, not $HOME) with standard config/state roots; any
  other home or root gets a stable scoped label, so an isolated or test
  installation can never reach the account's real job.
- Nothing is loaded, unloaded or stopped unless the manager reports the job
  loaded from this schedule's own file; a job of our label loaded from
  elsewhere is a collision, and every mutation refuses.
- An uninstall with no schedule-owned ledger entries is inert: no manager call.
- An update that fails to activate restores the previous files and, when the
  previous schedule was loaded, loads it again; an identical owned schedule
  that is already loaded is neither rewritten nor restarted.
- --platform selects a manager for mutation only on its native host or with
  an explicit --manager fixture; rendering (preview) is free.
- A wrapper the profile installer owns is used as shared, never retagged and
  never removed here; only a wrapper this schedule created is removed.

Every file is owned through the profile ledger (profiles/manage.py: the same
ownership.json, lock, conflict rules and atomic writes), so `homi profile
status` lists them and nothing unowned is ever replaced. Output is JSON.
Nothing here reads or moves snapshots.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import pwd
import re
import shutil
import subprocess
import sys
import time

sys.dont_write_bytecode = True   # the profile installer is imported from an immutable payload

BASE_LABEL = "com.communicate.homi.snapshots"
BASE_UNIT = "communicate-homi-snapshots"
HOURS = [3, 9, 15, 21]
OWNER = "snapshots-schedule"
HERE = Path(__file__).resolve().parent
NATIVE = "darwin" if sys.platform == "darwin" else "linux"


class Conflict(Exception):
    pass


def account_home():
    """The account's real home, from the password database: $HOME is what a
    shell, a test or a launchd job happens to carry."""
    try:
        return Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()
    except (KeyError, OSError):
        return Path(os.path.expanduser("~")).resolve()


def load_manage(explicit=None):
    """The profile installer, imported for its ownership machinery: beside this
    module in a source tree, the installed HOMI release's copy, or an explicit
    path. Never a second installer, and never bytecode beside it."""
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


class Manager:
    """The service manager, addressed only by our own label/unit, and asked
    which file it loaded that from before anything is loaded or unloaded."""

    def __init__(self, platform, command=None):
        self.platform = platform
        self.explicit = command is not None
        self.command = command or ("launchctl" if platform == "darwin" else "systemctl")

    def available(self):
        return Path(self.command).is_file() or shutil.which(self.command) is not None

    def _run(self, args, tolerate=False):
        try:
            result = subprocess.run([self.command, *args], capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError) as error:
            if tolerate:
                return None
            raise Conflict("%s %s failed: %s" % (self.command, " ".join(args), error))
        if result.returncode != 0 and not tolerate:
            raise Conflict("%s %s failed: %s" % (self.command, " ".join(args),
                                                   (result.stderr or result.stdout).strip()[:300]))
        return result

    def state(self, name):
        """loaded (None when the manager cannot be asked), the file the job was
        loaded from (None when unreported), and on Linux enablement and
        activity separately."""
        if not self.available():
            return {"loaded": None, "path": None}
        if self.platform == "darwin":
            result = self._run(["print", f"gui/{os.getuid()}/{name}"], tolerate=True)
            if result is None or result.returncode != 0:
                return {"loaded": False, "path": None}
            match = re.search(r"^\s*path = (.+?)\s*$", result.stdout, re.M)
            return {"loaded": True, "path": match.group(1) if match else None}
        result = self._run(["--user", "show", "-p", "FragmentPath", "-p", "ActiveState", "-p", "UnitFileState", name],
                           tolerate=True)
        props = dict(line.split("=", 1) for line in (result.stdout if result else "").splitlines() if "=" in line)
        enabled = props.get("UnitFileState", "") in ("enabled", "enabled-runtime")
        active = props.get("ActiveState", "") in ("active", "activating")
        return {"loaded": enabled or active, "path": props.get("FragmentPath") or None,
                "enabled": enabled, "active": active}

    def load(self, name, path):
        if self.platform == "darwin":
            self._run(["bootstrap", f"gui/{os.getuid()}", str(path)])
            self._run(["enable", f"gui/{os.getuid()}/{name}"], tolerate=True)
        else:
            self._run(["--user", "daemon-reload"])
            self._run(["--user", "enable", "--now", name])

    def unload(self, name):
        # Only ever our own label/unit, and only after state() said its file is ours.
        if self.platform == "darwin":
            self._run(["bootout", f"gui/{os.getuid()}/{name}"], tolerate=True)
        else:
            self._run(["--user", "disable", "--now", name], tolerate=True)
            self._run(["--user", "daemon-reload"], tolerate=True)


class Schedule:
    def __init__(self, manage, home, platform, runtime=None, manager=None):
        self.manage = manage
        self.profile = manage.Profile(home)
        self.home = self.profile.home
        self.platform = platform
        self.manager = Manager(platform, manager)
        self._runtime = Path(runtime).resolve() if runtime else None
        self.wrapper = self.home / ".local/bin/homi-snapshot"
        self.log_dir = self.home / "Library/Logs"
        self.scope, tag = self.compute_scope()
        self.label = BASE_LABEL if self.scope == "default" else f"{BASE_LABEL}.{tag}"
        self.unit = BASE_UNIT if self.scope == "default" else f"{BASE_UNIT}-{tag}"
        self.name = self.label if platform == "darwin" else f"{self.unit}.timer"

    def compute_scope(self):
        """"default" only for the actual account home with standard roots;
        otherwise a stable tag from the resolved home and roots."""
        xdg_config = os.environ.get("XDG_CONFIG_HOME")
        xdg_state = os.environ.get("XDG_STATE_HOME")
        standard = (self.home.resolve() == account_home()
                    and (not xdg_config or Path(xdg_config).resolve() == (self.home / ".config").resolve())
                    and (not xdg_state or Path(xdg_state).resolve() == (self.home / ".local/state").resolve()))
        if standard:
            return "default", ""
        key = "\0".join([str(self.home.resolve()),
                         str(Path(xdg_config).resolve()) if xdg_config else "",
                         str(Path(xdg_state).resolve()) if xdg_state else ""])
        return "scoped", hashlib.sha256(key.encode()).hexdigest()[:8]

    @property
    def runtime(self):
        if self._runtime is None:
            payload = self.profile.record.get("payload")
            if payload and (Path(payload) / "runtime/modules/snapshots/homi-snapshot").is_file():
                self._runtime = Path(payload) / "runtime"
            else:
                raise Conflict("no installed workstation profile carries the snapshot module; install the profile "
                               "first (homi profile install --terminal) or pass --runtime PATH")
        return self._runtime

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
            return {"job": self.home / "Library/LaunchAgents" / f"{self.label}.plist"}
        base = Path(os.environ.get("XDG_CONFIG_HOME") or self.home / ".config") / "systemd/user"
        return {"service": base / f"{self.unit}.service", "timer": base / f"{self.unit}.timer"}

    @property
    def job_path(self):
        paths = self.unit_paths()
        return paths["job"] if self.platform == "darwin" else paths["timer"]

    def render(self):
        out = self.unit_paths()
        files = {self.wrapper: (self.wrapper_text(), 0o755)}
        if self.platform == "darwin":
            plist = {
                "Label": self.label,
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
                f"OnCalendar=*-*-* {hours}:00:00\nPersistent=true\nUnit={self.unit}.service\n\n"
                "[Install]\nWantedBy=timers.target\n", 0o644)
        return files

    def plan(self, entries):
        """Each target with the action the ownership rules allow. An existing
        file is replaced only when the ledger owns it with its recorded bytes;
        a wrapper the profile installer owns is shared, never rewritten here."""
        rows = []
        for path, (content, mode) in self.render().items():
            row = {"path": str(path), "mode": mode, "content": content}
            try:
                self.profile.check_parent(path)
                before = self.manage.snapshot(path)
                prev = entries.get(str(path))
                if path == self.wrapper and prev and prev.get("owner") != OWNER:
                    if before["kind"] == "file" and before.get("hash") == prev.get("installed_hash"):
                        row["before"], row["action"] = before, "shared"
                        rows.append(row)
                        continue
                    raise Conflict("the profile-owned wrapper was edited or replaced; repair it with the profile installer")
                if before["kind"] != "absent" and (not prev or before.get("hash") != prev.get("installed_hash")):
                    raise Conflict("existing file is not owned by this schedule; move it aside or uninstall its owner")
                row["before"] = before
                row["action"] = ("unchanged" if before.get("hash") == self.manage.digest(content.encode())
                                 else "update" if prev else "install")
            except (Conflict, self.manage.Conflict, UnicodeError) as error:
                row["action"], row["reason"] = "conflict", str(error)
            rows.append(row)
        return rows

    def loaded_state(self):
        state = self.manager.state(self.name)
        ours = None
        if state.get("path"):
            try:
                ours = Path(state["path"]).resolve() == self.job_path.resolve()
            except OSError:
                ours = False
        state["ours"] = ours
        return state

    def require_mutable(self):
        if self.platform != NATIVE and not self.manager.explicit:
            raise Conflict(f"--platform {self.platform} names a service manager that is not this host's ({NATIVE}); "
                           "preview renders it, and a fixture needs an explicit --manager PATH")
        if not self.manager.available():
            raise Conflict(f"service manager {self.manager.command} is not available; nothing changed")

    def refuse_foreign(self, state, verb):
        if state["loaded"] is None:
            raise Conflict(f"{verb}: the service manager could not be asked which file {self.name} is loaded from; "
                           "refusing to touch it")
        if state["loaded"] and not state["ours"]:
            raise Conflict(f"{verb}: {self.name} is loaded from {state['path'] or 'an unreported path'}, not this "
                           f"schedule's file {self.job_path}; refusing to touch it")

    def files_report(self, rows):
        return [{"path": r["path"], "action": r["action"], **({"reason": r["reason"]} if "reason" in r else {})}
                for r in rows]

    def describe(self):
        out = {"label": self.label, "scope": self.scope, "platform": self.platform, "hours": HOURS,
               "job": str(self.job_path)}
        if self.platform == "linux":
            out["unit"] = self.unit
        return out

    def preview(self):
        rows = self.plan(self.profile.record.get("entries", {}))
        return {"read_only": True, **self.describe(),
                "files": [{k: v for k, v in r.items() if k != "before"} for r in rows],
                "no_automatic_actions": ["profile installation never installs this schedule",
                                         "install takes no snapshot", "the service manager is not consulted by preview"]}

    def _write_ledger(self, record, entries):
        record["entries"] = entries
        record["updated_at"] = time.time()
        self.manage.atomic(self.profile.ledger, (json.dumps(record, indent=2) + "\n").encode())

    def install(self):
        with self.profile.lock():
            record = json.loads(self.profile.ledger.read_text()) if self.profile.ledger.exists() else {"entries": {}}
            entries = dict(record.get("entries", {}))
            entries_before = dict(entries)
            rows = self.plan(entries)
            conflicts = [r for r in rows if r["action"] == "conflict"]
            if conflicts:
                raise Conflict("; ".join(r["path"] + ": " + r["reason"] for r in conflicts))
            self.require_mutable()
            state = self.loaded_state()
            self.refuse_foreign(state, "install")
            previously_loaded = bool(state["loaded"] and state["ours"])
            if previously_loaded and not any(r["action"] in ("install", "update") for r in rows):
                return {"ok": True, "action": "unchanged", **self.describe(), "loaded": True, "restarted": False,
                        "files": self.files_report(rows)}
            if self.platform == "darwin":
                self.log_dir.mkdir(parents=True, exist_ok=True)
            rollback = []

            def restore_files():
                for path, data, mode in reversed(rollback):
                    if data is None:
                        path.unlink(missing_ok=True)
                    else:
                        self.manage.atomic(path, data, mode)

            try:
                for r in rows:
                    if r["action"] == "shared":
                        continue
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
                                          "block": None, "owner": OWNER}
                self._write_ledger(record, entries)
            except Exception:
                restore_files()
                raise
            # The service manager, explicitly and only here: unload our own
            # previous job (verified ours above), load the new file, and check
            # the manager now reports our file. A failure puts the previous
            # files back and, when a previous job was loaded, loads it again.
            try:
                if previously_loaded:
                    self.manager.unload(self.name)
                self.manager.load(self.name, self.job_path)
                after = self.loaded_state()
                if not (after["loaded"] and after["ours"]):
                    raise Conflict("the service manager did not report the schedule loaded from its file")
            except Exception as error:
                now = self.loaded_state()
                if now["loaded"] and now["ours"]:
                    self.manager.unload(self.name)
                restore_files()
                self._write_ledger(record, entries_before)
                if previously_loaded:
                    try:
                        self.manager.load(self.name, self.job_path)
                        back = self.loaded_state()
                        restored = bool(back["loaded"] and back["ours"])
                    except Exception:
                        restored = False
                    note = ("previous files restored and the previous schedule loaded again" if restored
                            else "previous files restored but the previous schedule could NOT be loaded again; "
                                 "run install again or check the service manager")
                else:
                    note = "nothing left installed"
                raise Conflict(f"activation failed: {error}; {note}")
        return {"ok": True, "action": "updated" if previously_loaded else "installed", **self.describe(),
                "loaded": True, "restarted": previously_loaded, "files": self.files_report(rows),
                "note": "no snapshot was taken now; the first runs at the next 03/09/15/21 firing "
                        "(homi-snapshot run takes one immediately)"}

    def uninstall(self):
        with self.profile.lock():
            record = json.loads(self.profile.ledger.read_text()) if self.profile.ledger.exists() else {"entries": {}}
            entries = dict(record.get("entries", {}))
            candidates = list(self.unit_paths().values()) + [self.wrapper]
            mine = [p for p in candidates if entries.get(str(p), {}).get("owner") == OWNER]
            if not mine:
                return {"ok": True, "inert": True, **self.describe(), "removed": [],
                        "note": "no schedule-owned ledger entries for this home; the service manager was not consulted"}
            self.require_mutable()
            state = self.loaded_state()
            self.refuse_foreign(state, "uninstall")
            conflicts, actions = [], []
            for path in mine:
                now = self.manage.snapshot(path)
                if now["kind"] == "absent":
                    actions.append((path, now))
                elif now["kind"] != "file" or now.get("hash") != entries[str(path)]["installed_hash"]:
                    conflicts.append({"path": str(path), "reason": "owned file was replaced or edited; left untouched"})
                else:
                    actions.append((path, now))
            if conflicts:
                return {"ok": False, "conflicts": conflicts, "changed": 0, "unloaded": False}
            if state["loaded"] and state["ours"]:
                self.manager.unload(self.name)
            removed = []
            for path, before in actions:
                if before["kind"] != "absent":
                    if self.manage.snapshot(path) != before:
                        raise Conflict(f"target changed during uninstall: {path}")
                    path.unlink()
                entries.pop(str(path), None)
                removed.append(str(path))
            self._write_ledger(record, entries)
            shared = [str(self.wrapper)] if str(self.wrapper) in entries else []
            after = self.loaded_state()
        return {"ok": True, **self.describe(), "removed": removed, "shared_kept": shared,
                "unloaded": not (after["loaded"] and after["ours"]),
                "preserved": "local snapshots, the log, the archive, private configuration, a profile-owned wrapper"}

    def status(self, brief=False):
        entries = self.profile.record.get("entries", {})
        files = {}
        for path in list(self.unit_paths().values()) + [self.wrapper]:
            now = self.manage.snapshot(path)
            prev = entries.get(str(path))
            files[str(path)] = ("missing" if now["kind"] == "absent" else
                                "unowned" if not prev else
                                "owned-but-edited" if now.get("hash") != prev.get("installed_hash") else
                                "owned" if prev.get("owner") == OWNER else "shared (profile-owned)")
        state = self.loaded_state()
        if brief:
            word = ("unknown" if state["loaded"] is None else
                    "no" if not state["loaded"] else
                    "yes" if state["ours"] else f"foreign {state.get('path') or '?'}")
            return f"{self.name} {word}"
        out = {"read_only": True, **self.describe(), "loaded": state["loaded"], "loaded_path": state.get("path"),
               "ours": state["ours"], "schedule": ", ".join("%02d:00" % h for h in HOURS), "files": files}
        if self.platform == "linux":
            out["enabled"], out["active"] = state.get("enabled"), state.get("active")
        return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["preview", "install", "uninstall", "status"], nargs="?", default="preview")
    ap.add_argument("--platform", choices=["darwin", "linux"], default=NATIVE,
                    help="render for this platform; mutation needs the native host or --manager")
    ap.add_argument("--home", default=str(account_home()),
                    help="installation home; anything but the account home gets a scoped label")
    ap.add_argument("--runtime", help="the installed profile runtime holding modules/snapshots (default: the ledger's payload)")
    ap.add_argument("--manage", help="explicit path to profiles/manage.py")
    ap.add_argument("--manager", help="explicit service-manager executable (a qualification fixture)")
    ap.add_argument("--brief", action="store_true", help="status as one line: <name> yes|no|foreign PATH|unknown")
    args = ap.parse_args(argv)
    try:
        manage = load_manage(args.manage)
        schedule = Schedule(manage, args.home, args.platform, args.runtime, args.manager)
        if args.command == "status":
            result = schedule.status(brief=args.brief)
            print(result if args.brief else json.dumps(result, indent=2))
            return 0
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
