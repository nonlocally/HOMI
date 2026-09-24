#!/usr/bin/env node
// Actual MCP -> existing CLI -> isolated durable daemon. No real agent/model.
import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, mkdirSync, rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const entry = process.env.HOMI_MCP_TEST_ENTRY || path.join(root, "packages/communicate/src/cli.mjs");
const cli = process.env.HOMI_TEST_CLI || path.join(root, "bin/communicate");
const temp = mkdtempSync(path.join(os.tmpdir(), "homi-mcp-core-"));
const env = { ...process.env, HOME: temp, COMM_STATE: path.join(temp, "state"),
  COMMUNICATE_DATA: path.join(temp, "data"), HOMI_SOCK_DIR: path.join(temp, "sockets"),
  HOMI_SESSIONS_DIR: path.join(temp, "sessions"), HOMI_SELF: "fixture", HOMI_TICK: "1" };
for (const key of ["CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CONFIG_DIR", "CODEX_HOME",
  "CODEX_THREAD_ID", "CODEX_SESSION_ID", "COMMUNICATE_HOME", "HOMI_SOCK"]) delete env[key];
mkdirSync(env.HOMI_SESSIONS_DIR);
const child = spawn(process.execPath, [entry, "serve"], { env, stdio: ["pipe", "pipe", "pipe"] });
let buffer = "", stderr = "", next = 0;
const pending = new Map();
child.stderr.on("data", (part) => { stderr += part; });
child.stdout.on("data", (part) => {
  buffer += part;
  let boundary;
  while ((boundary = buffer.indexOf("\n")) !== -1) {
    const line = buffer.slice(0, boundary); buffer = buffer.slice(boundary + 1);
    if (!line.trim()) continue;
    const message = JSON.parse(line), callback = pending.get(message.id);
    if (callback) { clearTimeout(callback.timer); pending.delete(message.id); callback.resolve(message); }
  }
});
const rpc = (method, params) => new Promise((resolve, reject) => {
  const id = ++next;
  const timer = setTimeout(() => { pending.delete(id); reject(Error(`${method} timed out: ${stderr}`)); }, 20000);
  pending.set(id, { resolve, timer });
  child.stdin.write(JSON.stringify({ jsonrpc: "2.0", id, method, params }) + "\n");
});
async function call(name, args = {}) {
  const response = await rpc("tools/call", { name, arguments: args });
  assert(!response.error, JSON.stringify(response.error));
  const output = response.result.content?.[0]?.text || "";
  assert(!response.result.isError, `${name}: ${output}`);
  return output;
}

try {
  const init = await rpc("initialize", { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "core-fixture", version: "1" } });
  assert.equal(init.result.serverInfo.name, "communicate");
  child.stdin.write(JSON.stringify({ jsonrpc: "2.0", method: "notifications/initialized" }) + "\n");
  const names = (await rpc("tools/list", {})).result.tools.map((tool) => tool.name);
  for (const name of ["bus_dashboard", "bus_reply", "route", "homi_claim", "homi_seat_bind"]) assert(names.includes(name));
  for (const args of [{}, { hub: "https://bus.nonlocally.org" }]) {
    const status = JSON.parse(await call("bus_status", args));
    assert.equal(status.configured, false, "fresh status must not invent enrollment");
    assert.equal(status.hub, null, "fresh status must not select a fallback hub");
  }
  for (const file of ["server.json", "worker.json"]) {
    assert(!existsSync(path.join(env.COMM_STATE, "bus", file)), "status inspection must not start bus services");
  }
  await call("homi_start");
  await call("homi_claim", { name: "alice" });
  await call("homi_claim", { name: "bob" });
  await call("homi_send", { target: "bob", from: "alice", message: "--from" });
  const inbox = (await call("homi_inbox", { name: "bob" })).trim().split("\n").map(JSON.parse);
  assert(inbox.some((item) => item.text === "--from"), "option-shaped message must stay literal");
  const ask = call("homi_ask", { target: "bob", from: "alice", message: "--timeout", timeout: 8 });
  let token;
  for (let i = 0; i < 40 && !token; i++) {
    const mail = await call("homi_inbox", { name: "bob" });
    token = mail.match(/homi reply ([^\s\\]+)/)?.[1];
    if (!token) await new Promise((resolve) => setTimeout(resolve, 100));
  }
  assert(token, "pending ask must not block other MCP calls");
  await call("homi_reply", { token, from: "bob", message: "--from" });
  const reply = JSON.parse(await ask);
  assert.equal(reply.reply, "--from");
  assert(reply.corr, "explicit reply correlation retained");
  assert(JSON.parse(await call("homi_agents")).agents.some((agent) => agent.name === "bob"));
  console.log("PASS: combined MCP namespaces, literal messages, durable inbox and concurrent correlated reply");
} finally {
  child.stdin.end();
  child.kill();
  for (const { timer } of pending.values()) clearTimeout(timer);
  spawnSync(cli, ["homi", "stop"], { env, encoding: "utf8", timeout: 15000 });
  rmSync(temp, { recursive: true, force: true });
}
