#!/usr/bin/env python3
"""Profile qualification uses disposable homes and a dedicated tmux socket only."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("profiles", ROOT / "profiles/manage.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
BASH = next((p for p in ["/opt/homebrew/bin/bash", "/usr/local/bin/bash", shutil.which("bash")] if p and Path(p).exists()), "bash")


class Profiles(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="homi-profile-")
        self.home = Path(self.temp.name) / "home with spaces"
        self.home.mkdir()
        self.profile = mod.Profile(self.home)
        self.env = dict(os.environ, HOME=str(self.home), PATH=str(self.home / ".local/bin") + ":" + os.environ["PATH"])
        for key in ["TMUX", "TMUX_PANE", "HOMI_PROFILE_CONFIG", "HOMI_PROFILE_STATE", "HOMI_PROFILE_RUNTIME", "HOMI_PROFILE_MODULES", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "BASH_ENV"]:
            self.env.pop(key, None)

    def tearDown(self):
        self.temp.cleanup()

    def install(self, modules=None):
        self.profile.install(modules or ["terminal", "mesh"])
        self.profile = mod.Profile(self.home)

    def run_tool(self, *args, check=True, env=None):
        return subprocess.run(args, env=env or self.env, text=True, capture_output=True, check=check, timeout=15)

    def test_preview_and_migration_are_read_only(self):
        self.assertTrue(self.profile.plan(["terminal"]))
        self.assertTrue(self.profile.migration()["read_only"])
        self.assertEqual(list(self.home.iterdir()), [])

    def test_install_repeat_upgrade_uninstall_preserve_original_and_user_edits(self):
        rc = self.home / ".bashrc"
        rc.write_text("# my original, no trailing newline")
        self.install()
        original_backup = self.profile.record["entries"][str(rc)]["original"]["backup"]
        rc.write_text(rc.read_text() + "# added after install\n")
        self.install()
        self.assertEqual(self.profile.record["entries"][str(rc)]["original"]["backup"], original_backup)
        self.assertEqual(rc.read_text().count(mod.BEGIN), 1)
        private = self.profile.config / "local.sh"
        private.write_text("# private overlay\n")
        data = self.home / ".local/state/homi/workstation/sessions/keep"
        data.mkdir(parents=True)
        self.assertTrue(self.profile.uninstall()["ok"])
        self.assertIn("# my original", rc.read_text())
        self.assertIn("# added after install", rc.read_text())
        self.assertNotIn(mod.BEGIN, rc.read_text())
        self.assertTrue(private.exists())
        self.assertTrue(data.exists())

    def test_uninstall_untouched_file_restores_exact_bytes(self):
        rc = self.home / ".bashrc"
        rc.write_bytes(b"# no newline")
        self.install()
        self.assertTrue(self.profile.uninstall()["ok"])
        self.assertEqual(rc.read_bytes(), b"# no newline")

    def test_legacy_symlink_is_never_followed(self):
        original = Path(self.temp.name) / "legacy-config"
        original.write_text("keep this exactly\n")
        target = self.home / ".bashrc"
        target.symlink_to(original)
        plan = self.profile.plan(["terminal"])
        self.assertTrue(any(p["action"] == "conflict" for p in plan))
        with self.assertRaises(mod.Conflict):
            self.profile.install(["terminal"])
        self.assertTrue(target.is_symlink())
        self.assertEqual(original.read_text(), "keep this exactly\n")

    def test_uninstall_refuses_replacement_and_keeps_entire_profile(self):
        self.install()
        wrapper = self.home / ".local/bin/homi-agent"
        wrapper.write_text("# replacement owned by another installer\n")
        result = self.profile.uninstall()
        self.assertFalse(result["ok"])
        self.assertEqual(result["changed"], 0)
        self.assertTrue((self.home / ".local/bin/homi-workstation").exists())
        self.assertEqual(wrapper.read_text(), "# replacement owned by another installer\n")

    def test_modified_payload_and_modified_block_are_not_overwritten(self):
        self.install()
        payload = Path(self.profile.record["payload"]) / "runtime/init.sh"
        payload.write_text("# changed payload\n")
        with self.assertRaises(mod.Conflict):
            self.install()
        self.assertEqual(payload.read_text(), "# changed payload\n")
        rc = self.home / ".bashrc"
        rc.write_text(rc.read_text().replace("[[ $- != *i* ]]", "# edited\n[[ $- != *i* ]]"))
        self.assertFalse(self.profile.uninstall()["ok"])
        self.assertTrue((self.home / ".local/bin/homi-workstation").exists())

    def test_failed_install_restores_earlier_targets(self):
        from unittest.mock import patch
        rc = self.home / ".bashrc"
        rc.write_text("original\n")
        actual = mod.atomic
        def fail_one(path, data, mode=0o600):
            if str(path).endswith("/.bash_profile"):
                raise OSError("simulated write failure")
            return actual(path, data, mode)
        with patch.object(mod, "atomic", fail_one):
            with self.assertRaises(OSError):
                self.install()
        self.assertEqual(rc.read_text(), "original\n")
        self.assertFalse((self.home / ".local/bin/homi-workstation").exists())
        self.assertFalse(self.profile.ledger.exists())

    def test_installed_helpers_work_without_source_checkout(self):
        self.install()
        result = self.run_tool(str(self.home / ".local/bin/homi-mesh"), "help")
        self.assertIn("HOMI mesh", result.stdout)
        active = self.profile.config / "active.sh"
        result = self.run_tool(BASH, "--noprofile", "--norc", "-c", '. "$1"; declare -F t _t_switch tss tsr al mesh; declare -F anu_landing chat browser || true', "test", str(active))
        self.assertIn("_t_switch", result.stdout)
        self.assertNotIn("anu_landing", result.stdout)
        self.assertNotIn(".local/share/anu", active.read_text())

    def test_failed_uninstall_restores_earlier_targets_and_ownership(self):
        from unittest.mock import patch
        self.install()
        before = {name: Path(name).read_bytes() for name in self.profile.record["entries"]}
        ledger = self.profile.ledger.read_bytes()
        actual = mod.atomic
        failed = False
        def fail_once(path, data, mode=0o600):
            nonlocal failed
            if path == self.profile.ledger and not failed:
                failed = True
                raise OSError("simulated ownership write failure")
            return actual(path, data, mode)
        with patch.object(mod, "atomic", fail_once):
            with self.assertRaises(OSError):
                self.profile.uninstall()
        self.assertEqual(self.profile.ledger.read_bytes(), ledger)
        self.assertEqual({name: Path(name).read_bytes() for name in before}, before)

    def test_native_launch_and_optional_account_adapter_preserve_argv(self):
        self.install()
        stub = self.home / ".local/bin/claude"
        stub.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$@"\n')
        stub.chmod(0o755)
        result = self.run_tool(str(self.home / ".local/bin/homi-agent"), "cxx", "literal $value\nnext")
        self.assertEqual(result.stdout, "--dangerously-skip-permissions\nliteral $value\nnext\n")
        result = self.run_tool(str(self.home / ".local/bin/homi-agent"), "cxc", check=False)
        self.assertNotEqual(result.returncode, 0)
        adapter = self.home / "account adapter"
        adapter.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$@"\n')
        adapter.chmod(0o755)
        (self.profile.config / "local.sh").write_text("export HOMI_ACCOUNT_LAUNCHER=" + mod.quote(adapter) + "\n")
        result = self.run_tool(str(self.home / ".local/bin/homi-agent"), "cdxx", "message")
        self.assertEqual(result.stdout, "launch\n--provider\ncodex\n--\n--yolo\nmessage\n")

    @unittest.skipUnless(shutil.which("jq"), "jq unavailable")
    def test_manual_mesh_hosts_and_ssh_export_preserve_ssh_config(self):
        self.install(["mesh"])
        mesh = str(self.home / ".local/bin/homi-mesh")
        self.run_tool(mesh, "host", "add", "lab", "scientist@lab.example", "2222")
        self.assertIn("lab", self.run_tool(mesh, "host", "list").stdout)
        sshdir = self.home / ".ssh"
        sshdir.mkdir()
        config = sshdir / "config"
        config.write_text("Host mine\n  HostName untouched\n")
        self.run_tool(mesh, "sshconfig")
        self.assertEqual(config.read_text(), "Host mine\n  HostName untouched\n")
        generated = self.profile.config / "mesh/ssh.conf"
        self.assertIn("lab.example", generated.read_text())
        self.assertIn("2222", generated.read_text())
        # Manual routes never need a Tailscale lookup or real SSH connection.
        stub = self.home / ".local/bin/ssh"
        stub.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$@"\n')
        stub.chmod(0o755)
        tail = self.home / ".local/bin/tailscale"
        tail.write_text('#!/usr/bin/env bash\necho UNEXPECTED_TAILSCALE >&2; exit 99\n')
        tail.chmod(0o755)
        result = self.run_tool(mesh, "ssh", "lab")
        self.assertIn("scientist@lab.example", result.stdout)
        self.assertNotIn("UNEXPECTED", result.stderr)
        result = self.run_tool(mesh, "run", "lab", "printf '%s' '$literal'")
        self.assertIn("printf '%s' '$literal'", result.stdout)
        self.assertNotIn("UNEXPECTED", result.stderr)
        # The terminal helper produces shell syntax only for deliberate commands;
        # execution here reaches our SSH stub and preserves the exact argument.
        result = self.run_tool(BASH, "--noprofile", "--norc", "-c",
                               '. "$1"; line=$(_mesh_terminal_command lab "$2") || exit; bash -c "$line"',
                               "test", str(self.profile.config / "active.sh"), "printf '%s' '$literal'")
        self.assertEqual(result.stdout, "-t\n-p\n2222\n--\nscientist@lab.example\nprintf '%s' '$literal'\n")
        self.assertNotIn("UNEXPECTED", result.stderr)
        result = self.run_tool(mesh, "host", "add", "bad", "u@$(touch bad)", check=False)
        self.assertNotEqual(result.returncode, 0)

    @unittest.skipUnless(shutil.which("tmux"), "tmux unavailable")
    def test_isolated_tmux_profile_navigation_and_snapshots(self):
        self.install()
        # Never uses the user's default server. Environment and socket are private.
        socket = str(Path(self.temp.name) / "tmux.sock")
        tm = [shutil.which("tmux"), "-S", socket]
        cfg = str(self.profile.config / "tmux.conf")
        clients = []
        try:
            self.run_tool(*tm, "-f", cfg, "new-session", "-d", "-s", "fixture", "-x", "180", "-y", "60", BASH + " --noprofile --norc")
            self.run_tool(*tm, "source-file", cfg)
            pane = self.run_tool(*tm, "display-message", "-p", "-t", "fixture", "#{pane_id}").stdout.strip()
            env = dict(self.env, TMUX=socket + ",0,0", TMUX_PANE=pane)
            wrapper = str(self.home / ".local/bin/homi-workstation")
            self.run_tool(wrapper, "tile", "new", env=env)
            self.assertEqual(len(self.run_tool(*tm, "list-panes", "-t", "fixture").stdout.splitlines()), 2)
            self.run_tool(wrapper, "shell", "tss", "proof", env=env)
            self.assertTrue((self.home / ".local/state/homi/workstation/sessions/proof/windows.tsv").exists())
            self.assertIn("fixture", self.run_tool(wrapper, "shell", "tsr", "-n", "proof", env=env).stdout)
            result = self.run_tool(wrapper, "shell", "tss", "../escape", check=False, env=env)
            self.assertNotEqual(result.returncode, 0)
            keys = self.run_tool(*tm, "list-keys").stdout
            self.assertNotIn(".local/share/anu", keys)
            self.assertNotIn("browser", keys)
            hooks = self.run_tool(*tm, "show-hooks", "-g").stdout
            self.assertEqual(hooks.count("after-new-window[0]"), 1)
            # Two control clients are enough to exercise tmux's real per-client
            # session/window pointers without opening a terminal or a live agent.
            self.run_tool(*tm, "new-window", "-d", "-t", "fixture", "-n", "second")
            for _ in range(2):
                clients.append(subprocess.Popen(tm + ["-C", "attach-session", "-t", "fixture"],
                                                env=self.env, stdin=subprocess.PIPE,
                                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            deadline = time.monotonic() + 3
            rows = []
            while time.monotonic() < deadline:
                rows = self.run_tool(*tm, "list-clients", "-F", "#{client_name}").stdout.splitlines()
                if len(rows) == 2:
                    break
                time.sleep(0.02)
            self.assertEqual(len(rows), 2)
            for client in rows:
                self.run_tool(BASH, "--noprofile", "--norc", "-c", '. "$1"; _t_switch fixture "$2"',
                              "test", str(self.profile.config / "active.sh"), client, env=env)
            attached = self.run_tool(*tm, "list-clients", "-F", "#{client_session}").stdout.splitlines()
            self.assertEqual(len(set(attached)), 2)
            self.assertTrue(all(s == "fixture" or s.startswith("fixture~") for s in attached))
            self.run_tool(*tm, "select-window", "-t", attached[0] + ":2")
            current = self.run_tool(*tm, "list-clients", "-F", "#{window_index}").stdout.splitlines()
            self.assertEqual(sorted(current), ["1", "2"])
        finally:
            for client in clients:
                client.terminate()
                client.wait(timeout=3)
                client.stdin.close()
            subprocess.run(tm + ["kill-server"], env=self.env, capture_output=True)


if __name__ == "__main__":
    unittest.main()
