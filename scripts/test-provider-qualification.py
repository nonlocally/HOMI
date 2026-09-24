#!/usr/bin/env python3
"""Validate release-proof parsing without contacting a provider."""
import hashlib
import importlib.util
from pathlib import Path
import json
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("provider_gate", Path(__file__).with_name("qualify-provider.py"))
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


class ProofTests(unittest.TestCase):
    def test_assistant_claim_is_not_a_tool_call(self):
        self.assertEqual(gate.tool_calls([{"type": "assistant", "message": {"content": [
            {"type": "text", "text": "I called bus_register and bus_reply successfully"}]}}]), [])

    def test_claude_tool_call_keeps_original_reply_id(self):
        calls = gate.tool_calls([{"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "mcp__plugin_communicate_communicate__bus_reply",
             "input": {"id": "m_original", "message": "literal\n--from"}}]}}])
        self.assertEqual(calls[0][1], {"id": "m_original", "message": "literal\n--from"})

    def test_codex_structured_and_serialized_arguments(self):
        for arguments in ({"id": "m_original"}, '{"id":"m_original"}'):
            calls = gate.tool_calls([{"type": "item.completed", "item": {
                "type": "mcp_tool_call", "tool": "bus_reply", "arguments": arguments}}])
            self.assertEqual(calls, [("bus_reply", {"id": "m_original"})])

    def test_malformed_arguments_do_not_supply_a_reply_id(self):
        self.assertEqual(gate.tool_calls([{"item": {"type": "mcp_tool_call", "tool": "bus_reply",
                                                    "arguments": "not JSON"}}]), [("bus_reply", {})])

    def test_artifact_hash_change_and_checkout_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            path = root / "runtime.txt"
            path.write_text("reviewed")
            manifest = {"source": "fixture", "files": {path.name: hashlib.sha256(path.read_bytes()).hexdigest()}}
            (root / "release.json").write_text(json.dumps(manifest))
            self.assertEqual(gate.artifact(root), manifest)
            path.write_text("changed")
            with self.assertRaisesRegex(RuntimeError, "checksum mismatch"):
                gate.artifact(root)
            (root / ".git").mkdir()
            with self.assertRaisesRegex(RuntimeError, "not a checkout"):
                gate.artifact(root)

    def test_artifact_import_does_not_create_bytecode_or_change_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / "vendor/lib/bus.py"
            source.parent.mkdir(parents=True)
            source.write_text("VALUE = 'fixture'\n")
            manifest = {"source": "fixture", "files": {
                "vendor/lib/bus.py": hashlib.sha256(source.read_bytes()).hexdigest()}}
            (root / "release.json").write_text(json.dumps(manifest))
            before = gate.artifact(root)
            self.assertEqual(gate.import_artifact_bus(root).VALUE, "fixture")
            self.assertEqual(gate.artifact(root), before)
            self.assertFalse((source.parent / "__pycache__").exists())
            (source.parent / "unexpected.pyc").write_bytes(b"unexpected")
            with self.assertRaisesRegex(RuntimeError, "inventory mismatch"):
                gate.artifact(root)


if __name__ == "__main__":
    unittest.main()
