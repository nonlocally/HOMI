#!/usr/bin/env python3
"""Exercise an actual historical package's installer and migration into HOMI.

PREVIOUS must be an installed/extracted package with dependencies. Model client
CLIs are stateful fixtures; their real-host contracts are qualified separately.
Pass --provenance to distinguish original archives from reconstructed packages.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("previous", type=Path)
    parser.add_argument("runtime", type=Path)
    parser.add_argument("--provenance", required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    previous, runtime = args.previous.resolve(), args.runtime.resolve()
    old_version = json.loads((previous / "package.json").read_text())["version"]
    node = shutil.which("node")
    if not node or not (previous / "src/cli.mjs").is_file():
        parser.error("Node and a historical Communicate package are required")
    home = Path(tempfile.mkdtemp(prefix="homi-legacy-"))
    data, state = home / "data", home / "state"
    env = {**os.environ, "HOME": str(home), "COMMUNICATE_DATA": str(data), "COMM_STATE": str(state),
           "CLAUDE_CONFIG_DIR": str(home / ".claude"), "CODEX_HOME": str(home / ".codex"),
           "HOMI_SELF": "legacy-fixture", "HOMI_SOCK_DIR": str(home / "sockets"),
           "HOMI_SESSIONS_DIR": str(home / "sessions"), "COMM_BUS_PORT": "0",
           "PATH": str(home / "bin") + os.pathsep + os.environ["PATH"]}
    for key in ("HOMI_SOCK", "HOMI_DAEMON_DIR", "CLAUDE_CODE_MESSAGING_SOCKET", "CODEX_THREAD_ID", "CODEX_SESSION_ID", "COMMUNICATE_HOME"):
        env.pop(key, None)
    (home / "bin").mkdir()
    fixture = '''#!/usr/bin/env python3
import json,os,pathlib,sys
h=pathlib.Path(os.environ['HOME']); a=sys.argv[1:]; kind=pathlib.Path(sys.argv[0]).name
with (h/'client-calls').open('a') as f:f.write(json.dumps([kind,*a])+'\\n')
market=h/(kind+'-market'); cached=h/(kind+'-version')
if a==['--version']:print('fixture');sys.exit(0)
if a[:3]==['plugin','marketplace','add']:market.write_text(a[3])
if a[:3]==['plugin','marketplace','remove']:market.unlink(missing_ok=True)
if a[:3]==['plugin','marketplace','list']:
 print(json.dumps([{'name':'communicate','root':market.read_text(),'marketplaceSource':{'source':market.read_text()}}] if market.exists() else []))
if a[:2] in (['plugin','update'],['plugin','install'],['plugin','add']):
 m=pathlib.Path(market.read_text()) if market.exists() else pathlib.Path(os.environ['COMMUNICATE_DATA'])/'current/vendor'
 p=(m/'communicate/.claude-plugin/plugin.json') if kind=='claude' else (m/'plugins/communicate/.claude-plugin/plugin.json')
 cached.write_text(json.loads(p.read_text())['version'])
if a[:2]==['plugin','list']:
 m=pathlib.Path(market.read_text()) if market.exists() else pathlib.Path(os.environ['COMMUNICATE_DATA'])/'current/vendor'
 p=(m/'communicate/.claude-plugin/plugin.json') if kind=='claude' else (m/'plugins/communicate/.claude-plugin/plugin.json')
 version=cached.read_text() if cached.exists() else json.loads(p.read_text())['version']
 print(json.dumps([{'id':'communicate@communicate','scope':'user','version':version}]))
'''
    for name in ("claude", "codex"):
        tool = home / "bin" / name; tool.write_text(fixture); tool.chmod(0o755)
    for name in ("launchctl", "systemctl", "ssh", "gh", "tailscale"):
        tool = home / "bin" / name; tool.write_text("#!/bin/sh\necho 'external tool forbidden in legacy fixture' >&2\nexit 93\n"); tool.chmod(0o755)
    settings = home / ".claude/settings.json"; settings.parent.mkdir()
    settings.write_text(json.dumps({"unrelated": "preserved", "enabledPlugins": {"other@other": True}}))
    report = {"previous_version": old_version, "previous_provenance": args.provenance, "checks": []}
    old_cli, new_cli = previous / "src/cli.mjs", runtime / "src/homi.mjs"
    def run(entry, *arguments, success=True):
        result = subprocess.run([node, str(entry), *arguments], env=env, text=True, capture_output=True, timeout=60)
        if (result.returncode == 0) != success:
            raise RuntimeError(f"{arguments}: {result.stdout}\n{result.stderr}")
        return result.stdout + result.stderr
    try:
        run(old_cli, "setup", "--claude", "--codex")
        old_target = (data / "current").resolve()
        assert old_target.name == old_version and not (old_target / "vendor/release.json").exists()
        run(old_cli, "bus", "create", "migration-fixture")
        assert "migration-fixture" in run(old_cli, "bus", "list", "--json")
        # This is real broker state produced by the historical code. Setup must
        # preserve its credential/database bytes while the broker is stopped.
        run(old_cli, "bus", "stop")
        state_before = {str(p.relative_to(state)): hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in state.rglob('*') if p.is_file() and not p.is_symlink()}
        old_payload = {str(p.relative_to(old_target)): hashlib.sha256(p.read_bytes()).hexdigest()
                       for p in old_target.rglob('*') if p.is_file() and not p.is_symlink()}
        report["checks"].append("historical installer plus real local broker persistence")
        run(new_cli, "setup", "--claude", "--codex")
        new_target = (data / "current").resolve()
        assert new_target != old_target
        for name, digest in state_before.items():
            assert hashlib.sha256((state / name).read_bytes()).hexdigest() == digest, name
        assert "migration-fixture" in run(runtime / "src/cli.mjs", "bus", "list", "--json")
        run(runtime / "src/cli.mjs", "bus", "stop")
        assert json.loads(settings.read_text())["unrelated"] == "preserved"
        report["checks"].append("migration preserves historical broker data, credentials and unrelated client settings")
        ledger_path = data / "install.json"; ledger_bytes = ledger_path.read_text()
        guarded = json.loads(ledger_bytes); guarded["service"] = {"fixture": "do not invoke"}
        ledger_path.write_text(json.dumps(guarded))
        before_calls = (home / "client-calls").read_text()
        refused = run(new_cli, "rollback", success=False)
        assert "No pointer or client changes" in refused
        assert (data / "current").resolve() == new_target and (home / "client-calls").read_text() == before_calls
        ledger_path.write_text(ledger_bytes)
        run(new_cli, "rollback")
        assert (data / "current").resolve() == old_target
        assert old_version in run(data / "current/src/cli.mjs", "version")
        for client in ("claude", "codex"):
            assert (home / (client + '-version')).read_text().split('+')[0] == old_version
        run(new_cli, "doctor")
        for name, digest in old_payload.items():
            assert hashlib.sha256((old_target / name).read_bytes()).hexdigest() == digest, name
        report["checks"].append("legacy rollback refreshes both clients without changing old payload; unsupported service rollback refuses before mutation")
        run(new_cli, "rollback")
        assert (data / "current").resolve() == new_target
        run(new_cli, "uninstall")
        after_settings = json.loads(settings.read_text())
        assert not after_settings.get("extraKnownMarketplaces", {}).get("communicate")
        assert "communicate@communicate" not in after_settings.get("enabledPlugins", {})
        assert not (home / 'claude-market').exists() and not (home / 'codex-market').exists()
        report["checks"].append("forward recovery and complete owned client detach preserve state")
        report["ok"] = True
    finally:
        subprocess.run([node, str(old_cli), "bus", "stop"], env=env, capture_output=True, timeout=20)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + '\n')
        shutil.rmtree(home)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
