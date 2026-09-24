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
export function writeJson(p, value) {
  fs.mkdirSync(path.dirname(p), { recursive: true, mode: 0o700 });
  const tmp = `${p}.tmp-${process.pid}`;
  fs.writeFileSync(tmp, JSON.stringify(value, null, 2) + "\n", { mode: 0o600 });
  fs.renameSync(tmp, p);
}
export function linkTarget(link = currentLink()) {
  try { return path.resolve(path.dirname(link), fs.readlinkSync(link)); }
  catch (error) { if (error.code === "ENOENT") return null; throw new Error(`Refusing non-symlink installation pointer: ${link}`); }
}
export function switchCurrent(target) {
  const tmp = currentLink() + `.tmp-${process.pid}`;
  fs.rmSync(tmp, { force: true });
  if (target === null) { fs.rmSync(currentLink(), { force: true }); return; }
  fs.symlinkSync(target, tmp);
  fs.renameSync(tmp, currentLink());
}
export async function withInstallLock(action) {
  fs.mkdirSync(dataRoot(), { recursive: true, mode: 0o700 });
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
export function serviceDefinition(platform, py, device) {
  const daemon = path.join(currentLink(), "vendor/lib/homi.py");
  const vars = { HOME: home(), COMM_STATE: stateRoot(), HOMI_SELF: device,
    PATH: process.env.PATH || "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin" };
  for (const name of ["HOMI_SOCK_DIR", "HOMI_SESSIONS_DIR", "CLAUDE_CONFIG_DIR", "CODEX_HOME", "HOMI_TMUX_SOCKET"])
    if (process.env[name] !== undefined) vars[name] = process.env[name];
  if (platform === "darwin") return { platform, label: "com.communicate.homi",
    path: path.join(home(), "Library/LaunchAgents/com.communicate.homi.plist"),
    content: `<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n<plist version="1.0"><dict>
<key>Label</key><string>com.communicate.homi</string>
<key>ProgramArguments</key><array><string>${xml(py)}</string><string>${xml(daemon)}</string><string>daemon</string></array>
<key>EnvironmentVariables</key><dict>${Object.entries(vars).map(([k,v]) => `<key>${xml(k)}</key><string>${xml(v)}</string>`).join("")}</dict>
<key>RunAtLoad</key><true/><key>KeepAlive</key><true/><key>ProcessType</key><string>Interactive</string>
<key>StandardOutPath</key><string>${xml(path.join(stateRoot(), "homi/launchd.log"))}</string>
<key>StandardErrorPath</key><string>${xml(path.join(stateRoot(), "homi/launchd.log"))}</string>
</dict></plist>\n` };
  if (platform === "linux") return { platform, label: "communicate-homi.service",
    path: path.join(process.env.XDG_CONFIG_HOME || path.join(home(), ".config"), "systemd/user/communicate-homi.service"),
    content: `[Unit]\nDescription=HOMI durable agent identities\n[Service]\nExecStart=${unit(py)} ${unit(daemon)} daemon\n${Object.entries(vars).map(([k,v]) => `Environment=${unit(k + "=" + v)}`).join("\n")}\nRestart=always\nRestartSec=2\n[Install]\nWantedBy=default.target\n` };
  throw new Error("Persistent services are supported on macOS and Linux only");
}
function serviceRun(command, args, allowFailure = false) {
  const result = spawnSync(command, args, { encoding: "utf8", timeout: 30000 });
  if (!allowFailure && (result.error || result.status !== 0))
    throw new Error(`${command} ${args.join(" ")} failed: ${(result.stderr || result.error?.message || "").trim()}`);
}
export function unloadService(definition) {
  if (definition.platform === "darwin") serviceRun("launchctl", ["bootout", `gui/${process.getuid()}/${definition.label}`], true);
  else serviceRun("systemctl", ["--user", "disable", "--now", definition.label], true);
}
export function loadService(definition) {
  if (definition.platform === "darwin") serviceRun("launchctl", ["bootstrap", `gui/${process.getuid()}`, definition.path]);
  else {
    serviceRun("systemctl", ["--user", "daemon-reload"]);
    serviceRun("systemctl", ["--user", "enable", "--now", definition.label]);
  }
}
export function daemonRequest(op = "status", timeout = 3000) {
  // A diagnosis never autostarts services. Preserve control authentication.
  return new Promise((resolve) => {
    const root = path.join(stateRoot(), "homi");
    const request = { op };
    const token = path.join(root, "control.token");
    if (fs.existsSync(token)) request.auth = fs.readFileSync(token, "utf8").trim();
    const socket = net.createConnection(process.env.HOMI_SOCK || path.join(root, "homi.sock"));
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
export async function installService(dry = false, beforeRestore = () => {}) {
  const py = python();
  const incumbent = dry ? null : await daemonRequest();
  const device = process.env.HOMI_SELF || incumbent?.self?.device || os.hostname().split(".")[0].toLowerCase();
  const definition = serviceDefinition(process.platform, py, device);
  if (dry) { console.log(`[dry-run] would install ${definition.path} using ${py} and ${currentLink()}`); return null; }
  fs.mkdirSync(path.join(stateRoot(), "homi"), { recursive: true, mode: 0o700 });
  fs.mkdirSync(path.dirname(definition.path), { recursive: true });
  const before = fs.existsSync(definition.path) ? fs.readFileSync(definition.path, "utf8") : null;
  const backup = before === null ? null : `${definition.path}.homi-backup-${Date.now()}`;
  if (backup) fs.writeFileSync(backup, before, { mode: 0o600 });
  unloadService(definition);
  await daemonRequest("stop");
  // Stop replies precede the daemon's delayed shutdown; wait on the socket.
  for (let i = 0; i < 30 && await daemonRequest(); i++) await new Promise((r) => setTimeout(r, 100));
  try {
    fs.writeFileSync(definition.path, definition.content, { mode: 0o600 });
    loadService(definition);
    const expectedSource = fs.realpathSync(path.join(currentLink(), "vendor/lib/homi.py"));
    for (let i = 0; i < 50; i++) {
      const status = await daemonRequest();
      if (status?.ok && status.self?.source_file === expectedSource)
        return { ...definition, hash: hash(definition.content), backup, state: stateRoot() };
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
      fs.writeFileSync(definition.path, before, { mode: 0o600 });
      try { loadService(definition); }
      catch (restoreError) { throw new Error(`${error.message}; previous service also failed to restart: ${restoreError.message}`); }
    }
    throw error;
  }
}
export async function uninstallService(record, dry = false) {
  if (!record || !fs.existsSync(record.path)) return true;
  if (hash(fs.readFileSync(record.path)) !== record.hash) {
    console.log(`kept changed service definition: ${record.path}`); return false;
  }
  if (dry) { console.log(`[dry-run] would stop and remove owned service ${record.path}; preserve ${record.state}`); return true; }
  unloadService(record);
  fs.rmSync(record.path);
  if (record.platform === "linux") serviceRun("systemctl", ["--user", "daemon-reload"]);
  return true;
}
