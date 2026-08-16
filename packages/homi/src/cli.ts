#!/usr/bin/env node
// cli.ts — the `homi` bin. `serve` runs the MCP stdio server (what an MCP client
// spawns); `setup` stabilizes the vendored daemon, ensures it runs, and prints
// the client install lines; `doctor` checks the install. The MCP server and the
// Python CLI are two faces of one kernel — this bin never reimplements fabric
// logic, it drives the daemon over its control socket.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { buildServer } from "./server.js";
import { call, resolvePython, stateRoot, controlSocket, daemonFile } from "./kernel.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const pkgRoot = path.dirname(here);
const VERSION = "0.1.0";

async function serve() {
  const server = buildServer();
  const transport = new StdioServerTransport();
  await server.connect(transport);
  // Stay alive until stdin closes (the client owns our lifetime).
}

function dataDir(): string {
  return path.join(
    process.env.XDG_DATA_HOME || path.join(os.homedir(), ".local", "share"),
    "homi",
  );
}

// Copy the vendored kernel into a STABLE location (the npx cache is ephemeral;
// a launchd/systemd unit must point at a path that survives cache pruning).
function stabilizeVendor(): string {
  const dst = path.join(dataDir(), "daemon", VERSION);
  fs.mkdirSync(dst, { recursive: true });
  for (const f of ["homi.py", "cc_peer.py", "homi_seat.py", "homi_workspace.py", "VERSION"]) {
    const src = path.join(pkgRoot, "vendor", f);
    if (fs.existsSync(src)) fs.copyFileSync(src, path.join(dst, f));
  }
  const cur = path.join(dataDir(), "daemon", "current");
  try { fs.rmSync(cur, { force: true }); } catch {}
  try { fs.symlinkSync(dst, cur); } catch {
    // Windows / no-symlink: write a redirector file the resolver can read.
    fs.writeFileSync(cur + ".path", dst);
  }
  return path.join(cur, "homi.py");
}

function installLaunchd(python: string, daemon: string, self: string) {
  const label = "com.communicate.homi";
  const plist = `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>${label}</string>
  <key>ProgramArguments</key><array>
    <string>${python}</string><string>${daemon}</string><string>daemon</string>
  </array>
  <key>EnvironmentVariables</key><dict>
    <key>COMM_STATE</key><string>${process.env.COMM_STATE || path.dirname(path.dirname(stateRoot()))}</string>
    <key>HOMI_SELF</key><string>${self}</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardErrorPath</key><string>${path.join(stateRoot(), "daemon.log")}</string>
  <key>StandardOutPath</key><string>${path.join(stateRoot(), "daemon.log")}</string>
</dict></plist>`;
  const dir = path.join(os.homedir(), "Library", "LaunchAgents");
  fs.mkdirSync(dir, { recursive: true });
  const plistPath = path.join(dir, label + ".plist");
  fs.writeFileSync(plistPath, plist);
  const uid = process.getuid ? process.getuid() : 0;
  spawnSync("launchctl", ["bootout", `gui/${uid}`, plistPath], { stdio: "ignore" });
  const r = spawnSync("launchctl", ["bootstrap", `gui/${uid}`, plistPath], { stdio: "ignore" });
  return { plistPath, ok: r.status === 0 };
}

function selfName(python: string, daemon: string): string {
  const r = spawnSync(python, [daemon, "selfname"], { encoding: "utf8" });
  return (r.stdout || "").trim() || os.hostname().split(".")[0];
}

async function setup(args: string[]) {
  const yes = args.includes("-y");
  const noPersist = args.includes("--no-persist") || process.env.HOMI_NO_PERSIST === "1";
  const claimIdx = args.indexOf("--claim");
  const claimName = claimIdx >= 0 ? args[claimIdx + 1] : null;
  const pyIdx = args.indexOf("--python");
  if (pyIdx >= 0) process.env.HOMI_PYTHON = args[pyIdx + 1];

  const python = resolvePython();
  console.error("• python:", python);
  const daemon = stabilizeVendor();
  console.error("• daemon stabilized at:", daemon);
  process.env.HOMI_DAEMON_DIR = path.dirname(daemon);

  // Respect an incumbent daemon (a repo-managed homi may already own this device).
  let incumbent = false;
  try {
    const st = await call({ op: "status" }, { noAutostart: true, timeoutMs: 2000 });
    incumbent = !!st?.ok;
    if (incumbent) console.error("• a homi daemon already answers on", controlSocket(), "— reusing it");
  } catch {}

  const self = selfName(python, daemon);
  if (!incumbent && noPersist) {
    console.error("• (persistence install skipped: --no-persist)");
  } else if (!incumbent && process.platform === "darwin") {
    const { plistPath, ok } = installLaunchd(python, daemon, self);
    console.error(`• launchd ${ok ? "installed" : "install attempted"}: ${plistPath}`);
  } else if (!incumbent) {
    console.error("• (persistence install: on Linux run `communicate homi install` from the repo; systemd unit)");
  }

  // Ensure it's up (autostart if launchd didn't take).
  const st = await call({ op: "status" }, { timeoutMs: 8000 });
  console.error(`• daemon up as device "${st?.self?.device}"`);

  if (claimName) {
    const r = await call({ op: "claim", name: claimName });
    console.error(`• claim ${claimName}: ${r?.ok ? "ok" : r?.err}`);
  }

  const abs = path.join(pkgRoot, "dist", "cli.js");
  console.log("\nhomi is set up. Add it to your agents:\n");
  console.log(`  claude mcp add homi -- node ${abs} serve`);
  console.log(`  codex  mcp add homi -- node ${abs} serve`);
  console.log(`\n(or, once published:  claude mcp add homi -- npx -y @aadarwal/homi serve)\n`);
}

async function doctor() {
  const checks: [string, boolean, string][] = [];
  let python = "";
  try { python = resolvePython(); checks.push(["python3 >=3.9", true, python]); }
  catch (e: any) { checks.push(["python3 >=3.9", false, String(e.message)]); }
  const daemon = daemonFile();
  checks.push(["daemon file present", fs.existsSync(daemon), daemon]);
  try {
    const st = await call({ op: "status" }, { timeoutMs: 5000 });
    checks.push(["daemon answers", !!st?.ok, `device=${st?.self?.device}`]);
    const ag = await call({ op: "agents" });
    checks.push(["roster reachable", !!ag?.ok, `${(ag?.agents || []).length} identities`]);
  } catch (e: any) {
    checks.push(["daemon answers", false, String(e.message)]);
  }
  let allok = true;
  for (const [name, ok, detail] of checks) {
    if (!ok) allok = false;
    console.log(`${ok ? "ok  " : "FAIL"} ${name}${detail ? "  (" + detail + ")" : ""}`);
  }
  process.exit(allok ? 0 : 1);
}

async function main() {
  const [cmd, ...rest] = process.argv.slice(2);
  switch (cmd) {
    case undefined:
    case "serve":
      await serve();
      break;
    case "setup":
      await setup(rest);
      break;
    case "doctor":
      await doctor();
      break;
    case "--version":
    case "version":
      console.log(VERSION);
      break;
    default:
      console.error(`usage: homi {serve|setup [--claim NAME] [--python P]|doctor|version}`);
      process.exit(1);
  }
}

main().catch((e) => {
  console.error(e?.stack || String(e));
  process.exit(1);
});
