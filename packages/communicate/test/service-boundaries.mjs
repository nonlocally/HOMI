#!/usr/bin/env node
// Actual lifecycle functions, fake global manager registry, local socket fixture.
// No host service manager, provider, daemon or current HOME configuration is used.
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import net from "node:net";
import { once } from "node:events";
import { serviceDefinition, installService, uninstallService, assertManagedPath, withInstallLock, writeJson, daemonRequest, hash } from "../src/lifecycle.mjs";

// Exercise the actual Linux command branch on macOS without a Linux manager.
if (process.argv.includes("--linux-fixture")) Object.defineProperty(process, "platform", { value: "linux" });
const priorEnv = { ...process.env };
const temp = fs.mkdtempSync("/tmp/homi-svc-");
const home = path.join(temp, "home"), data = path.join(home, "data"), state = path.join(home, "state");
const bin = path.join(home, "bin"), registry = path.join(temp, "global-manager.json"), calls = path.join(temp, "calls.jsonl");
let server;
try {
  for (const key of ["COMMUNICATE_DATA", "COMM_STATE", "XDG_STATE_HOME", "XDG_CONFIG_HOME"] ) delete process.env[key];
  process.env.HOME = os.userInfo().homedir;
  const standardMac = serviceDefinition("darwin", "/usr/bin/python3", "fixture");
  const standardLinux = serviceDefinition("linux", "/usr/bin/python3", "fixture");
  assert.equal(standardMac.label, "com.communicate.homi");
  assert.equal(standardLinux.label, "communicate-homi.service");
  Object.assign(process.env, { HOME: home, COMMUNICATE_DATA: data, COMM_STATE: state,
    XDG_CONFIG_HOME: path.join(home, ".config"), PATH: bin + path.delimiter + priorEnv.PATH,
    HOMI_SELF: "fixture", HOMI_SOCK: path.join(temp, "never-contact-this.sock"),
    CODEX_HOME: path.join(temp, "ambient-private-account"), SERVICE_REGISTRY: registry, SERVICE_CALLS: calls });
  fs.mkdirSync(bin, { recursive: true });
  fs.mkdirSync(path.join(state, "homi"), { recursive: true });
  const definition = serviceDefinition(process.platform, "/usr/bin/python3", "fixture");
  const standard = process.platform === "darwin" ? standardMac : standardLinux;
  assert.notEqual(definition.label, standard.label, "isolated HOME collided with production label");
  const sameScope = serviceDefinition(process.platform, "/different/python3", "fixture");
  assert.equal(definition.label, sameScope.label, "interpreter change changed service identity");
  process.env.COMM_STATE = path.join(home, "other-state");
  assert.notEqual(serviceDefinition(process.platform, "/usr/bin/python3", "fixture").label, definition.label);
  process.env.COMM_STATE = state;
  const production = { active: true, enabled: true, path: standard.path, source: "/production/daemon.py" };
  const original = "# original operator service\n";
  fs.mkdirSync(path.dirname(definition.path), { recursive: true });
  fs.writeFileSync(definition.path, original, { mode: 0o640 });
  const readRegistry = () => JSON.parse(fs.readFileSync(registry));
  const writeRegistry = (value) => fs.writeFileSync(registry, JSON.stringify(value));
  writeRegistry({ [standard.label]: production, [definition.label]: { active: true, enabled: false, path: definition.path, source: "/legacy/daemon.py" } });
  const manager = `#!/usr/bin/env node
const fs=require('node:fs'),path=require('node:path'),a=process.argv.slice(2),file=process.env.SERVICE_REGISTRY;
const jobs=JSON.parse(fs.readFileSync(file));
fs.appendFileSync(process.env.SERVICE_CALLS,JSON.stringify(a)+'\\n');
let label, op;
if(a[0]==='print'||a[0]==='bootout'){op=a[0];label=a[1].split('/').at(-1);}
else if(a[0]==='bootstrap'){op='load';label=path.basename(a[2],'.plist');}
else if(a.includes('show')){op='print';label=a[a.indexOf('show')+1];}
else if(a.includes('disable')){op='bootout';label=a.at(-1);}
else if(a.includes('enable')){op='enable';label=a.at(-1);}
else if(a.includes('start')){op='load';label=a.at(-1);}
else if(a.includes('daemon-reload'))process.exit(0);
if(op==='print'){
 const job=jobs[label];
 if(!job||(a[0]==='print'&&!job.active)){console.error('Could not find service');process.exit(113);}
 console.log(a[0]==='print'?'path = '+job.path:'FragmentPath='+job.path+'\\nActiveState='+(job.active?'active':'inactive')+'\\nUnitFileState='+(job.enabled?(job.runtime?'enabled-runtime':'enabled'):'disabled'));process.exit(0);
}
if(op==='bootout'){if(jobs[label]){jobs[label].active=false;if(a[0]!=='bootout')jobs[label].enabled=false;}}
if(op==='enable'){
 jobs[label]??={active:false,path:path.join(process.env.XDG_CONFIG_HOME,'systemd/user',label)};
 jobs[label].enabled=true;jobs[label].runtime=a.includes('--runtime');
}
if(op==='load'){
 const unit=a[0]==='bootstrap'?a[2]:path.join(process.env.XDG_CONFIG_HOME,'systemd/user',label);
 const text=fs.readFileSync(unit,'utf8');
 if(process.env.FAIL_ORIGINAL==='1'&&text.startsWith('# original'))process.exit(9);
 if(process.env.FAIL_CURRENT==='1'&&!text.startsWith('# original'))process.exit(9);
 jobs[label]={...jobs[label],active:true,path:unit,source:text.startsWith('# original')?'/legacy/daemon.py':fs.realpathSync(path.join(process.env.COMMUNICATE_DATA,'current/vendor/lib/homi.py'))};
}
fs.writeFileSync(file,JSON.stringify(jobs));
`;
  for (const name of ["launchctl", "systemctl"]) fs.writeFileSync(path.join(bin, name), manager, { mode: 0o755 });
  const release = (name) => {
    const p = path.join(data, name);
    fs.mkdirSync(path.join(p, "vendor/lib"), { recursive: true });
    fs.writeFileSync(path.join(p, "vendor/lib/homi.py"), "# fixture source " + name);
    return p;
  };
  const first = release("one"), second = release("two");
  fs.symlinkSync(first, path.join(data, "current"));
  server = net.createServer((socket) => {
    let input = "";
    socket.on("data", (data) => { input += data; });
    socket.on("end", () => {
      const request = JSON.parse(input), jobs = readRegistry(), job = jobs[definition.label];
      if (!job?.active) { socket.end(); return; }
      if (request.op === "stop") { job.active = false; writeRegistry(jobs); socket.end('{"ok":true}\n'); return; }
      socket.end(JSON.stringify({ ok: true, self: { device: "fixture", source_file: job.source } }) + "\n");
    });
  });
  server.listen(path.join(state, "homi/homi.sock")); await once(server, "listening");

  // Global manager label collisions are rejected before bootout, even when the
  // on-disk file appears to belong to this installation.
  let jobs = readRegistry(); jobs[definition.label].path = path.join(temp, "other.plist"); writeRegistry(jobs);
  await assert.rejects(installService(), /another\/unknown unit/);
  assert(!fs.readFileSync(calls, "utf8").includes("bootout"));
  jobs[definition.label].path = definition.path; writeRegistry(jobs);

  let record = await installService();
  assert.equal(record.original.hash, hash(original));
  assert.equal(fs.readFileSync(record.original.backup, "utf8"), original);
  assert.equal(fs.statSync(record.original.backup).mode & 0o077, 0);
  assert(!record.environment.CODEX_HOME, "ambient account home leaked into service");
  assert(!record.environment.PATH.includes(bin), "invoking agent PATH leaked into service");
  const oldCalls = fs.readFileSync(calls, "utf8").split("\n").filter((s) => s.includes("bootout") || s.includes("bootstrap") || s.includes("disable") || s.includes("enable") || s.includes('"start"'));
  assert.equal(await installService(false, () => {}, record), record, "same release restarted its daemon");
  const newCalls = fs.readFileSync(calls, "utf8").split("\n").filter((s) => s.includes("bootout") || s.includes("bootstrap") || s.includes("disable") || s.includes("enable") || s.includes('"start"'));
  assert.deepEqual(newCalls, oldCalls);

  const firstOriginal = record.original;
  record = await installService(false, () => {}, record, { inherit: ["CODEX_HOME"] });
  assert.equal(record.environment.CODEX_HOME, process.env.CODEX_HOME);
  process.env.CODEX_HOME = path.join(temp, "another-account");
  assert.equal(await installService(false, () => {}, record), record, "repeat captured a different ambient account");
  fs.unlinkSync(path.join(data, "current")); fs.symlinkSync(second, path.join(data, "current"));
  record = await installService(false, () => {}, record);
  assert.deepEqual(record.original, firstOriginal, "update lost original ownership chain");
  assert.equal(readRegistry()[definition.label].source, fs.realpathSync(path.join(second, "vendor/lib/homi.py")));
  fs.appendFileSync(definition.path, "# user edit\n");
  await assert.rejects(installService(false, () => {}, record), /changed outside/);
  assert.equal(await uninstallService(record), false, "uninstall removed user-edited unit");
  fs.writeFileSync(definition.path, record.content);
  process.env.FAIL_ORIGINAL = "1";
  await assert.rejects(uninstallService(record), /failed/);
  assert.equal(fs.readFileSync(definition.path, "utf8"), record.content, "failed original reload lost active unit");
  delete process.env.FAIL_ORIGINAL;
  await uninstallService(record);
  assert.equal(fs.readFileSync(definition.path, "utf8"), original, "uninstall did not restore original bytes");
  assert.equal(fs.statSync(definition.path).mode & 0o777, 0o640, "uninstall lost original unit mode");
  assert.equal(readRegistry()[definition.label].source, "/legacy/daemon.py", "uninstall did not restore original loaded job");
  if (process.platform === "linux") {
    assert.equal(readRegistry()[definition.label].enabled, false, "manually started original gained autostart");
    for (const enabled of [true, false]) {
      const jobs = readRegistry(); jobs[definition.label].active = false; jobs[definition.label].enabled = enabled; writeRegistry(jobs);
      if (enabled) {
        process.env.FAIL_CURRENT = "1";
        await assert.rejects(installService(), /failed/);
        delete process.env.FAIL_CURRENT;
        assert.equal(readRegistry()[definition.label].active, false, "failed replacement started original idle unit");
        assert.equal(readRegistry()[definition.label].enabled, true, "failed replacement lost original autostart");
        assert.equal(fs.readFileSync(definition.path, "utf8"), original);
      }
      const inactiveOriginal = await installService();
      assert.equal(inactiveOriginal.original.active, false);
      assert.equal(inactiveOriginal.original.enabled, enabled);
      await uninstallService(inactiveOriginal);
      const restored = readRegistry()[definition.label];
      assert.equal(restored.active, false, "original inactive service unexpectedly started");
      assert.equal(restored.enabled, enabled, "original inactive service lost enablement state");
      assert.equal(fs.readFileSync(definition.path, "utf8"), original);
    }
    // A temporary autostart setting must not become persistent on restoration.
    const jobs = readRegistry(); jobs[definition.label].enabled = true; jobs[definition.label].runtime = true; writeRegistry(jobs);
    const runtimeOriginal = await installService();
    await uninstallService(runtimeOriginal);
    assert.equal(readRegistry()[definition.label].runtime, true);
    assert.equal(readRegistry()[definition.label].active, false);
  }
  assert.deepEqual(readRegistry()[standard.label], production, "isolated service changed production manager job");

  // Managed-path guards must stop aliases before writes; system /tmp aliases and
  // the intentional current release symlink are not mistaken for config files.
  const outside = path.join(temp, "outside"); fs.mkdirSync(outside);
  const alias = path.join(home, "alias"); fs.symlinkSync(outside, alias);
  assert.throws(() => assertManagedPath(path.join(alias, "new.json")), /symlink/);
  assert.throws(() => writeJson(path.join(alias, "new.json"), {}), /symlink/);
  const otherState = path.join(home, "symlink-state"); fs.mkdirSync(otherState);
  fs.symlinkSync(path.join(state, "homi"), path.join(otherState, "homi"));
  process.env.COMM_STATE = otherState;
  assert.throws(() => daemonRequest("stop", 100, true), /symlink/, "service control followed a state alias into another daemon");
  process.env.COMM_STATE = state;
  process.env.COMMUNICATE_DATA = alias;
  await assert.rejects(withInstallLock(() => {}), /symlink/);
  assert.deepEqual(fs.readdirSync(outside), []);
  console.log(`PASS (${process.platform} fixture): scoped service ownership, explicit environment, unchanged refresh, original lineage/activity/enablement, restoration failure, and symlink boundaries`);
} finally {
  if (server) await new Promise((resolve) => server.close(resolve));
  for (const key of Object.keys(process.env)) if (!(key in priorEnv)) delete process.env[key];
  Object.assign(process.env, priorEnv);
  fs.rmSync(temp, { recursive: true, force: true });
}
