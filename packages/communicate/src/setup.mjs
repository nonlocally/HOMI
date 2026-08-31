// setup/doctor for @aadarwal/communicate — stabilize the payload, register
// both ecosystems, reversibly. Never touches CLI-owned plugin state files;
// never hand-edits ~/.codex/config.toml.
import { cpSync, existsSync, mkdirSync, readFileSync, writeFileSync, rmSync, symlinkSync, renameSync, readlinkSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import os from "node:os";
import path from "node:path";

const pkgDir = fileURLToPath(new URL("..", import.meta.url));
const pkg = JSON.parse(readFileSync(path.join(pkgDir, "package.json"), "utf8"));
const home = () => process.env.HOME || os.homedir();
const dataRoot = () => process.env.COMMUNICATE_DATA || path.join(home(), ".local", "share", "communicate");
const currentLink = () => path.join(dataRoot(), "current");
const settingsPath = () => path.join(home(), ".claude", "settings.json");
const MARKET_ID = "communicate";
const PLUGIN_ID = "communicate@communicate";

const log = (s) => console.log(s);
const hasCodex = () => !spawnSync("codex", ["--version"], { stdio: "ignore" }).error;

function parseFlags(argv) {
  const f = { claude: false, codex: false, dryRun: false, uninstall: false, purge: false, yes: false };
  for (const a of argv) {
    if (a === "--claude") f.claude = true;
    else if (a === "--codex") f.codex = true;
    else if (a === "--dry-run") f.dryRun = true;
    else if (a === "--uninstall") f.uninstall = true;
    else if (a === "--purge") f.purge = true;
    else if (a === "-y" || a === "--yes") f.yes = true;
    else { console.error(`unknown flag: ${a}`); process.exit(2); }
  }
  if (!f.claude && !f.codex) { f.claude = true; f.codex = true; }
  return f;
}

function stabilize(dry) {
  const dest = path.join(dataRoot(), pkg.version);
  const hasDeps = existsSync(path.join(pkgDir, "node_modules"));
  if (dry) {
    log(`[dry-run] would copy payload -> ${dest} (vendor, src, package.json${hasDeps ? ", node_modules" : ""})`);
    log(`[dry-run] would point symlink ${currentLink()} -> ${dest}`);
    return dest;
  }
  const tmp = dest + `.tmp-${process.pid}`;
  rmSync(tmp, { recursive: true, force: true });
  mkdirSync(tmp, { recursive: true });
  for (const item of ["vendor", "src", "package.json"]) cpSync(path.join(pkgDir, item), path.join(tmp, item), { recursive: true });
  if (hasDeps) cpSync(path.join(pkgDir, "node_modules"), path.join(tmp, "node_modules"), { recursive: true });
  // The installed plugin must not depend on the npm registry: point its MCP
  // entry at the stabilized files when the deps travelled with them.
  const mcpPath = path.join(tmp, "vendor", "plugins", "communicate", ".mcp.json");
  if (hasDeps && existsSync(mcpPath)) {
    const mcp = JSON.parse(readFileSync(mcpPath, "utf8"));
    mcp.mcpServers.communicate = { type: "stdio", command: "node", args: [path.join(currentLink(), "src", "cli.mjs"), "serve"] };
    writeFileSync(mcpPath, JSON.stringify(mcp, null, 2) + "\n");
  }
  rmSync(dest, { recursive: true, force: true });
  renameSync(tmp, dest);
  const linkTmp = currentLink() + `.tmp-${process.pid}`;
  rmSync(linkTmp, { force: true });
  symlinkSync(dest, linkTmp);
  renameSync(linkTmp, currentLink());
  log(`payload -> ${dest} (current -> ${dest})`);
  return dest;
}

function readSettings() {
  const p = settingsPath();
  if (!existsSync(p)) return {};
  try { return JSON.parse(readFileSync(p, "utf8")); }
  catch { console.error(`refusing to touch unparsable ${p} — fix it first`); process.exit(1); }
}

function writeSettings(obj, dry, why) {
  const p = settingsPath();
  if (dry) { log(`[dry-run] would ${why} in ${p}`); return; }
  mkdirSync(path.dirname(p), { recursive: true });
  if (existsSync(p)) cpSync(p, `${p}.communicate-backup-${Date.now()}`);
  writeFileSync(p, JSON.stringify(obj, null, 2) + "\n");
  log(`${why} in ${p} (backup written)`);
}

function claudeInstall(dry) {
  const marketRoot = path.join(currentLink(), "vendor", "plugins");
  const s = readSettings();
  s.extraKnownMarketplaces = { ...(s.extraKnownMarketplaces || {}), [MARKET_ID]: { source: { source: "directory", path: marketRoot } } };
  s.enabledPlugins = { ...(s.enabledPlugins || {}), [PLUGIN_ID]: true };
  writeSettings(s, dry, `add extraKnownMarketplaces.${MARKET_ID} + enabledPlugins["${PLUGIN_ID}"]`);
  log("Claude Code: new sessions load the plugin (skills, /agents, CLI on PATH, MCP tools).");
}

function claudeUninstall(dry) {
  const s = readSettings();
  if (s.extraKnownMarketplaces) delete s.extraKnownMarketplaces[MARKET_ID];
  if (s.enabledPlugins) delete s.enabledPlugins[PLUGIN_ID];
  writeSettings(s, dry, `remove extraKnownMarketplaces.${MARKET_ID} + enabledPlugins["${PLUGIN_ID}"]`);
}

function codexInstall(dry) {
  const marketRoot = path.join(currentLink(), "vendor");
  const cmds = [["plugin", "marketplace", "add", marketRoot], ["plugin", "add", PLUGIN_ID]];
  if (!hasCodex()) {
    log("codex CLI not found — run these once it is installed:");
    for (const c of cmds) log(`  codex ${c.join(" ")}`);
    log(`  # or MCP only: codex mcp add communicate -- npx -y @aadarwal/communicate serve`);
    return;
  }
  for (const c of cmds) {
    if (dry) { log(`[dry-run] would run: codex ${c.join(" ")}`); continue; }
    const r = spawnSync("codex", c, { encoding: "utf8" });
    const out = (r.stdout || "") + (r.stderr || "");
    if (r.status === 0) log(`codex ${c.join(" ")} — ok`);
    else { log(`codex ${c.join(" ")} — FAILED (${out.trim().slice(0, 200)}); run manually:`); log(`  codex ${c.join(" ")}`); }
  }
  log("Codex: start a NEW thread to see the skills and MCP tools.");
}

function codexUninstall(dry) {
  const cmds = [["plugin", "remove", PLUGIN_ID], ["plugin", "marketplace", "remove", MARKET_ID]];
  if (!hasCodex()) { for (const c of cmds) log(`  codex ${c.join(" ")}`); return; }
  for (const c of cmds) {
    if (dry) { log(`[dry-run] would run: codex ${c.join(" ")}`); continue; }
    const r = spawnSync("codex", c, { encoding: "utf8" });
    log(`codex ${c.join(" ")} — ${r.status === 0 ? "ok" : "failed (may not have been installed)"}`);
  }
}

export async function runSetup(argv) {
  const f = parseFlags(argv);
  if (f.uninstall) {
    if (f.claude) claudeUninstall(f.dryRun);
    if (f.codex) codexUninstall(f.dryRun);
    if (f.purge) { if (f.dryRun) log(`[dry-run] would remove ${dataRoot()}`); else { rmSync(dataRoot(), { recursive: true, force: true }); log(`removed ${dataRoot()}`); } }
    else log(`payload kept at ${dataRoot()} (add --purge to remove)`);
    return;
  }
  stabilize(f.dryRun);
  if (f.claude) claudeInstall(f.dryRun);
  if (f.codex) codexInstall(f.dryRun);
  if (!f.dryRun) log("done — run `communicate doctor` (or: npx -y @aadarwal/communicate doctor) to verify.");
}

export async function runDoctor() {
  const rows = [];
  const cur = currentLink();
  const payloadOk = existsSync(cur) && existsSync(path.join(cur, "vendor", "bin", "communicate"));
  const ver = payloadOk && existsSync(path.join(cur, "vendor", "VERSION")) ? readFileSync(path.join(cur, "vendor", "VERSION"), "utf8").trim() : "-";
  rows.push(["payload", payloadOk ? `ok (${ver} at ${payloadOk ? readlinkSync(cur) : "?"})` : "MISSING — run setup"]);
  const s = readSettings();
  rows.push(["claude marketplace", s.extraKnownMarketplaces?.[MARKET_ID] ? "ok" : "not registered"]);
  rows.push(["claude plugin enabled", s.enabledPlugins?.[PLUGIN_ID] ? "ok" : "not enabled"]);
  if (hasCodex()) {
    const r = spawnSync("codex", ["plugin", "list"], { encoding: "utf8" });
    rows.push(["codex plugin", (r.stdout || "").includes("communicate") ? "ok" : "not installed (or codex plugin list unsupported)"]);
  } else rows.push(["codex plugin", "codex CLI not found"]);
  for (const bin of ["bash", "python3", "ssh", "gh"]) rows.push([bin, spawnSync(bin, ["--version"], { stdio: "ignore" }).error ? "missing" : "ok"]);
  for (const [k, v] of rows) console.log(`  ${k.padEnd(24)} ${v}`);
}
