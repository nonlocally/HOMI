#!/usr/bin/env node
// @aadarwal/communicate — node verbs (setup/doctor/serve/version) + bash passthrough.
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { readFileSync, existsSync } from "node:fs";
import path from "node:path";

const pkgDir = fileURLToPath(new URL("..", import.meta.url));
const vendorCli = path.join(pkgDir, "vendor", "bin", "communicate");
const [cmd, ...rest] = process.argv.slice(2);

const version = () => {
  const v = JSON.parse(readFileSync(path.join(pkgDir, "package.json"), "utf8")).version;
  const vp = path.join(pkgDir, "vendor", "VERSION");
  const stamp = existsSync(vp) ? readFileSync(vp, "utf8").trim() : "unvendored";
  console.log(`@aadarwal/communicate ${v} (payload ${stamp})`);
};

switch (cmd) {
  case undefined:
  case "help":
  case "--help":
    version();
    console.log("verbs: setup [--claude|--codex|--dry-run|--uninstall|--purge|-y] | doctor | serve | version");
    console.log("       any communicate CLI verb (agents, route, send, codex ..., claude ..., wake ...)");
    break;
  case "version":
  case "--version":
    version();
    break;
  case "setup":
    await (await import("./setup.mjs")).runSetup(rest);
    break;
  case "doctor":
    await (await import("./setup.mjs")).runDoctor();
    break;
  case "serve":
    await (await import("./serve.mjs")).runServe();
    break;
  default: {
    if (!existsSync(vendorCli)) {
      console.error("communicate: payload missing — reinstall @aadarwal/communicate (or run: npm run vendor)");
      process.exit(127);
    }
    const r = spawnSync(vendorCli, [cmd, ...rest], { stdio: "inherit" });
    process.exit(r.status ?? 1);
  }
}
