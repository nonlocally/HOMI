#!/usr/bin/env node
// Exercise the MCP contract against isolated state and a fake Codex executable.
// No live sessions, real messages, registry server, or user settings are used.
import { spawn, spawnSync } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, existsSync, rmSync } from "node:fs";
import { fileURLToPath } from "node:url";
import os from "node:os";
import path from "node:path";
import { communicateCli, packageVersion } from "../src/paths.mjs";

const pkgDir = fileURLToPath(new URL("..", import.meta.url));
const taskHome = mkdtempSync(path.join(os.tmpdir(), "comm-mcp-"));
const env = { ...process.env, HOME: taskHome, COMM_STATE: path.join(taskHome, "state"),
  COMM_BUS_PORT: "0", COMMUNICATE_DATA: path.join(taskHome, "data"),
  PATH: path.join(taskHome, "bin") + path.delimiter + process.env.PATH };
for (const key of ["CODEX_HOME", "CODEX_THREAD_ID", "CODEX_SESSION_ID", "COMM_CODEX_INDEX",
  "CLAUDE_CONFIG_DIR", "CLAUDE_CODE_MESSAGING_SOCKET", "COMMUNICATE_HOME"]) delete env[key];
mkdirSync(path.join(taskHome, ".codex"), { recursive: true });
mkdirSync(path.join(taskHome, "bin"));
const senderThread = "11111111-1111-4111-8111-111111111111";
const recipientThread = "22222222-2222-4222-8222-222222222222";
writeFileSync(path.join(taskHome, ".codex", "session_index.jsonl"),
  [{ id: senderThread, thread_name: "sender" }, { id: recipientThread, thread_name: "recipient" }]
    .map((v) => JSON.stringify(v)).join("\n") + "\n");
writeFileSync(path.join(taskHome, "bin", "codex"), `#!/usr/bin/env python3
import json, os, sys
if sys.argv[1:] == ["queue", "--help"]:
    raise SystemExit(0)
if len(sys.argv) < 2 or sys.argv[1] != "queue":
    raise SystemExit("test refuses headless session creation")
with open(os.path.join(os.environ["HOME"], "queued.jsonl"), "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")
`, { mode: 0o755 });

// Override the entry point to test a packed artifact with the same protocol checks.
const entry = process.env.COMM_MCP_TEST_ENTRY || path.join(pkgDir, "src", "cli.mjs");
const child = spawn("node", [entry, "serve"], { env, stdio: ["pipe", "pipe", "pipe"] });
let buf = "", stderr = ""; const pending = new Map();
child.stderr.on("data", (d) => { stderr += d; });
child.stdout.on("data", (d) => {
  buf += d;
  let i; while ((i = buf.indexOf("\n")) >= 0) {
    const line = buf.slice(0, i); buf = buf.slice(i + 1);
    if (!line.trim()) continue;
    const msg = JSON.parse(line);
    if (msg.id !== undefined && pending.has(msg.id)) {
      const { resolve, timer } = pending.get(msg.id);
      clearTimeout(timer); pending.delete(msg.id); resolve(msg);
    }
  }
});
let nextId = 0;
const rpc = (method, params) => new Promise((resolve, reject) => {
  const id = ++nextId;
  const timer = setTimeout(() => { pending.delete(id); reject(new Error(`timeout: ${method}\n${stderr}`)); }, 30000);
  pending.set(id, { resolve, timer });
  child.stdin.write(JSON.stringify({ jsonrpc: "2.0", id, method, params }) + "\n");
});
const notify = (method, params) => child.stdin.write(JSON.stringify({ jsonrpc: "2.0", method, params }) + "\n");
const call = async (name, args = {}, errorExpected = false) => {
  const result = await rpc("tools/call", { name, arguments: args });
  if (result.error) throw new Error(`${name}: ${JSON.stringify(result.error)}`);
  const output = result.result.content?.[0]?.text ?? "";
  if (Boolean(result.result.isError) !== errorExpected) throw new Error(`${name}: ${output}`);
  return output;
};
const jsonCall = async (...args) => JSON.parse(await call(...args));
const assert = (ok, message) => { if (!ok) throw new Error(message); };
const EXPECT = ["agents_list", "whereis", "route", "send", "codex_queue", "codex_ask", "status", "ask", "card_set",
  "bus_register", "bus_list", "bus_agents", "bus_leave", "bus_send", "bus_receipt", "bus_status", "bus_dashboard", "bus_create"];
