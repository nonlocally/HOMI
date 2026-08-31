#!/usr/bin/env node
// Sandboxed setup test: fake $HOME + $COMMUNICATE_DATA, assert merge-not-clobber,
// backup, payload+symlink, uninstall restore, and dry-run writes nothing.
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, existsSync, readdirSync, lstatSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import os from "node:os";
import path from "node:path";

const pkgDir = fileURLToPath(new URL("..", import.meta.url));
const cli = path.join(pkgDir, "src", "cli.mjs");
const fakeHome = mkdtempSync(path.join(os.tmpdir(), "comm-setup-home-"));
const data = path.join(fakeHome, "data");
mkdirSync(path.join(fakeHome, ".claude"), { recursive: true });
const sp = path.join(fakeHome, ".claude", "settings.json");
writeFileSync(sp, JSON.stringify({ sentinel: "keep-me", enabledPlugins: { "existing@mkt": true }, permissions: { allow: ["Read"] } }, null, 2));

const env = { ...process.env, HOME: fakeHome, COMMUNICATE_DATA: data };
const runCli = (...args) => spawnSync("node", [cli, ...args], { encoding: "utf8", env });
const die = (m) => { console.error("FAIL: " + m); process.exit(1); };

// 1. dry-run writes nothing
let r = runCli("setup", "--claude", "--dry-run");
if (r.status !== 0) die("dry-run exited " + r.status + "\n" + r.stderr);
if (existsSync(data)) die("dry-run created the data dir");
if (JSON.parse(readFileSync(sp, "utf8")).extraKnownMarketplaces) die("dry-run edited settings");
if (!/would copy payload/.test(r.stdout)) die("dry-run did not narrate");

// 2. real --claude install
r = runCli("setup", "--claude");
if (r.status !== 0) die("setup exited " + r.status + "\n" + r.stderr + r.stdout);
const s = JSON.parse(readFileSync(sp, "utf8"));
if (s.sentinel !== "keep-me" || !s.enabledPlugins["existing@mkt"] || !s.permissions) die("merge clobbered existing keys");
if (s.extraKnownMarketplaces?.communicate?.source?.source !== "directory") die("marketplace key wrong: " + JSON.stringify(s.extraKnownMarketplaces));
const marketPath = s.extraKnownMarketplaces.communicate.source.path;
if (marketPath !== path.join(data, "current", "vendor", "plugins")) die("marketplace path wrong: " + marketPath);
if (s.enabledPlugins["communicate@communicate"] !== true) die("plugin not enabled");
if (!readdirSync(path.join(fakeHome, ".claude")).some((f) => f.startsWith("settings.json.communicate-backup-"))) die("no backup written");
if (!lstatSync(path.join(data, "current")).isSymbolicLink()) die("current is not a symlink");
if (!existsSync(path.join(data, "current", "vendor", "bin", "communicate"))) die("payload CLI missing");
if (!existsSync(path.join(data, "current", "vendor", "plugins", ".claude-plugin", "marketplace.json"))) die("marketplace file missing in payload");
// stabilized .mcp.json must not depend on the registry when deps travelled
const mcp = JSON.parse(readFileSync(path.join(data, "current", "vendor", "plugins", "communicate", ".mcp.json"), "utf8"));
if (existsSync(path.join(pkgDir, "node_modules")) && mcp.mcpServers.communicate.command !== "node") die("stabilized .mcp.json still uses npx");
// payload CLI actually runs
const agents = spawnSync(path.join(data, "current", "vendor", "bin", "communicate"), ["agents"], { encoding: "utf8", env });
if (agents.status !== 0) die("payload CLI failed: " + agents.stderr);

// 3. uninstall restores
r = runCli("setup", "--claude", "--uninstall");
if (r.status !== 0) die("uninstall exited " + r.status);
const s2 = JSON.parse(readFileSync(sp, "utf8"));
if (s2.extraKnownMarketplaces?.communicate || s2.enabledPlugins?.["communicate@communicate"]) die("uninstall left keys");
if (s2.sentinel !== "keep-me" || !s2.enabledPlugins["existing@mkt"]) die("uninstall damaged unrelated keys");
if (!existsSync(path.join(data, "current"))) die("uninstall should keep payload without --purge");

console.log("PASS: setup-smoke — dry-run inert, merge-not-clobber, backup, payload+symlink, mcp rewrite, uninstall restores");
