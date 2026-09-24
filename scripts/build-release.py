#!/usr/bin/env python3
"""Build an installable, deterministic HOMI runtime archive from the lockfile.

The archive includes production Node dependencies. Installing it requires Node,
Python and Bash, but never a Git checkout, npm credentials, or npm registry access.
"""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "packages/communicate"


def run(*args, cwd=ROOT):
    subprocess.run(args, cwd=cwd, check=True)


def launcher(entry):
    return '''#!/usr/bin/env bash
set -euo pipefail
self="${BASH_SOURCE[0]}"
while [ -L "$self" ]; do
  base="$(cd "$(dirname "$self")" && pwd)"
  self="$(readlink "$self")"
  [[ "$self" = /* ]] || self="$base/$self"
done
root="$(cd "$(dirname "$self")/.." && pwd)"
exec node "$root/src/''' + entry + '''" "$@"
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    parser.add_argument("--allow-dirty", action="store_true", help="development candidates only")
    args = parser.parse_args()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT).strip())
    if dirty and not args.allow_dirty:
        parser.error("release source has uncommitted changes; commit it or use --allow-dirty for a development candidate")
    if not (PACKAGE / "package-lock.json").is_file():
        parser.error("a committed package-lock.json is required")
    version = json.loads((PACKAGE / "package.json").read_text())["version"]
    if (ROOT / "VERSION").read_text().strip() != version:
        parser.error("root and package release versions differ")
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    epoch = int(subprocess.check_output(["git", "show", "-s", "--format=%ct", "HEAD"], cwd=ROOT))
    run("npm", "ci", "--ignore-scripts", cwd=PACKAGE)
    run("npm", "run", "vendor", cwd=PACKAGE)
    args.output.mkdir(parents=True, exist_ok=True)
    target = args.output / f"homi-{version}.tar.gz"
    with tempfile.TemporaryDirectory(prefix="homi-release-") as temp:
        stage = Path(temp) / f"homi-{version}"
        stage.mkdir()
        for name in ("src", "vendor"):
            shutil.copytree(PACKAGE / name, stage / name, symlinks=True)
        for name in ("package.json", "package-lock.json", "README.md"):
            shutil.copy2(PACKAGE / name, stage / name)
        shutil.copy2(ROOT / "LICENSE", stage / "LICENSE")
        run("npm", "ci", "--omit=dev", "--ignore-scripts", cwd=stage)
        (stage / "bin").mkdir()
        for name, entry in (("homi", "homi.mjs"), ("communicate", "cli.mjs")):
            path = stage / "bin" / name
            path.write_text(launcher(entry))
            path.chmod(0o755)
        dependencies = {}
        lock = json.loads((stage / "package-lock.json").read_text())
        for name, data in lock.get("packages", {}).items():
            if name:
                dependencies[name] = {k: data[k] for k in ("version", "integrity", "license") if k in data}
        manifest = {"product": "HOMI", "version": version, "source": revision,
                    "dirty": dirty, "sourceDateEpoch": epoch, "dependencies": dependencies,
                    "files": {}}
        for file in sorted(stage.rglob("*")):
            if file.is_file() and not file.is_symlink():
                manifest["files"][file.relative_to(stage).as_posix()] = hashlib.sha256(file.read_bytes()).hexdigest()
        (stage / "release.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        with target.open("wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=epoch) as zipped:
            with tarfile.open(fileobj=zipped, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for file in [stage, *sorted(stage.rglob("*"))]:
                    info = archive.gettarinfo(str(file), arcname=file.relative_to(stage.parent).as_posix())
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    info.mtime = epoch
                    info.pax_headers = {}
                    info.mode = 0o755 if file.is_dir() or file.stat().st_mode & 0o111 else 0o644
                    if info.isfile():
                        with file.open("rb") as source:
                            archive.addfile(info, source)
                    else:
                        archive.addfile(info)
    checksum = hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_suffix(target.suffix + ".sha256").write_text(f"{checksum}  {target.name}\n")
    print(json.dumps({"archive": str(target), "sha256": checksum, "source": revision, "dirty": dirty}))


if __name__ == "__main__":
    main()