let failed = false;
try {
  const init = await rpc("initialize", { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "smoke", version: "0" } });
  assert(init.result?.serverInfo?.name === "communicate", "bad serverInfo");
  assert(init.result?.serverInfo?.version === packageVersion, "MCP server version must match package version");
  assert(/register yourself on the bus/.test(init.result?.instructions ?? ""), "registration instructions missing");
  notify("notifications/initialized", {});
  const list = await rpc("tools/list", {});
  assert(JSON.stringify(list.result.tools.map((t) => t.name)) === JSON.stringify(EXPECT), "tool list mismatch");
  assert(/NAME|no reachable agents/.test(await call("agents_list")), "legacy agents_list output");
  assert(/cannot identify this session/.test(await call("bus_register", {}, true)), "missing self must fail without creating an agent");
  await call("bus_create", { name: "photonics" });
  const sender = await jsonCall("bus_register", { bus: "photonics", kind: "codex", session: senderThread, description: "Sender fixture" });
  const recipient = await jsonCall("bus_register", { bus: "photonics", kind: "codex", session: recipientThread });
  assert(sender.id && recipient.id && sender.id !== recipient.id, "distinct current-session registrations");
  const roster = await jsonCall("bus_agents", { bus: "photonics" });
  assert(JSON.stringify(roster).includes(sender.id) && JSON.stringify(roster).includes(recipient.id), "registrations missing from selected bus");
  const general = await jsonCall("bus_agents", { bus: "general" });
  assert(!JSON.stringify(general).includes(sender.id), "private registration leaked into general");
  const message = "literal quotes ' \" $HOME `touch nope` $(touch nope)\nsecond line";
  const hub = (await jsonCall("bus_status")).hub;
  assert(/not connected/.test(await call("bus_send", {
    target: recipient.id, from: sender.id, bus: "photonics", message, hub: "https://unconnected.invalid",
  }, true)), "bus_send must forward the selected hub before the subcommand");
  assert(/not connected/.test(await call("bus_receipt", {
    id: "unused-receipt", hub: "https://unconnected.invalid",
  }, true)), "bus_receipt must forward the selected hub before the subcommand");
  assert(!existsSync(path.join(taskHome, "queued.jsonl")), "unknown-hub send must not reach the default broker");
  const sent = await jsonCall("bus_send", { target: recipient.id, from: sender.id, bus: "photonics", message, hub });
  const receiptId = sent.id || sent.message_id || sent.receipt?.id;
  assert(receiptId, "bus_send must return receipt ID");
  for (let i = 0; i < 40 && !existsSync(path.join(taskHome, "queued.jsonl")); i++) {
    await new Promise((r) => setTimeout(r, 250));
  }
  assert(existsSync(path.join(taskHome, "queued.jsonl")), "worker did not queue the bus message");
  const queued = readFileSync(path.join(taskHome, "queued.jsonl"), "utf8").trim().split("\n").map(JSON.parse);
  assert(queued.some((args) => args.some((arg) => arg.includes(recipientThread)) && args.some((arg) => arg.includes(message))), "MCP lost literal message or exact target thread");
  await call("bus_receipt", { id: receiptId, hub });
  assert((await jsonCall("bus_status")).hub === hub, "operation-specific hub changed the default connection");
  const dashboard = await call("bus_dashboard");
  assert(/^http:\/\/127\.0\.0\.1:\d+\/#token=\S+\s*$/.test(dashboard), "dashboard must return authenticated loopback URL");
  const page = await fetch(dashboard.split("#")[0]);
  assert(page.status === 200 && (await page.text()).includes("<html"), "dashboard asset missing");
  await call("bus_leave", { target: recipient.id, bus: "photonics" });
  const after = await jsonCall("bus_agents", { bus: "photonics" });
  assert(!JSON.stringify(after).includes(recipient.id), "leave did not remove membership");
  await call("bus_status");
  console.log(`PASS: mcp-smoke — ${EXPECT.length} tools, current-session registration, scope, literal send/queue, receipt, dashboard, leave`);
} catch (e) {
  failed = true; console.error("FAIL: " + e.message);
} finally {
  child.kill();
  for (const { timer } of pending.values()) clearTimeout(timer);
  spawnSync(communicateCli, ["bus", "stop"], { env, encoding: "utf8", timeout: 15000 });
  rmSync(taskHome, { recursive: true, force: true });
}
process.exit(failed ? 1 : 0);
