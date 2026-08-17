#!/usr/bin/env node
// cli.ts — the `homi` bin. `serve` runs the MCP stdio server (what an MCP client
// spawns); `setup` stabilizes the vendored daemon, ensures it runs, and prints
// the client install lines; `doctor` checks the install. The MCP server and the
// Python CLI are two faces of one kernel — this bin never reimplements fabric
// logic, it drives the daemon over its control socket.
import fs from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import readline from "node:readline/promises";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { buildServer } from "./server.js";
import { renderPlist, LAUNCHD_LABEL } from "./plist.js";
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
  const plist = renderPlist(python, daemon, self);
  const dir = path.join(os.homedir(), "Library", "LaunchAgents");
  fs.mkdirSync(dir, { recursive: true });
  const plistPath = path.join(dir, LAUNCHD_LABEL + ".plist");
  fs.writeFileSync(plistPath, plist);
  const uid = process.getuid ? process.getuid() : 0;
  spawnSync("launchctl", ["bootout", `gui/${uid}`, plistPath], { stdio: "ignore" });
  const r = spawnSync("launchctl", ["bootstrap", `gui/${uid}`, plistPath], { stdio: "ignore" });
  return { plistPath, ok: r.status === 0 };
}

// The daemon's own version constant, read from the kernel file we ship — the
// one honest source for "what would run after a restart".
function vendoredVersion(daemonPath: string): string | null {
  try {
    const m = fs.readFileSync(daemonPath, "utf8").match(/^HOMI_VERSION = "([^"]+)"/m);
    return m ? m[1] : null;
  } catch {
    return null;
  }
}

async function promptLine(q: string): Promise<string> {
  const rl = readline.createInterface({ input: process.stdin, output: process.stderr });
  try {
    return (await rl.question(q)).trim();
  } finally {
    rl.close();
  }
}

// Connect-only probe (a socket FILE is not a listener — measure it).
function probeSocket(p: string, timeoutMs = 800): Promise<boolean> {
  return new Promise((resolve) => {
    const s = net.createConnection({ path: p });
    const to = setTimeout(() => { s.destroy(); resolve(false); }, timeoutMs);
    s.once("connect", () => { clearTimeout(to); s.destroy(); resolve(true); });
    s.once("error", () => { clearTimeout(to); resolve(false); });
  });
}

