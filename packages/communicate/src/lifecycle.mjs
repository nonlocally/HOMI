// Installation mechanics only. Identity, mail and execution remain in the kernel.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import net from "node:net";
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";

export const home = () => process.env.HOME || os.homedir();
export const dataRoot = () => process.env.COMMUNICATE_DATA || path.join(home(), ".local/share/communicate");
export const stateRoot = () => process.env.COMM_STATE || path.join(process.env.XDG_STATE_HOME || path.join(home(), ".local/state"), "communicate");
export const currentLink = () => path.join(dataRoot(), "current");
export const ledgerPath = () => path.join(dataRoot(), "install.json");
export const readJson = (p, fallback = {}) => fs.existsSync(p) ? JSON.parse(fs.readFileSync(p, "utf8")) : fallback;
export const hash = (data) => createHash("sha256").update(data).digest("hex");
export function assertManagedPath(target, boundary = home()) {
  const base = path.resolve(boundary), absolute = path.resolve(target);
  // HOME may itself be a deliberately configured alias. Resolve that boundary
  // once; aliases inside it are not permission to modify their destinations.
  const realBase = fs.existsSync(base) ? fs.realpathSync(base) : base;
  const mapped = absolute === base || absolute.startsWith(base + path.sep)
    ? path.join(realBase, path.relative(base, absolute)) : absolute;
  let current = path.parse(mapped).root;
  const components = mapped.slice(current.length).split(path.sep).filter(Boolean);
  for (let i = 0; i < components.length; i++) {
    current = path.join(current, components[i]);
    let stat;
    try { stat = fs.lstatSync(current); } catch (e) { if (e.code === "ENOENT") continue; throw e; }
    if (stat.isSymbolicLink()) {
      // macOS /tmp and /var are root-owned platform aliases, above user roots.
      if (i !== components.length - 1 && stat.uid === 0 &&
          !(current === realBase || current.startsWith(realBase + path.sep))) {
        current = fs.realpathSync(current); continue;
      }
      throw new Error(`Refusing symlinked managed path: ${current}`);
    }
    if (stat.uid !== process.getuid() && (stat.uid !== 0 || i === components.length - 1))
      throw new Error(`Managed path is not owned by this user: ${current}`);
  }
  return mapped;
}
export function writeJson(p, value) {
  assertManagedPath(p);
  fs.mkdirSync(path.dirname(p), { recursive: true, mode: 0o700 });
  const staging = fs.mkdtempSync(path.join(path.dirname(p), ".homi-write-"));
  try {
    const tmp = path.join(staging, "value");
    fs.writeFileSync(tmp, JSON.stringify(value, null, 2) + "\n", { mode: 0o600, flag: "wx" });
    fs.renameSync(tmp, p);
  } finally { fs.rmSync(staging, { recursive: true, force: true }); }
}
export function linkTarget(link = currentLink()) {
  try { return path.resolve(path.dirname(link), fs.readlinkSync(link)); }
  catch (error) { if (error.code === "ENOENT") return null; throw new Error(`Refusing non-symlink installation pointer: ${link}`); }
}
export function switchCurrent(target) {
  assertManagedPath(dataRoot());
  const tmp = currentLink() + `.tmp-${process.pid}`;
  fs.rmSync(tmp, { force: true });
  if (target === null) { fs.rmSync(currentLink(), { force: true }); return; }
  fs.symlinkSync(target, tmp);
  fs.renameSync(tmp, currentLink());
}
export async function withInstallLock(action) {
  assertManagedPath(dataRoot());
  fs.mkdirSync(dataRoot(), { recursive: true, mode: 0o700 });
  fs.chmodSync(dataRoot(), 0o700);
  const lock = path.join(dataRoot(), "install.lock");
  try { fs.mkdirSync(lock); }
  catch { throw new Error(`Another installation may be active (${lock}); inspect its owner before removing a stale lock`); }
  fs.writeFileSync(path.join(lock, "pid"), String(process.pid));
  try { return await action(); } finally { fs.rmSync(lock, { recursive: true, force: true }); }
}
export function executable(name) {
  if (name.includes(path.sep)) return name;
  for (const dir of (process.env.PATH || "").split(path.delimiter)) {
    const candidate = path.join(dir, name);
    try { fs.accessSync(candidate, fs.constants.X_OK); return candidate; } catch {}
  }
  return null;
}
export function python() {
  const candidate = executable(process.env.HOMI_PYTHON || "python3");
  if (!candidate || spawnSync(candidate, ["-B", "-c", "import sys; assert sys.version_info >= (3,9)"], { stdio: "ignore", timeout: 10000 }).status !== 0)
    throw new Error("HOMI requires Python 3.9 or later; set HOMI_PYTHON to its executable");
  return candidate;
}
const xml = (s) => String(s).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
const unit = (s) => '"' + String(s).replaceAll("\\", "\\\\").replaceAll('"', '\\"').replaceAll("%", "%%").replaceAll("\n", "\\n") + '"';
const INHERITABLE = ["PATH", "HOMI_SOCK_DIR", "HOMI_SESSIONS_DIR", "CLAUDE_CONFIG_DIR", "CODEX_HOME", "HOMI_TMUX_SOCKET"];
const canonical = (p) => {
  let existing = path.resolve(p), tail = [];
  while (!fs.existsSync(existing)) { tail.unshift(path.basename(existing)); existing = path.dirname(existing); }
  return path.join(fs.realpathSync(existing), ...tail);
};
function serviceScope() {
  // os.homedir() honors $HOME; userInfo() reads the actual account database.
  const accountHome = canonical(os.userInfo().homedir);
  const scope = { home: canonical(home()), data: canonical(dataRoot()), state: canonical(stateRoot()) };
  const standard = scope.home === accountHome && scope.data === path.join(accountHome, ".local/share/communicate") &&
    scope.state === path.join(accountHome, ".local/state/communicate");
  return { ...scope, standard, suffix: standard ? "" : hash(JSON.stringify(scope)).slice(0, 12) };
}
export function serviceDefinition(platform, py, device, options = {}) {
  const daemon = path.join(currentLink(), "vendor/lib/homi.py");
  const scope = serviceScope();
  const vars = { HOME: home(), COMM_STATE: stateRoot(), HOMI_SELF: device,
    PATH: [...new Set([path.dirname(py), "/opt/homebrew/bin", "/usr/local/bin", path.join(home(), ".local/bin"), "/usr/bin", "/bin", "/usr/sbin", "/sbin"])].join(path.delimiter) };
  for (const name of INHERITABLE) if (options.environment?.[name] !== undefined) vars[name] = options.environment[name];
  for (const name of options.inherit || []) {
    if (!INHERITABLE.includes(name)) throw new Error(`Unsupported inherited service variable: ${name}`);
    if (process.env[name] === undefined) throw new Error(`Cannot inherit unset service variable: ${name}`);
    vars[name] = process.env[name];
  }
  const macLabel = "com.communicate.homi" + (scope.suffix ? "." + scope.suffix : "");
  const linuxLabel = "communicate-homi" + (scope.suffix ? "-" + scope.suffix : "") + ".service";
  if (platform === "darwin") return { platform, label: macLabel, scope, environment: vars, python: py,
    path: path.join(home(), "Library/LaunchAgents", macLabel + ".plist"),
    content: `<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n<plist version="1.0"><dict>
<key>Label</key><string>${macLabel}</string>
<key>ProgramArguments</key><array><string>${xml(py)}</string><string>${xml(daemon)}</string><string>daemon</string></array>
<key>EnvironmentVariables</key><dict>${Object.entries(vars).map(([k,v]) => `<key>${xml(k)}</key><string>${xml(v)}</string>`).join("")}</dict>
<key>RunAtLoad</key><true/><key>KeepAlive</key><true/><key>ProcessType</key><string>Interactive</string>
<key>StandardOutPath</key><string>${xml(path.join(stateRoot(), "homi/launchd.log"))}</string>
<key>StandardErrorPath</key><string>${xml(path.join(stateRoot(), "homi/launchd.log"))}</string>
</dict></plist>\n` };
  if (platform === "linux") return { platform, label: linuxLabel, scope, environment: vars, python: py,
    path: path.join(process.env.XDG_CONFIG_HOME || path.join(home(), ".config"), "systemd/user", linuxLabel),
    content: `[Unit]\nDescription=HOMI durable agent identities\n[Service]\nExecStart=${unit(py)} ${unit(daemon)} daemon\n${Object.entries(vars).map(([k,v]) => `Environment=${unit(k + "=" + v)}`).join("\n")}\nRestart=always\nRestartSec=2\n[Install]\nWantedBy=default.target\n` };
  throw new Error("Persistent services are supported on macOS and Linux only");
}
function serviceRun(command, args, allowFailure = false) {
  const result = spawnSync(command, args, { encoding: "utf8", timeout: 30000 });
  if (!allowFailure && (result.error || result.status !== 0))
    throw new Error(`${command} ${args.join(" ")} failed: ${(result.stderr || result.error?.message || "").trim()}`);
}
function serviceState(definition) {
  const expected = serviceDefinition(definition.platform, definition.python || "python3", "scope-check");
  if (definition.label !== expected.label || canonical(definition.path) !== canonical(expected.path))
    throw new Error("Service ownership belongs to another HOME/state scope; refusing service-manager action");
  assertManagedPath(definition.path);
  const result = definition.platform === "darwin"
    ? spawnSync("launchctl", ["print", `gui/${process.getuid()}/${definition.label}`], { encoding: "utf8", timeout: 10000, stdio: ["ignore", "pipe", "pipe"] })
    : spawnSync("systemctl", ["--user", "show", definition.label, "--property=FragmentPath", "--property=ActiveState"], { encoding: "utf8", timeout: 10000, stdio: ["ignore", "pipe", "pipe"] });
  if (result.error) throw new Error(`Could not inspect service ownership: ${result.error.message}`);
  if (result.status !== 0) {
    if (/could not find service|not found|not loaded|does not exist/i.test((result.stderr || "") + (result.stdout || ""))) return { active: false };
    throw new Error(`Could not verify service ownership: ${(result.stderr || result.stdout || "unknown manager error").trim()}`);
  }
  const activePath = definition.platform === "darwin"
    ? result.stdout.match(/^\s*path = (.+)$/m)?.[1]?.trim()
    : result.stdout.match(/^FragmentPath=(.*)$/m)?.[1]?.trim();
  if (!activePath && definition.platform === "linux") return { active: false };
  if (!activePath || canonical(activePath) !== canonical(definition.path))
    throw new Error(`Refusing to replace a loaded service from another/unknown unit: ${activePath || definition.label}`);
  const running = definition.platform === "darwin" || /^(active|activating|reloading)$/.test(result.stdout.match(/^ActiveState=(.*)$/m)?.[1] || "");
  return { active: running, path: activePath };
}
export function unloadService(definition) {
  if (!serviceState(definition).active) return;
  if (definition.platform === "darwin") serviceRun("launchctl", ["bootout", `gui/${process.getuid()}/${definition.label}`], true);
  else serviceRun("systemctl", ["--user", "disable", "--now", definition.label], true);
}
export function loadService(definition) {
  serviceState(definition); // A colliding manager label is never ours to replace.
  if (definition.platform === "darwin") serviceRun("launchctl", ["bootstrap", `gui/${process.getuid()}`, definition.path]);
  else {
    serviceRun("systemctl", ["--user", "daemon-reload"]);
    serviceRun("systemctl", ["--user", "enable", "--now", definition.label]);
  }
}
export function daemonRequest(op = "status", timeout = 3000, serviceControl = false) {
  // A diagnosis never autostarts services. Preserve control authentication.
  if (serviceControl) {
    for (const name of ["homi", "homi/control.token", "homi/homi.sock"])
      assertManagedPath(path.join(stateRoot(), name));
  }
  return new Promise((resolve) => {
    const root = path.join(stateRoot(), "homi");
    const request = { op };
    const token = path.join(root, "control.token");
    if (fs.existsSync(token)) request.auth = fs.readFileSync(token, "utf8").trim();
    const socket = net.createConnection((!serviceControl && process.env.HOMI_SOCK) || path.join(root, "homi.sock"));
    let text = "", done = false;
    const finish = (value) => { if (!done) { done = true; clearTimeout(timer); socket.destroy(); resolve(value); } };
    const timer = setTimeout(() => finish(null), timeout);
    socket.on("error", () => finish(null));
    socket.on("connect", () => socket.end(JSON.stringify(request) + "\n"));
    socket.on("data", (data) => {
      text += data;
      if (text.length > 8 * 1024 * 1024) return finish(null);
      if (text.includes("\n")) { try { finish(JSON.parse(text.split("\n")[0])); } catch { finish(null); } }
    });
    socket.on("end", () => finish(null));
  });
}
function writeService(file, data, mode = 0o600) {
  assertManagedPath(file);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const staging = fs.mkdtempSync(path.join(path.dirname(file), ".homi-unit-"));
  try {
    const tmp = path.join(staging, "unit");
    fs.writeFileSync(tmp, data, { mode, flag: "wx" });
    fs.renameSync(tmp, file);
  } finally { fs.rmSync(staging, { recursive: true, force: true }); }
}
function originalService(record) {
  if (Object.hasOwn(record || {}, "original")) return record.original;
  // Older installer records had one backup. Preserve that available lineage;
  // never replace it with another copy of HOMI's own generated unit.
  if (record?.backup) {
    assertManagedPath(record.backup);
    return { backup: record.backup, hash: hash(fs.readFileSync(record.backup)), mode: 0o600, active: true };
  }
  return undefined;
}
function originalBytes(original) {
  if (!original) return null;
  assertManagedPath(original.backup);
  const bytes = fs.readFileSync(original.backup);
  if (hash(bytes) !== original.hash) throw new Error("Original service backup changed; refusing service mutation");
  return bytes;
}
export async function installService(dry = false, beforeRestore = () => {}, priorRecord = null, options = {}) {
  const py = python();
  assertManagedPath(stateRoot());
  const incumbent = dry ? null : await daemonRequest("status", 3000, true);
  const device = process.env.HOMI_SELF || incumbent?.self?.device || os.hostname().split(".")[0].toLowerCase();
  const definition = serviceDefinition(process.platform, py, device, { ...options, environment: priorRecord?.environment });
  assertManagedPath(definition.path);
  const receipt = { label: definition.label, scope: definition.scope, path: definition.path, python: py, environment: definition.environment };
  if (dry) { console.log(`[dry-run] would verify/update service ${JSON.stringify(receipt)}`); return null; }
  const active = serviceState(definition); // Inspect BEFORE bootout/stop or writes.
  if (priorRecord && (priorRecord.path !== definition.path || priorRecord.label !== definition.label))
    throw new Error("Recorded service belongs to another HOME/state scope; migrate it explicitly");
  if (priorRecord && fs.existsSync(definition.path) && hash(fs.readFileSync(definition.path)) !== priorRecord.hash)
    throw new Error("Service unit changed outside this installation; refusing to overwrite it");
  let original = originalService(priorRecord);
  if (original !== undefined) originalBytes(original);
  const expectedSource = fs.realpathSync(path.join(currentLink(), "vendor/lib/homi.py"));
  if (priorRecord && fs.existsSync(definition.path) && active.active && hash(definition.content) === priorRecord.hash && incumbent?.ok && incumbent.self?.source_file === expectedSource) {
    console.log(`daemon unchanged (release and unit unchanged): ${JSON.stringify(receipt)}`);
    return priorRecord;
  }
  fs.mkdirSync(path.join(stateRoot(), "homi"), { recursive: true, mode: 0o700 });
  fs.mkdirSync(path.dirname(definition.path), { recursive: true });
  const before = fs.existsSync(definition.path) ? fs.readFileSync(definition.path) : null;
  const beforeMode = before === null ? 0o600 : fs.statSync(definition.path).mode & 0o777;
  let backup = null;
  if (before !== null) {
    backup = path.join(dataRoot(), "service-backups", hash(before) + ".original");
    assertManagedPath(backup);
    fs.mkdirSync(path.dirname(backup), { recursive: true, mode: 0o700 });
    if (fs.existsSync(backup)) {
      if (hash(fs.readFileSync(backup)) !== hash(before)) throw new Error("Service backup collision");
    } else fs.writeFileSync(backup, before, { mode: 0o600, flag: "wx" });
  }
  if (original === undefined && before === null) original = null;
  else if (original === undefined) {
    original = { backup, hash: hash(before), mode: beforeMode, active: active.active };
  }
  unloadService(definition);
  await daemonRequest("stop", 3000, true);
  // Stop replies precede the daemon's delayed shutdown; wait on the socket.
  for (let i = 0; i < 30 && await daemonRequest("status", 3000, true); i++) await new Promise((r) => setTimeout(r, 100));
  try {
    writeService(definition.path, definition.content);
    loadService(definition);
    for (let i = 0; i < 50; i++) {
      const status = await daemonRequest("status", 3000, true);
      if (status?.ok && status.self?.source_file === expectedSource) {
        console.log(`managed service: ${JSON.stringify(receipt)}`);
        return { ...definition, hash: hash(definition.content), original, backup, previousActive: active.active, previousMode: beforeMode, state: stateRoot() };
      }
      await new Promise((r) => setTimeout(r, 200));
    }
    throw new Error("Service did not answer from the selected release; inspect its preserved logs and homi doctor");
  } catch (error) {
    unloadService(definition);
    // The previous unit may itself use /current. Restore that pointer before
    // restarting it, otherwise a failed upgrade can relaunch the new payload.
    beforeRestore();
    if (before === null) fs.rmSync(definition.path, { force: true });
    else {
      writeService(definition.path, before, beforeMode);
      try { if (active.active) loadService(definition); }
      catch (restoreError) { throw new Error(`${error.message}; previous service also failed to restart: ${restoreError.message}`); }
    }
    throw error;
  }
}
export async function uninstallService(record, dry = false) {
  if (!record) return true;
  assertManagedPath(record.path);
  if (!fs.existsSync(record.path)) return true;
  if (hash(fs.readFileSync(record.path)) !== record.hash) {
    console.log(`kept changed service definition: ${record.path}`); return false;
  }
  const original = originalService(record), originalData = originalBytes(original);
  if (dry) { console.log(`[dry-run] would stop owned service ${record.label} at ${record.path}; ${original ? "restore original unit" : "remove owned unit"}; preserve ${record.state}`); return true; }
  const active = serviceState(record), before = fs.readFileSync(record.path);
  unloadService(record);
  try {
    if (originalData === null) fs.rmSync(record.path);
    else writeService(record.path, originalData, original.mode ?? 0o600);
    if (record.platform === "linux") serviceRun("systemctl", ["--user", "daemon-reload"]);
    if (original?.active) loadService(record);
  } catch (error) {
    writeService(record.path, before);
    if (active.active) loadService(record);
    throw error;
  }
  console.log(original ? `restored original service ${record.path}${original.active ? " and reloaded it" : " (previously inactive)"}` : `removed owned service ${record.path}`);
  return true;
}
