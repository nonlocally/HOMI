// Copy the stdlib-only Python kernel into vendor/ so the npm package ships a
// self-contained daemon (no pip, no separate checkout). Run at build time.
import { copyFileSync, mkdirSync, writeFileSync, existsSync } from "node:fs";
import { execSync } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const pkg = dirname(here);              // packages/homi
const repo = dirname(dirname(pkg));     // repo root
const lib = join(repo, "lib");
const vendor = join(pkg, "vendor");
mkdirSync(vendor, { recursive: true });

for (const f of ["homi.py", "cc_peer.py", "homi_seat.py"]) {
  const src = join(lib, f);
  if (!existsSync(src)) throw new Error("missing kernel file: " + src);
  copyFileSync(src, join(vendor, f));
}

let sha = "unknown";
try { sha = execSync("git rev-parse --short HEAD", { cwd: repo }).toString().trim(); } catch {}
writeFileSync(join(vendor, "VERSION"), `communicate@${sha}\n`);
console.log(`vendored homi.py, cc_peer.py, homi_seat.py (communicate@${sha})`);
