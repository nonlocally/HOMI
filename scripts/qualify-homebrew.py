#!/usr/bin/env python3
"""Qualify HOMI's formula in a temporary keg without replacing installed commands.

Run on a qualification host, passing an extracted artifact's archive and the
rendered canonical formula. Only the candidate formula name, local archive URL,
and keg-only status differ. Uses existing dependencies; never upgrades them.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


def run(argv, *, env=None, timeout=180, quiet=False):
    print("+ " + " ".join(map(str, argv)), flush=True)
    result = subprocess.run(list(map(str, argv)), env=env, text=True, capture_output=True, timeout=timeout)
    if result.stdout and not quiet:
        print(result.stdout, end="", flush=True)
    if result.returncode:
        if result.stderr:
            print(result.stderr, end="", flush=True)
        raise RuntimeError(f"command failed ({result.returncode}): {argv[0]}")
    return result.stdout


def fingerprint(path):
    if path.is_symlink():
        return {"kind": "link", "target": os.readlink(path)}
    if path.is_file():
        return {"kind": "file", "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "mode": path.stat().st_mode}
    return {"kind": "absent"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("formula", type=Path)
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    archive = args.archive.resolve(strict=True)
    args.work.mkdir(parents=True, exist_ok=True)
    report = {"archive": str(archive), "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
              "mode": "renamed keg-only candidate; canonical command names inside keg", "checks": []}
    brew = shutil.which("brew")
    if not brew:
        parser.error("Homebrew is required on the qualification host")
    brew_env = {**os.environ, "HOMEBREW_NO_AUTO_UPDATE": "1", "HOMEBREW_NO_ANALYTICS": "1",
                "HOMEBREW_NO_INSTALL_CLEANUP": "1", "HOMEBREW_NO_INSTALLED_DEPENDENTS_CHECK": "1",
                "HOMEBREW_NO_INSTALL_UPGRADE": "1", "HOMEBREW_NO_ENV_HINTS": "1", "HOMEBREW_NO_ASK": "1"}
    prefix = Path(run([brew, "--prefix"], env=brew_env, quiet=True).strip())
    protected = [prefix / "bin/homi", prefix / "bin/communicate"]
    before = {str(p): fingerprint(p) for p in protected}
    report["protected_before"] = before
    # Refuse to install/upgrade dependencies: this is qualification on an
    # existing workstation. The formula still exercises their real opt paths.
    for dependency in ("node", "python@3.14", "bash"):
        versions = run([brew, "list", "--versions", dependency], env=brew_env, quiet=True).strip()
        if not versions:
            raise RuntimeError(f"preinstall {dependency} before qualifying this host")
    formula_name = "homi-qualification-" + report["sha256"][:10]
    classname = "".join(part.capitalize() for part in formula_name.split("-"))
    formula = args.formula.read_text()
    if "class Homi < Formula" not in formula or '  license "MIT"' not in formula:
        raise RuntimeError("expected the rendered canonical HOMI formula")
    formula = formula.replace("class Homi < Formula", f"class {classname} < Formula", 1)
    formula = re.sub(r'^  url ".*"$', '  url "' + archive.as_uri() + '"', formula, count=1, flags=re.M)
    formula = re.sub(r'^  sha256 ".*"$', '  sha256 "' + report["sha256"] + '"', formula, count=1, flags=re.M)
    formula = formula.replace('  license "MIT"', '  license "MIT"\n  keg_only "temporary isolated HOMI qualification"', 1)
    formula_path = args.work / f"{formula_name}.rb"
    formula_path.write_text(formula)
    if (prefix / "opt" / formula_name).exists() or (prefix / "Cellar" / formula_name).exists():
        raise RuntimeError("candidate keg already exists; inspect it before rerunning qualification")
    installed = False
    temporary = Path(tempfile.mkdtemp(prefix="homi-brew-"))
    env = {**os.environ, "HOME": str(temporary), "COMMUNICATE_DATA": str(temporary / "data"),
           "COMM_STATE": str(temporary / "state"), "HOMI_SOCK_DIR": str(temporary / "socks"),
           "HOMI_SESSIONS_DIR": str(temporary / "sessions"), "HOMI_SELF": "brew-fixture",
           "CODEX_HOME": str(temporary / "codex"), "CLAUDE_CONFIG_DIR": str(temporary / "claude")}
    for key in ("HOMI_SOCK", "HOMI_DAEMON_DIR", "CLAUDE_CODE_MESSAGING_SOCKET", "CODEX_THREAD_ID", "CODEX_SESSION_ID", "COMMUNICATE_HOME"):
        env.pop(key, None)
    stable = temporary / "data/bin/homi"
    try:
        run([brew, "install", "--formula", "--ignore-dependencies", formula_path], env=brew_env)
        installed = True
        keg = prefix / "opt" / formula_name
        entry = keg / "bin/homi"
        run([brew, "test", formula_name], env=brew_env)
        minimal = {**env, "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}
        version = run([entry, "version"], env=minimal)
        assert "HOMI 0.3.0" in version
        assert not (temporary / "data").exists(), "formula install/test unexpectedly ran setup"
        report["checks"].append("formula install/test and wrapper with minimal PATH")
        run([entry, "setup", "--no-clients"], env=env)
        run([stable, "start"], env=env)
        run([stable, "claim", "brew-fixture"], env=env)
        run([stable, "send", "brew-fixture", "--from", "qualification", "--", "brew-reinstall-sentinel"], env=env)
        stored = run([stable, "inbox", "brew-fixture"], env=env)
        assert "brew-reinstall-sentinel" in stored
        run([stable, "stop"], env=env)
        report["checks"].append("explicit isolated setup and durable claim/send/inbox")
        run([brew, "reinstall", "--formula", "--ignore-dependencies", formula_path], env=brew_env)
        run([keg / "bin/communicate", "version"], env=minimal)
        run([stable, "start"], env=env)
        assert "brew-reinstall-sentinel" in run([stable, "inbox", "brew-fixture"], env=env)
        run([stable, "stop"], env=env)
        report["checks"].append("formula reinstall preserves stable installation and mail")
        run([brew, "uninstall", "--formula", formula_name], env=brew_env)
        installed = False
        assert not (prefix / "opt" / formula_name).exists()
        run([stable, "start"], env=env)
        assert "brew-reinstall-sentinel" in run([stable, "inbox", "brew-fixture"], env=env)
        run([stable, "stop"], env=env)
        report["checks"].append("formula uninstall preserves explicit installation and mail")
        after = {str(p): fingerprint(p) for p in protected}
        assert before == after, "pre-existing executable changed"
        report["protected_after"] = after
        report["checks"].append("pre-existing brew executables unchanged")
        report["ok"] = True
    finally:
        if stable.exists():
            subprocess.run([str(stable), "stop"], env=env, capture_output=True, timeout=20)
        if installed:
            run([brew, "uninstall", "--formula", formula_name], env=brew_env)
        shutil.rmtree(temporary)
        (args.work / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print("PASS: Homebrew install, wrappers, reinstall and uninstall with preserved state", flush=True)


if __name__ == "__main__":
    main()
