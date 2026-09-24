#!/usr/bin/env node
// Changed installation boundaries only: immutable releases, rollback, owned
// settings/links, package tamper detection and preserved state. No live clients.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { createHash } from "node:crypto";
import { serviceDefinition } from "../src/lifecycle.mjs";

const pkg = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const temp = fs.mkdtempSync(path.join(os.tmpdir(), "homi-lifecycle-"));
const home = path.join(temp, "home with spaces"), data = path.join(home, "data"), state = path.join(home, "state");
fs.mkdirSync(path.join(home, "bin"), { recursive: true });
fs.mkdirSync(path.join(home, ".claude"));
const settings = path.join(home, ".claude/settings.json");
fs.writeFileSync(settings, JSON.stringify({ sentinel: "keep", enabledPlugins: { "other@other": true } }));
fs.writeFileSync(path.join(home, "bin/claude"), `#!/usr/bin/env node
const fs=require('node:fs'),path=require('node:path');
const a=process.argv.slice(2), home=process.env.HOME;
if (a[0]==='--version') { console.log('fixture'); process.exit(0); }
if (fs.existsSync(path.join(home,'fail-client'))) process.exit(9);
if (a[0]==='plugin' && a[1]==='list') {
 const p=path.join(process.env.COMMUNICATE_DATA,'current/vendor/plugins/communicate/.claude-plugin/plugin.json');
 console.log(JSON.stringify([{id:'communicate@communicate',scope:'user',version:JSON.parse(fs.readFileSync(p)).version}]));
}
`, { mode: 0o755 });
const env = { ...process.env, HOME: home, COMMUNICATE_DATA: data, COMM_STATE: state,
  CLAUDE_CONFIG_DIR: path.join(home, ".claude"), PATH: path.join(home, "bin") + path.delimiter + process.env.PATH };
