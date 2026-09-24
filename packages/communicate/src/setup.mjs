// setup/doctor for @aadarwal/communicate — stabilize the payload, register
// both ecosystems, reversibly. Never touches CLI-owned plugin state files;
// never hand-edits ~/.codex/config.toml.
import { cpSync, existsSync, mkdirSync, readFileSync, writeFileSync, rmSync, symlinkSync, renameSync, readlinkSync, realpathSync, chmodSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { copyRuntimeDependencies, hasRuntimeDependencies } from "./runtime-deps.mjs";
import { home, dataRoot, currentLink, ledgerPath, readJson, writeJson, hash, linkTarget, switchCurrent,
  withInstallLock, executable, stateRoot, daemonRequest, installService, uninstallService, unloadService, loadService } from "./lifecycle.mjs";

const pkgDir = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const pkg = JSON.parse(readFileSync(path.join(pkgDir, "package.json"), "utf8"));
const settingsPath = () => path.join(process.env.CLAUDE_CONFIG_DIR || path.join(home(), ".claude"), "settings.json");
const MARKET_ID = "communicate";
const PLUGIN_ID = "communicate@communicate";

const log = (s) => console.log(s);
const hasCodex = () => !spawnSync("codex", ["--version"], { stdio: "ignore" }).error;

function parseFlags(argv) {
  const f = { claude: false, codex: false, dryRun: false, uninstall: false, purge: false, yes: false, service: false, noClients: false };
  for (const a of argv) {
    if (a === "--claude") f.claude = true;
    else if (a === "--codex") f.codex = true;
    else if (a === "--dry-run") f.dryRun = true;
    else if (a === "--uninstall") f.uninstall = true;
    else if (a === "--purge") f.purge = true;
    else if (a === "--service") f.service = true;
    else if (a === "--no-service") { f.service = false; f.noService = true; }
    else if (a === "--no-clients") f.noClients = true;
    else if (a === "-y" || a === "--yes") f.yes = true;
    else throw new Error(`unknown flag: ${a}`);
  }
  if (f.noClients && (f.claude || f.codex)) throw new Error("--no-clients cannot be combined with --claude or --codex");
  if (f.service && f.noService) throw new Error("--service and --no-service cannot be combined");
  f.selective = f.claude || f.codex;
  if (!f.noClients && !f.claude && !f.codex) { f.claude = true; f.codex = true; }
  if (f.purge && !f.uninstall) throw new Error("--purge only applies to uninstall; identity state is always preserved");
  return f;
}

function stabilize(dry) {
  const manifestPath = path.join(pkgDir, "vendor/release.json");
  if (!existsSync(manifestPath)) throw new Error("Release manifest missing; build or reinstall the HOMI artifact");
  const manifest = readJson(manifestPath);
  if (manifest.version !== pkg.version) throw new Error("Package version and release manifest disagree");
  const manifestHash = hash(readFileSync(manifestPath)).slice(0, 12);
  const dest = path.join(dataRoot(), `${pkg.version}-${manifestHash}`);
  const hasDeps = hasRuntimeDependencies(pkgDir);
  if (!hasDeps) throw new Error("MCP runtime dependencies are missing; install the release package before setup");
  if (dry) {
    log(`[dry-run] would copy payload -> ${dest} (vendor, src, package.json${hasDeps ? ", node_modules" : ""})`);
    log(`[dry-run] would point symlink ${currentLink()} -> ${dest}`);
    return dest;
  }
  // Verify the source artifact before staging. Never replace an immutable
  // release directory in place: old processes and rollback keep using it.
  const verify = (root) => {
    for (const [prefix, files] of [["vendor", manifest.files], ["", manifest.packageFiles || {}]])
      for (const [name, expected] of Object.entries(files)) {
        const base = path.join(root, prefix), file = path.resolve(base, name);
        if (!file.startsWith(base + path.sep) || hash(readFileSync(file)) !== expected)
          throw new Error(`Release payload verification failed: ${name} in ${root}`);
      }
  };
  verify(pkgDir);
  if (existsSync(dest)) {
    if (readFileSync(path.join(dest, "vendor/release.json"), "utf8") !== readFileSync(manifestPath, "utf8"))
      throw new Error(`Release path collision: ${dest}`);
    verify(dest);
    return dest;
  }
  const tmp = dest + `.tmp-${process.pid}`;
  rmSync(tmp, { recursive: true, force: true });
  mkdirSync(tmp, { recursive: true });
  for (const item of ["vendor", "src", "package.json", "LICENSE"]) cpSync(path.join(pkgDir, item), path.join(tmp, item), { recursive: true });
  for (const file of ["cli.mjs", "homi.mjs"]) chmodSync(path.join(tmp, "src", file), 0o755);
  if (hasDeps) copyRuntimeDependencies(pkgDir, tmp);
  // The release's plugin launcher is relative to its bundled Node source.
  // Keep it immutable so repeat setup verifies the same release hashes.
  renameSync(tmp, dest);
  log(`payload staged -> ${dest}`);
  return dest;
}

function readSettings() {
  const p = settingsPath();
  if (!existsSync(p)) return {};
  try { return JSON.parse(readFileSync(p, "utf8")); }
  catch { throw new Error(`refusing to touch unparsable ${p} — fix it first`); }
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
  if (!executable("claude")) { log("Claude CLI unavailable; rerun homi setup --claude after installing it."); return false; }
  const s = readSettings();
  s.extraKnownMarketplaces = { ...(s.extraKnownMarketplaces || {}), [MARKET_ID]: { source: { source: "directory", path: marketRoot } } };
  s.enabledPlugins = { ...(s.enabledPlugins || {}), [PLUGIN_ID]: true };
  writeSettings(s, dry, `add extraKnownMarketplaces.${MARKET_ID} + enabledPlugins["${PLUGIN_ID}"]`);
  const commands = [["plugin", "marketplace", "add", marketRoot], ["plugin", "update", PLUGIN_ID, "--scope", "user"]];
  if (dry) {
    for (const args of commands) log(`[dry-run] would run: claude ${args.join(" ")}`);
    return true;
  }
  const probe = spawnSync("claude", ["--version"], {stdio: "ignore", timeout: 30000});
  if (probe.error?.code === "ENOENT") {
    log("Claude CLI not found; after installing it, rerun this archive's communicate setup --claude.");
    return;
  }
  const run = (args) => spawnSync("claude", args, {encoding: "utf8", stdio: ["ignore", "pipe", "pipe"], timeout: 30000}).status === 0;
  if (!run(commands[0])) throw new Error("Claude marketplace registration failed; rerun this archive's communicate setup --claude.");
  if (!run(commands[1]) && !run(["plugin", "install", PLUGIN_ID, "--scope", "user"]))
    throw new Error(`Claude plugin refresh failed; run claude plugin update ${PLUGIN_ID} --scope user.`);
  const expected = JSON.parse(readFileSync(path.join(marketRoot, "communicate", ".claude-plugin", "plugin.json"), "utf8")).version;
  const listed = spawnSync("claude", ["plugin", "list", "--json"], {encoding: "utf8", timeout: 30000});
  let installed;
  try { installed = listed.status === 0 ? JSON.parse(listed.stdout) : []; } catch { installed = []; }
  if (!Array.isArray(installed) || !installed.some((p) => p.id === PLUGIN_ID && p.scope === "user" && p.version === expected))
    throw new Error(`Claude plugin version verification failed; run claude plugin update ${PLUGIN_ID} --scope user.`);
  log("Claude plugin installed/refreshed via CLI. Restart Claude Code to load HOMI's skills, commands, CLI, and MCP tools.");
  return true;
}

function claudeUninstall(dry, record = {}) {
  const s = readSettings();
  if (s.extraKnownMarketplaces?.[MARKET_ID]?.source?.path !== path.join(currentLink(), "vendor/plugins")) {
    log("kept Claude registration: it no longer points at this installation"); return false;
  }
  if (record.previousMarket) s.extraKnownMarketplaces[MARKET_ID] = record.previousMarket;
  else delete s.extraKnownMarketplaces[MARKET_ID];
  if (s.enabledPlugins?.[PLUGIN_ID] === true) {
    if (record.previousEnabled !== undefined) s.enabledPlugins[PLUGIN_ID] = record.previousEnabled;
    else delete s.enabledPlugins[PLUGIN_ID];
  }
  writeSettings(s, dry, "remove owned HOMI plugin settings (restore previous values)");
  if (!dry && executable("claude")) {
    spawnSync("claude", ["plugin", "uninstall", PLUGIN_ID, "--scope", "user"], { encoding: "utf8", timeout: 30000 });
    if (record.previousMarket?.source?.path) {
      spawnSync("claude", ["plugin", "marketplace", "add", record.previousMarket.source.path], { encoding: "utf8", timeout: 30000 });
      if (record.previousEnabled) spawnSync("claude", ["plugin", "install", PLUGIN_ID, "--scope", "user"], { encoding: "utf8", timeout: 30000 });
    }
  }
  return true;
}

function codexMarketplace() {
  if (!executable("codex")) return null;
  const r = spawnSync("codex", ["plugin", "marketplace", "list", "--json"], { encoding: "utf8", timeout: 30000 });
  if (r.status !== 0) return null;
  try {
    const rows = JSON.parse(r.stdout);
    return (Array.isArray(rows) ? rows : rows.marketplaces || []).find((m) => m.name === MARKET_ID) || null;
  } catch { return null; }
}
function codexInstall(dry) {
  const marketRoot = path.join(currentLink(), "vendor");
  const cmds = [["plugin", "marketplace", "add", marketRoot], ["plugin", "add", PLUGIN_ID]];
  if (!hasCodex()) {
    log("codex CLI not found — run these once it is installed:");
    for (const c of cmds) log(`  codex ${c.join(" ")}`);
    log("After installing Codex, rerun this archive's communicate setup --codex to register its local MCP payload.");
    return false;
  }
  for (const c of cmds) {
    if (dry) { log(`[dry-run] would run: codex ${c.join(" ")}`); continue; }
    const r = spawnSync("codex", c, { encoding: "utf8" });
    const out = (r.stdout || "") + (r.stderr || "");
    if (r.status === 0) log(`codex ${c.join(" ")} — ok`);
    else {
      // Switching a registered checkout to this release is explicit setup.
      if (c[1] === "marketplace" && c[2] === "add") {
        spawnSync("codex", ["plugin", "marketplace", "remove", MARKET_ID], { encoding: "utf8" });
        if (spawnSync("codex", c, { encoding: "utf8" }).status === 0) continue;
      }
      throw new Error(`codex ${c.join(" ")} failed (${out.trim().slice(0, 200)})`);
    }
  }
  log("Codex: start a NEW thread to see the skills and MCP tools.");
  return true;
}

function codexUninstall(dry, record = {}) {
  const market = codexMarketplace();
  const ownedRoot = path.join(currentLink(), "vendor");
  if (!market || (market.root !== ownedRoot && market.marketplaceSource?.source !== ownedRoot)) {
    log("kept Codex registration: this installation's ownership could not be verified"); return false;
  }
  const cmds = [["plugin", "remove", PLUGIN_ID], ["plugin", "marketplace", "remove", MARKET_ID]];
  if (!hasCodex()) return false;
  for (const c of cmds) {
    if (dry) { log(`[dry-run] would run: codex ${c.join(" ")}`); continue; }
    const r = spawnSync("codex", c, { encoding: "utf8" });
    log(`codex ${c.join(" ")} — ${r.status === 0 ? "ok" : "failed (may not have been installed)"}`);
  }
  if (!dry && record.previousRoot) {
    const r = spawnSync("codex", ["plugin", "marketplace", "add", record.previousRoot], { encoding: "utf8", timeout: 30000 });
    if (r.status !== 0) throw new Error(`Previous Codex marketplace could not be restored: ${record.previousRoot}`);
  }
  return true;
}

function restoreCodex(market) {
  // CLI-owned registry files are never edited directly.
  spawnSync("codex", ["plugin", "marketplace", "remove", MARKET_ID], { encoding: "utf8", timeout: 30000 });
  const previous = market?.marketplaceSource?.source || market?.root;
  if (previous) spawnSync("codex", ["plugin", "marketplace", "add", previous], { encoding: "utf8", timeout: 30000 });
}

function restoreService(record) {
  unloadService(record);
  if (record.backup) {
    cpSync(record.backup, record.path);
    loadService(record);
  } else rmSync(record.path, { force: true });
}

export async function runSetup(argv) {
  const f = parseFlags(argv);
  if (f.dryRun) {
    if (f.uninstall) {
      log(`[dry-run] would remove only owned registrations and executable links; preserve ${stateRoot()}`);
      const saved = readJson(ledgerPath());
      if (!f.selective) await uninstallService(saved.service, true);
    } else {
      stabilize(true);
      if (f.claude) claudeInstall(true);
      if (f.codex) codexInstall(true);
      if (f.service) await installService(true);
    }
    return;
  }
  await withInstallLock(async () => {
    const saved = readJson(ledgerPath(), { schema: 1, releases: [], clients: {}, links: {} });
    saved.clients ||= {}; saved.links ||= {}; saved.releases ||= [];
    if (f.uninstall) {
      if (f.claude && saved.clients.claude && claudeUninstall(false, saved.clients.claude)) delete saved.clients.claude;
      if (f.codex && saved.clients.codex && codexUninstall(false, saved.clients.codex)) delete saved.clients.codex;
      if (!f.selective && await uninstallService(saved.service)) delete saved.service;
      if (!f.selective) for (const [file, target] of Object.entries(saved.links)) {
        try {
          if (readlinkSync(file) === target) { rmSync(file); delete saved.links[file]; }
          else log(`kept changed executable link: ${file}`);
        } catch (e) { if (e.code === "ENOENT") delete saved.links[file]; else log(`kept user-owned executable: ${file}`); }
      }
      if (f.purge) {
        if (f.selective || Object.keys(saved.clients).length || saved.service || Object.keys(saved.links).length)
          throw new Error("Cannot purge while integrations remain; inspect ownership changes with homi doctor");
        const runtime = path.resolve(stateRoot());
        for (const release of saved.releases) {
          const resolved = path.resolve(release);
          if (path.dirname(resolved) !== path.resolve(dataRoot()) || !existsSync(path.join(resolved, "vendor/release.json")))
            throw new Error(`Refusing unrecognized release directory: ${release}`);
          if (runtime === resolved || runtime.startsWith(resolved + path.sep)) throw new Error("Runtime state is inside a release; move it before purging code");
        }
        switchCurrent(null);
        for (const release of saved.releases) rmSync(release, { recursive: true, force: true });
        saved.current = null; saved.previous = null; saved.releases = [];
      }
      writeJson(ledgerPath(), saved);
      log(`uninstalled owned integrations; identities, mail, credentials and configuration preserved at ${stateRoot()}`);
      if (!f.purge) log(`release payloads retained at ${dataRoot()} for rollback/reinstall`);
      return;
    }
    const previous = linkTarget();
    const before = f.claude ? readSettings() : {};
    const codexBefore = f.codex ? codexMarketplace() : null;
    const dest = stabilize(false);
    const next = structuredClone(saved);
    const bins = path.join(dataRoot(), "bin");
    const linksBefore = new Map();
    for (const [name, entry] of [["homi", "homi.mjs"], ["communicate", "cli.mjs"]]) {
      const file = path.join(bins, name), target = `../current/src/${entry}`;
      let existing = null;
      try { existing = readlinkSync(file); }
      catch (e) { if (e.code !== "ENOENT") throw new Error(`Refusing to replace user-owned executable: ${file}`); }
      if (existing !== null && existing !== target && existing !== saved.links[file])
        throw new Error(`Refusing to replace user-owned executable: ${file}`);
      linksBefore.set(file, existing);
      next.links[file] = target;
    }
    let serviceChanged = false;
    try {
      switchCurrent(dest);
      if (f.claude && claudeInstall(false)) next.clients.claude ||= {
        previousMarket: before.extraKnownMarketplaces?.[MARKET_ID]?.source?.path === path.join(currentLink(), "vendor/plugins")
          ? undefined : before.extraKnownMarketplaces?.[MARKET_ID], previousEnabled: before.enabledPlugins?.[PLUGIN_ID] };
      const previousRoot = codexBefore?.marketplaceSource?.source || codexBefore?.root;
      if (f.codex && codexInstall(false)) next.clients.codex ||= {
        previousRoot: previousRoot === path.join(currentLink(), "vendor") ? undefined : previousRoot };
      mkdirSync(bins, { recursive: true });
      for (const file of linksBefore.keys()) {
        rmSync(file, { force: true });
        symlinkSync(next.links[file], file);
      }
      if (!f.noService && (f.service || saved.service)) {
        next.service = await installService(false, () => switchCurrent(previous));
        serviceChanged = true;
      }
      next.current = dest;
      if (previous && previous !== dest) next.previous = previous;
      next.releases = [...new Set([...next.releases, dest])];
      next.source = readJson(path.join(dest, "vendor/release.json")).source;
      writeJson(ledgerPath(), next);
      log(`current -> ${dest}`);
      log(`CLI: ${path.join(bins, "homi")} (add ${bins} to PATH if needed)`);
      log(serviceChanged ? "durable daemon service installed" : "daemon service unchanged; use homi start or homi setup --service when needed");
      log("done — run the installed homi doctor; local operation needs no hosted invitation.");
    } catch (error) {
      switchCurrent(previous);
      for (const [file, target] of linksBefore) {
        rmSync(file, { force: true });
        if (target !== null) symlinkSync(target, file);
      }
      if (serviceChanged) restoreService(next.service);
      // Restore just the keys this installer owns, preserving unrelated edits.
      if (f.claude) {
        const now = readSettings();
        for (const [parent, key] of [["extraKnownMarketplaces", MARKET_ID], ["enabledPlugins", PLUGIN_ID]]) {
          now[parent] ||= {};
          if (before[parent]?.[key] === undefined) delete now[parent][key];
          else now[parent][key] = before[parent][key];
        }
        writeSettings(now, false, "restore plugin settings after failed activation");
      }
      if (f.codex) restoreCodex(codexBefore);
      throw new Error(`Activation failed; previous payload restored. ${error.message}`);
    }
  });
}

export async function runRollback(argv = []) {
  if (argv.some((a) => a !== "--dry-run")) throw new Error("usage: homi rollback [--dry-run]");
  const saved = readJson(ledgerPath());
  if (!saved.previous || !existsSync(path.join(saved.previous, "vendor/release.json"))) throw new Error("No retained previous release to roll back to");
  if (argv.includes("--dry-run")) { log(`[dry-run] would restore ${saved.previous}; identities and mail stay at ${stateRoot()}`); return; }
  await withInstallLock(async () => {
    const from = linkTarget();
    try {
      switchCurrent(saved.previous);
      if (saved.clients?.claude) claudeInstall(false);
      if (saved.clients?.codex) codexInstall(false);
      if (saved.service) saved.service = await installService(false, () => switchCurrent(from));
      saved.current = saved.previous; saved.previous = from;
      writeJson(ledgerPath(), saved);
      log(`rolled back to ${saved.current}; runtime state preserved`);
    } catch (error) { switchCurrent(from); throw new Error(`Rollback activation failed; original payload restored: ${error.message}`); }
  });
}

export async function runDoctor() {
  const rows = [];
  const cur = currentLink();
  const payloadOk = existsSync(cur) && existsSync(path.join(cur, "vendor", "bin", "communicate"));
  const ver = payloadOk && existsSync(path.join(cur, "vendor", "VERSION")) ? readFileSync(path.join(cur, "vendor", "VERSION"), "utf8").trim() : "-";
  rows.push(["invoked package", pkgDir]);
  rows.push(["package version", pkg.version]);
  rows.push(["payload", payloadOk ? `ok (${ver} at ${realpathSync(cur)})` : "MISSING — run setup"]);
  rows.push(["runtime state", stateRoot()]);
  for (const command of ["homi", "communicate"]) {
    const resolved = executable(command);
    rows.push([`${command} on PATH`, resolved ? `${resolved} -> ${realpathSync(resolved)}` : "not on PATH"]);
  }
  if (payloadOk) {
    const manifest = readJson(path.join(cur, "vendor/release.json"));
    rows.push(["source commit", `${manifest.source?.commit || "unknown"}${manifest.source?.dirty ? " (modified checkout)" : ""}`]);
    rows.push(["installed MCP", path.join(cur, "src/cli.mjs")]);
    rows.push(["installed daemon", path.join(cur, "vendor/lib/homi.py")]);
  }
  const s = readSettings();
  rows.push(["claude marketplace", s.extraKnownMarketplaces?.[MARKET_ID]?.source?.path || "not registered"]);
  rows.push(["claude plugin enabled", s.enabledPlugins?.[PLUGIN_ID] ? "ok" : "not enabled"]);
  if (hasCodex()) {
    const r = spawnSync("codex", ["plugin", "list"], { encoding: "utf8" });
    rows.push(["codex plugin", (r.stdout || "").includes("communicate") ? "ok" : "not installed (or codex plugin list unsupported)"]);
    rows.push(["codex marketplace", codexMarketplace()?.root || "not registered/unknown"]);
  } else rows.push(["codex plugin", "codex CLI not found"]);
  if (executable("claude")) {
    const r = spawnSync("claude", ["plugin", "list", "--json"], { encoding: "utf8", timeout: 10000 });
    try { rows.push(["Claude cached version", JSON.parse(r.stdout).find((p) => p.id === PLUGIN_ID)?.version || "not installed"]); } catch {}
  }
  const daemon = await daemonRequest();
  rows.push(["running daemon", daemon?.ok ? `pid=${daemon.self?.pid} version=${daemon.self?.version} state=${daemon.self?.state_root}` : "not running (not started by doctor)"]);
  if (daemon?.self?.source_file) rows.push(["daemon loaded source", daemon.self.source_file]);
  if (daemon?.self?.source_commit) rows.push(["daemon loaded commit", daemon.self.source_commit]);
  if (daemon?.self?.user?.handle) rows.push(["user claimed", `@${daemon.self.user.handle}`]);
  if (payloadOk && daemon?.ok) {
    const expected = realpathSync(path.join(cur, "vendor/lib/homi.py"));
    rows.push(["daemon release parity", daemon.self.source_file === expected ? "current" : "different/unreported source; restart explicitly with homi setup --service"]);
  }
  const orphan = path.join(process.env.XDG_STATE_HOME || path.join(home(), ".local/state"), "homi");
  if (path.resolve(orphan) !== path.join(path.resolve(stateRoot()), "homi") && existsSync(path.join(orphan, "mail"))) {
    rows.push(["orphaned legacy state", `${orphan} contains mail; inspect before migration or removal`]);
    process.exitCode = 1;
  }
  if (daemon?.self?.pid) {
    const r = spawnSync("ps", ["-p", String(daemon.self.pid), "-o", "command="], { encoding: "utf8", timeout: 5000 });
    rows.push(["daemon executable", r.status === 0 ? r.stdout.trim() : "process unavailable"]);
  }
  const ledger = readJson(ledgerPath());
  rows.push(["managed service", ledger.service?.path || "none installed by this setup"]);
  for (const bin of ["bash", "python3", "node", "tmux", "ssh", "gh", "tailscale"]) rows.push([bin, executable(bin) || "unavailable (capability requires it)"]);
  rows.push(["client activation", "start fresh client sessions to load refreshed integrations; cached files do not prove a running client loaded them"]);
  for (const [k, v] of rows) console.log(`  ${k.padEnd(24)} ${v}`);
}
