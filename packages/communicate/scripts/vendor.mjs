#!/usr/bin/env node
// Build vendor/ — the packaged payload. This list IS the allowlist: nothing
// homi-flavored may land here (the package ships the communicate layer only).
import { cpSync, mkdirSync, rmSync, writeFileSync, readdirSync, chmodSync, readFileSync, existsSync } from "node:fs";
import { execSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";

const pkgDir = fileURLToPath(new URL("..", import.meta.url));
const repo = path.resolve(pkgDir, "..", "..");
const vendor = path.join(pkgDir, "vendor");

rmSync(vendor, { recursive: true, force: true });
mkdirSync(path.join(vendor, "bin"), { recursive: true });
mkdirSync(path.join(vendor, "lib"), { recursive: true });

cpSync(path.join(repo, "bin", "communicate"), path.join(vendor, "bin", "communicate"));
for (const f of readdirSync(path.join(repo, "lib"))) {
  const keep = (f.endsWith(".sh") && !/homi/.test(f)) || f === "cc_peer.py";
  if (!keep) continue;
  if (/homi/.test(f)) throw new Error(`homi file escaped the filter: ${f}`);
  cpSync(path.join(repo, "lib", f), path.join(vendor, "lib", f));
}
// plugins/ may hold other planes (phone, ...) — vendor ONLY the communicate plugin.
mkdirSync(path.join(vendor, "plugins"), { recursive: true });
cpSync(path.join(repo, "plugins", ".claude-plugin"), path.join(vendor, "plugins", ".claude-plugin"), { recursive: true });
cpSync(path.join(repo, "plugins", "communicate"), path.join(vendor, "plugins", "communicate"), { recursive: true });
cpSync(path.join(repo, ".agents"), path.join(vendor, ".agents"), { recursive: true });

// Belt and braces: nothing under vendor/ may mention a homi lib file.
const walk = (d) => readdirSync(d, { withFileTypes: true }).flatMap((e) =>
  e.isDirectory() ? walk(path.join(d, e.name)) : [path.join(d, e.name)]);
for (const f of walk(vendor)) if (/homi.*\.(py|sh)$/.test(path.basename(f)))
  throw new Error(`homi artifact in vendor: ${f}`);

let sha = "unknown";
try { sha = execSync("git rev-parse --short HEAD", { cwd: repo, encoding: "utf8" }).trim(); } catch {}
const version = JSON.parse(readFileSync(path.join(pkgDir, "package.json"), "utf8")).version;
writeFileSync(path.join(vendor, "VERSION"), `${version}+${sha}\n`);
chmodSync(path.join(vendor, "bin", "communicate"), 0o755);
chmodSync(path.join(vendor, "plugins", "communicate", "bin", "communicate"), 0o755);
console.log(`vendored ${version}+${sha} -> ${vendor}`);
if (!existsSync(path.join(vendor, "lib", "codex.sh"))) throw new Error("codex.sh missing");
