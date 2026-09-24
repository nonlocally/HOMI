#!/usr/bin/env python3
"""Verify public dispatch preserves identity namespaces and literal arguments."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class EntrypointTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="homi-entry-")
        self.root = Path(self.temp.name) / "payload with spaces"
        (self.root / "bin").mkdir(parents=True)
        shutil.copy2(ROOT / "bin/homi", self.root / "bin/homi")
        self.cli = self.root / "bin/homi"
        self.cli.chmod(0o755)
        native = self.root / "bin/communicate"
        native.write_text("#!/usr/bin/env python3\nimport json,sys\nprint(json.dumps(sys.argv[1:]))\n")
        native.chmod(0o755)

    def tearDown(self):
        self.temp.cleanup()

    def run_cli(self, *args):
        return subprocess.run([str(self.cli), *args], capture_output=True, text=True,
                              env={**os.environ, "HOMI_PACKAGE_CLI": ""})

    def test_durable_target_and_message_are_literal(self):
        message = 'quotes " dollar $HOME backticks `id`\nsecond line'
        result = self.run_cli("send", "agent one", message)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), ["homi", "send", "agent one", message])

    def test_explicit_namespaces(self):
        for args, expected in [
            (("bus", "agents", "--json"), ["bus", "agents", "--json"]),
            (("native", "route", "worker", "hello"), ["route", "worker", "hello"]),
            (("daemon", "uninstall"), ["homi", "uninstall"]),
        ]:
            with self.subTest(args=args):
                result = self.run_cli(*args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), expected)

    def test_symlink_resolves_payload(self):
        link = Path(self.temp.name) / "homi"
        link.symlink_to(self.cli)
        result = subprocess.run([str(link), "claim", "worker"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), ["homi", "claim", "worker"])

    def test_missing_profile_is_honest(self):
        result = self.run_cli("profile", "status")
        self.assertEqual(result.returncode, 127)
        self.assertIn("unavailable", result.stderr)

    def test_help_needs_no_runtime(self):
        result = self.run_cli("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("homi bus", result.stdout)


if __name__ == "__main__":
    unittest.main()