for (const key of ["CLAUDE_CODE_MESSAGING_SOCKET", "CODEX_THREAD_ID", "HOMI_SOCK", "HOMI_SOCK_DIR", "HOMI_SESSIONS_DIR"]) delete env[key];
const assert = (condition, text) => { if (!condition) throw new Error(text); };
const run = (entry, args, ok = true) => {
  const r = spawnSync(process.execPath, [entry, ...args], { env, encoding: "utf8", timeout: 45000 });
  assert((r.status === 0) === ok, `${args.join(" ")}: ${r.stdout}\n${r.stderr}`);
  return r;
};
const cli = path.join(pkg, "src/homi.mjs");
const installed = path.join(data, "current/src/homi.mjs");
const target = () => fs.realpathSync(path.join(data, "current"));
const hash = (file) => createHash("sha256").update(fs.readFileSync(file)).digest("hex");
try {
  run(cli, ["setup", "--no-clients", "--dry-run"]);
  assert(!fs.existsSync(data), "dry-run created installation state");
  run(cli, ["setup", "--claude"]);
  const first = target();
  assert(fs.existsSync(path.join(first, "vendor/lib/homi.py")), "durable kernel absent");
  assert(fs.existsSync(path.join(first, "vendor/lib/bus_broker.py")), "bus absent");
  assert(!fs.existsSync(path.join(first, "vendor/lib/phone")), "phone leaked into core");
  assert(fs.readlinkSync(path.join(data, "bin/homi")) === "../current/src/homi.mjs", "stable CLI link missing");
  const untouched = path.join(state, "homi/mail/kept/inbox.jsonl");
  fs.mkdirSync(path.dirname(untouched), { recursive: true });
  fs.writeFileSync(untouched, '{"text":"preserved fixture"}\n');
  run(installed, ["setup", "--claude"]);
  assert(target() === first, "repeat setup replaced release directory");

  const next = path.join(temp, "next artifact");
  fs.mkdirSync(next);
  for (const name of ["src", "vendor", "package.json", "LICENSE"]) fs.cpSync(path.join(pkg, name), path.join(next, name), { recursive: true });
  fs.symlinkSync(path.join(pkg, "node_modules"), path.join(next, "node_modules"));
  const metadata = JSON.parse(fs.readFileSync(path.join(next, "package.json")));
  metadata.version = "0.3.1-fixture";
  fs.writeFileSync(path.join(next, "package.json"), JSON.stringify(metadata));
  for (const kind of [".claude-plugin", ".codex-plugin"]) {
    const p = path.join(next, "vendor/plugins/communicate", kind, "plugin.json");
    const plugin = JSON.parse(fs.readFileSync(p)); plugin.version = metadata.version;
    fs.writeFileSync(p, JSON.stringify(plugin));
  }
  const manifestFile = path.join(next, "vendor/release.json");
  const manifest = JSON.parse(fs.readFileSync(manifestFile)); manifest.version = metadata.version;
  for (const name of Object.keys(manifest.files)) manifest.files[name] = hash(path.join(next, "vendor", name));
  for (const name of Object.keys(manifest.packageFiles)) manifest.packageFiles[name] = hash(path.join(next, name));
  fs.writeFileSync(manifestFile, JSON.stringify(manifest));
  const nextCli = path.join(next, "src/homi.mjs");
  fs.writeFileSync(path.join(home, "fail-client"), "fixture");
  run(nextCli, ["setup", "--claude"], false);
  assert(target() === first, "failed client activation changed current release");
  fs.rmSync(path.join(home, "fail-client"));
  // Refuse unknown executables before switching payloads or refreshing clients.
  const stableBin = path.join(data, "bin/homi");
  fs.unlinkSync(stableBin); fs.writeFileSync(stableBin, "owned by another tool");
  run(nextCli, ["setup", "--claude"], false);
  assert(target() === first && fs.readFileSync(stableBin, "utf8") === "owned by another tool", "executable conflict modified the installation");
  fs.unlinkSync(stableBin); fs.symlinkSync("../current/src/homi.mjs", stableBin);

  // Service-manager fixture, never the host's actual launchctl/systemctl. A
  // failed replacement must restore /current BEFORE reloading the previous unit.
  const manager = process.platform === "darwin" ? "launchctl" : "systemctl";
  const unitFile = process.platform === "darwin" ? path.join(home, "Library/LaunchAgents/com.communicate.homi.plist") : path.join(home, ".config/systemd/user/communicate-homi.service");
  fs.mkdirSync(path.dirname(unitFile), { recursive: true }); fs.writeFileSync(unitFile, "previous fixture definition");
  fs.writeFileSync(path.join(home, "bin", manager), `#!/usr/bin/env node
const fs=require('node:fs'),path=require('node:path'),a=process.argv.slice(2);
if (a.includes('bootstrap') || a.includes('enable')) {
 const target=fs.realpathSync(path.join(process.env.COMMUNICATE_DATA,'current'));
 fs.appendFileSync(path.join(process.env.HOME,'service-calls'),target+'\\n');
 const version=JSON.parse(fs.readFileSync(path.join(target,'package.json'))).version;
 if (version==='0.3.1-fixture') process.exit(9);
}
`, { mode: 0o755 });
  run(nextCli, ["setup", "--no-clients", "--service"], false);
  const serviceCalls = fs.readFileSync(path.join(home, "service-calls"), "utf8").trim().split("\n");
  assert(serviceCalls.length === 2 && serviceCalls[1] === first, "previous service reloaded before pointer restoration");
  assert(target() === first && fs.readFileSync(unitFile, "utf8") === "previous fixture definition", "failed service activation lost previous unit/payload");
  run(nextCli, ["setup", "--claude"]);
  const second = target();
  assert(second !== first && fs.existsSync(first), "upgrade lost the previous release");
  run(installed, ["rollback"]);
  assert(target() === first, "rollback did not restore previous release");
  assert(fs.readFileSync(untouched, "utf8").includes("preserved"), "rollback altered identity state");
  run(installed, ["rollback"]);
  assert(target() === second, "rollback did not retain forward recovery");

  // An obsolete uninstaller must not deregister another installation.
  const modified = JSON.parse(fs.readFileSync(settings));
  modified.extraKnownMarketplaces.communicate.source.path = path.join(home, "different-checkout/plugins");
  fs.writeFileSync(settings, JSON.stringify(modified));
  run(installed, ["uninstall", "--claude"]);
  assert(JSON.parse(fs.readFileSync(settings)).extraKnownMarketplaces.communicate.source.path.includes("different-checkout"), "uninstall removed a replacement registration");
  modified.extraKnownMarketplaces.communicate.source.path = path.join(data, "current/vendor/plugins");
  fs.writeFileSync(settings, JSON.stringify(modified));
  run(installed, ["uninstall"]);
  assert(fs.existsSync(untouched), "uninstall removed durable mail");
  assert(JSON.parse(fs.readFileSync(settings)).enabledPlugins["other@other"], "uninstall damaged another plugin");
  run(nextCli, ["setup", "--no-clients"]);
  assert(fs.existsSync(untouched), "reinstall removed durable mail");
  fs.appendFileSync(path.join(next, "vendor/lib/homi.py"), "\n# tampered fixture\n");
  run(nextCli, ["setup", "--no-clients"], false);
  assert(target() === second, "tampered payload was activated");

  // Render both platform definitions; no service manager is invoked here.
  const mac = serviceDefinition("darwin", "/tmp/python & tools/python3", "fixture");
  const linux = serviceDefinition("linux", "/tmp/python tools/python3", "fixture");
  assert(mac.content.includes("python &amp; tools"), "launchd XML path was not escaped");
  assert(linux.content.includes('ExecStart="/tmp/python tools/python3"'), "systemd path with spaces was not quoted");
  assert(mac.label === "com.communicate.homi" && linux.label === "communicate-homi.service", "service compatibility label changed");
  console.log("PASS: lifecycle — immutable upgrade, failed activation, rollback, ownership, preserved state, tamper detection and platform service rendering");
} finally { fs.rmSync(temp, { recursive: true, force: true }); }
