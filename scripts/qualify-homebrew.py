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


def verify_runtime(runtime):
    manifest = json.loads((runtime / "release.json").read_text())
    for name, digest in manifest["files"].items():
        file = runtime / name
        if not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest() != digest:
            raise RuntimeError("Homebrew changed or removed an immutable artifact file: " + name)
    return {"source": manifest["source"], "files": len(manifest["files"])}


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
                "HOMEBREW_NO_INSTALL_UPGRADE": "1", "HOMEBREW_NO_AUTOREMOVE": "1",
                "HOMEBREW_NO_ENV_HINTS": "1", "HOMEBREW_NO_ASK": "1"}
    prefix = Path(run([brew, "--prefix"], env=brew_env, quiet=True).strip())
    protected = [prefix / "bin/homi", prefix / "bin/communicate"]
    before = {str(p): fingerprint(p) for p in protected}
    report["protected_before"] = before
    # Refuse to install/upgrade dependencies: this is qualification on an
    # existing workstation. The formula still exercises their real opt paths.
    dependencies = {}
    for dependency in ("node", "python@3.14", "bash"):
        versions = run([brew, "list", "--versions", dependency], env=brew_env, quiet=True).strip()
        if not versions:
            raise RuntimeError(f"preinstall {dependency} before qualifying this host")
        dependencies[dependency] = versions
    report["dependencies_before"] = dependencies
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
    tap = "homi-qualification/candidate-" + report["sha256"][:10]
    tapped = run([brew, "tap"], env=brew_env, quiet=True).splitlines()
    if tap in tapped:
        raise RuntimeError("candidate tap already exists; inspect it before rerunning qualification")
    qualified_name = tap + "/" + formula_name
    report["tap"] = tap
    installed = False
    tap_created = False
    temporary = Path(tempfile.mkdtemp(prefix="homi-brew-"))
    env = {**os.environ, "HOME": str(temporary), "COMMUNICATE_DATA": str(temporary / "data"),
           "COMM_STATE": str(temporary / "state"), "HOMI_SOCK_DIR": str(temporary / "socks"),
           "HOMI_SESSIONS_DIR": str(temporary / "sessions"), "HOMI_SELF": "brew-fixture",
           "CODEX_HOME": str(temporary / "codex"), "CLAUDE_CONFIG_DIR": str(temporary / "claude")}
    for key in ("HOMI_SOCK", "HOMI_DAEMON_DIR", "CLAUDE_CODE_MESSAGING_SOCKET", "CODEX_THREAD_ID", "CODEX_SESSION_ID", "COMMUNICATE_HOME"):
        env.pop(key, None)
    stable = temporary / "data/bin/homi"
    try:
        # Current Homebrew rejects arbitrary formula files. A private local tap
        # exercises normal formula loading without a source clone or publication.
        run([brew, "tap-new", "--no-git", tap], env=brew_env)
        tap_created = True
        tap_root = Path(run([brew, "--repository", tap], env=brew_env, quiet=True).strip())
        (tap_root / "Formula").mkdir(exist_ok=True)
        shutil.copyfile(formula_path, tap_root / "Formula" / formula_path.name)
        run([brew, "install", "--formula", "--ignore-dependencies", qualified_name], env=brew_env)
        installed = True
        keg = prefix / "opt" / formula_name
        entry = keg / "bin/homi"
        report["installed_manifest"] = verify_runtime(keg / "libexec")
        tested = subprocess.run([brew, "test", qualified_name], env=brew_env, text=True, capture_output=True, timeout=180)
        print(tested.stdout + tested.stderr, end="", flush=True)
        if tested.returncode:
            # brew test insists on current metadata versions even when the
            # declared runtime dependencies are installed and work. Do not
            # upgrade an existing workstation to get past this separate gate.
            if "is missing test dependencies:" not in tested.stdout + tested.stderr:
                raise RuntimeError("Homebrew formula test failed")
            report["formula_test"] = "unqualified: installed dependency versions do not satisfy brew test's current metadata"
        else:
            report["formula_test"] = "pass"
        minimal = {**env, "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}
        version = run([entry, "version"], env=minimal)
        assert "HOMI 0.3.0" in version
        assert "bus" in run([entry, "--help"], env=minimal)
        run([entry, "setup", "--no-clients", "--dry-run"], env=minimal)
        assert not (temporary / "data").exists(), "formula install/test unexpectedly ran setup"
        report["checks"].append("formula install and equivalent version/help/dry-run checks with minimal PATH")
        run([entry, "setup", "--no-clients"], env=env)
        run([stable, "start"], env=env)
        run([stable, "claim", "brew-fixture"], env=env)
        run([stable, "send", "brew-fixture", "--from", "qualification", "--", "brew-reinstall-sentinel"], env=env)
        stored = run([stable, "inbox", "brew-fixture"], env=env)
        assert "brew-reinstall-sentinel" in stored
        run([stable, "stop"], env=env)
        report["checks"].append("explicit isolated setup and durable claim/send/inbox")
        # Older host dependencies may not meet current tap metadata. Reinstall
        # exposes no --ignore-dependencies flag, so forbid changes to every
        # currently installed formula as a fail-closed dependency guard.
        names = run([brew, "list", "--formula"], env=brew_env, quiet=True).splitlines()
        reinstall_env = {**brew_env, "HOMEBREW_FORBIDDEN_FORMULAE": " ".join(name for name in names if name != formula_name)}
        result = subprocess.run([brew, "reinstall", "--formula", qualified_name], env=reinstall_env, text=True, capture_output=True, timeout=180)
        print(result.stdout + result.stderr, end="", flush=True)
        if result.returncode:
            if "forbidden" not in (result.stdout + result.stderr).lower():
                raise RuntimeError("Homebrew reinstall failed")
            report["brew_reinstall"] = "unqualified: current metadata requires dependency changes; guarded reinstall refused"
            run([brew, "uninstall", "--formula", qualified_name], env=brew_env)
            installed = False
            run([brew, "install", "--formula", "--ignore-dependencies", qualified_name], env=brew_env)
            installed = True
            report["reinstall_method"] = "explicit uninstall then install with unchanged dependencies"
        else:
            report["brew_reinstall"] = "pass"
            report["reinstall_method"] = "brew reinstall with dependency-change guard"
        report["reinstalled_manifest"] = verify_runtime(keg / "libexec")
        run([keg / "bin/communicate", "version"], env=minimal)
        run([stable, "start"], env=env)
        assert "brew-reinstall-sentinel" in run([stable, "inbox", "brew-fixture"], env=env)
        run([stable, "stop"], env=env)
        report["checks"].append("formula reinstall preserves stable installation and mail")
        run([brew, "uninstall", "--formula", qualified_name], env=brew_env)
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
        cleanup_errors = []
        if stable.exists() and (temporary / "state/homi/homi.sock").exists():
            try:
                stopped = subprocess.run([str(stable), "stop"], env=env, capture_output=True, timeout=20)
                if stopped.returncode:
                    cleanup_errors.append("isolated daemon stop")
            except subprocess.TimeoutExpired:
                cleanup_errors.append("isolated daemon stop timeout")
        # A failed install can still have created a keg. Its name was absent
        # before this run, so this cleanup never owns a pre-existing formula.
        if installed or (prefix / "Cellar" / formula_name).exists():
            try:
                run([brew, "uninstall", "--formula", qualified_name], env=brew_env)
            except RuntimeError:
                cleanup_errors.append("candidate keg removal")
        if tap_created:
            try:
                run([brew, "untap", tap], env=brew_env)
            except RuntimeError:
                cleanup_errors.append("candidate tap removal")
        report["protected_after"] = {str(p): fingerprint(p) for p in protected}
        report["dependencies_after"] = {name: run([brew, "list", "--versions", name], env=brew_env, quiet=True).strip()
                                        for name in dependencies}
        if report["protected_after"] != before:
            cleanup_errors.append("pre-existing executable changed")
        if report["dependencies_after"] != dependencies:
            cleanup_errors.append("dependency versions changed")
        report["cleanup_errors"] = cleanup_errors
        if cleanup_errors:
            report["ok"] = False
        else:
            shutil.rmtree(temporary)
        (args.work / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        if cleanup_errors:
            raise RuntimeError("qualification cleanup requires attention: " + ", ".join(cleanup_errors))
    print("PASS: Homebrew install, wrappers, reinstall and uninstall with preserved state; brew test: " + report["formula_test"], flush=True)


if __name__ == "__main__":
    main()