// Measured first-run proof: a real claim, a real send, the line really landing
// in the durable inbox. Never advertise what you have not measured.
async function selfTest(): Promise<number | null> {
  const name = "homi-selftest";
  const token = "selftest-" + Math.random().toString(16).slice(2, 10);
  const t0 = Date.now();
  try {
    await call({ op: "claim", name });
    await call({ op: "send", to: name, text: token, from: "homi-setup" });
    for (let i = 0; i < 20; i++) {
      const r = await call({ op: "inbox", name, tail: 5 });
      if ((r?.messages || []).some((m: any) => m?.text === token)) return Date.now() - t0;
      await new Promise((r2) => setTimeout(r2, 100));
    }
    return null;
  } catch {
    return null;
  } finally {
    try { await call({ op: "release", name }); } catch {}
  }
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
  let st = await call({ op: "status" }, { timeoutMs: 8000 });
  console.error(`• daemon up as device "${st?.self?.device}"`);
  const interactive = !!process.stdin.isTTY && !yes;

  // Version gate — a KeepAlive'd daemon never restarts on its own, so a stale
  // one silently lacks every feature the code on disk gained (P3). stop +
  // KeepAlive (or the kernel's autostart) respawns through the repointed
  // `current` symlink, i.e. the new kernel.
  const want = vendoredVersion(daemon);
  const running = st?.self?.version ?? null;
  if (want && running !== want) {
    console.error(`• daemon runs ${running || "an unversioned (pre-2026.08.17) kernel"}; staged kernel is ${want} (stale)`);
    let restart = yes;
    if (!restart && interactive)
      restart = /^y?$/i.test(await promptLine("  restart it to pick up the new kernel? [Y/n] "));
    if (restart) {
      try { await call({ op: "stop" }); } catch {}
      await new Promise((r) => setTimeout(r, 1500));
      st = await call({ op: "status" }, { timeoutMs: 10000 });
      console.error(`• daemon restarted: version ${st?.self?.version ?? "unknown"}`);
    } else {
      console.error("• leaving the stale daemon running — restart it before relying on new verbs");
    }
  } else if (want) {
    console.error(`• daemon version: ${running} (current)`);
  }

  // The claim ceremony — the ONLY writer of user.json. Explicit, never lazy:
  // a handle regenerated on the quiet is how one human becomes two identities.
  const hIdx = args.indexOf("--handle");
  const argHandle = hIdx >= 0 ? (args[hIdx + 1] || "").replace(/^@/, "") : null;
  const uinfo = await call({ op: "user" });
  if (argHandle) {
    const r = await call({ op: "user-set", handle: argHandle, via: "npx-setup" });
    console.error(`• user: ${r?.ok ? "claimed @" + r.user.handle : "claim failed — " + r?.err}`);
  } else if (uinfo?.user?.handle) {
    console.error(`• user: @${uinfo.user.handle}`);
  } else if (interactive) {
    console.error("\nNo user is claimed on this machine.");
    console.error("Your handle names YOU — every device and agent here hangs under it,");
    console.error("and other people reach your agents as <agent>@<handle>. Lowercase");
    console.error("letters, digits, hyphen; max 32. This claim is local — no server.");
    console.error("Already claimed a handle on another device? Type the SAME one.\n");
    const h = (await promptLine("  handle> ")).replace(/^@/, "");
    if (h) {
      const d = await promptLine("  display name (optional)> ");
      const r = await call({ op: "user-set", handle: h, display: d || undefined, via: "npx-setup" });
      console.error(`• user: ${r?.ok ? "claimed @" + r.user.handle : "claim failed — " + r?.err}`);
    } else {
      console.error("• user: left unclaimed (claim later: communicate homi init)");
    }
  } else {
    console.error("• user: unclaimed (claim: setup --handle YOU, or communicate homi init)");
  }

  if (claimName) {
    const r = await call({ op: "claim", name: claimName });
    console.error(`• claim ${claimName}: ${r?.ok ? "ok" : r?.err}`);
  }

  let failed = false;
  const ms = await selfTest();
  if (ms != null) console.error(`• self-test MEASURED: claim → send → inbox line landed (${ms} ms) → released`);
  else { console.error("FAIL self-test: the message never landed — see " + path.join(stateRoot(), "daemon.log")); failed = true; }

  // MCP registration — offered and run with consent, not just printed.
  const abs = path.join(pkgRoot, "dist", "cli.js");
  const clients: [string, string[]][] = [];
  for (const tool of ["claude", "codex"]) {
    if (spawnSync("which", [tool], { stdio: "ignore" }).status === 0)
      clients.push([tool, ["mcp", "add", "homi", "--", "node", abs, "serve"]]);
  }
  if (clients.length && !args.includes("--no-mcp")) {
    let doAdd = yes;
    if (!doAdd && interactive) {
      console.error("\nRegister homi with your agents?");
      for (const [t, a] of clients) console.error(`  ${t} ${a.join(" ")}`);
      doAdd = /^y?$/i.test(await promptLine("Run these now? [Y/n] "));
    }
    if (doAdd)
      for (const [t, a] of clients) {
        const r = spawnSync(t, a, { stdio: "ignore" });
        console.error(`• ${t}: ${r.status === 0 ? "mcp added" : "mcp add failed (exit " + r.status + ")"}`);
      }
  }

  console.log("\nhomi is set up. MCP line for any other client:\n");
  console.log(`  claude mcp add homi -- node ${abs} serve`);
  console.log(`\nNext: claim identities (communicate homi claim <name>) or spawn agents (communicate homi spawn).`);
  if (failed) process.exit(1);
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
    const want = vendoredVersion(daemon);
    checks.push(["daemon version current", !!want && st?.self?.version === want,
                 `running=${st?.self?.version ?? "unversioned (stale kernel)"} staged=${want ?? "?"}`]);
    checks.push(["user claimed", !!st?.self?.user?.handle,
                 st?.self?.user?.handle ? "@" + st.self.user.handle
                                        : "unclaimed — communicate homi init"]);
    const ag = await call({ op: "agents" });
    checks.push(["roster reachable", !!ag?.ok, `${(ag?.agents || []).length} identities`]);
  } catch (e: any) {
    checks.push(["daemon answers", false, String(e.message)]);
  }

  // Split-brain detection: the old installer wrote a launchd unit whose
  // COMM_STATE stripped one level too many, leaving a persistent daemon on
  // ~/.local/state/homi that no client ever talks to. Report it; NEVER delete
  // it — its mail/ may hold messages nobody read.
  const orphanRoot = path.join(os.homedir(), ".local", "state", "homi");
  if (orphanRoot !== stateRoot() && fs.existsSync(orphanRoot)) {
    const answering = fs.existsSync(path.join(orphanRoot, "homi.sock"))
      && await probeSocket(path.join(orphanRoot, "homi.sock"));
    checks.push(["no orphaned state root", false,
                 `${orphanRoot} exists${answering ? " and a daemon ANSWERS there" : ""}` +
                 " — old installer bug; inspect its mail/ before removing, then re-run setup"]);
  } else {
    checks.push(["no orphaned state root", true, ""]);
  }
  const plistPath = path.join(os.homedir(), "Library", "LaunchAgents", LAUNCHD_LABEL + ".plist");
  if (process.platform === "darwin" && fs.existsSync(plistPath)) {
    const content = fs.readFileSync(plistPath, "utf8");
    const m = content.match(/<key>COMM_STATE<\/key><string>([^<]*)<\/string>/);
    checks.push(["launchd unit state root", !!m && path.join(m[1], "homi") === stateRoot(),
                 m ? m[1] : "no COMM_STATE in plist"]);
    checks.push(["launchd unit bakes PATH", /<key>PATH<\/key>/.test(content),
                 "seat/spawn need tmux+claude on the daemon's PATH"]);
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
