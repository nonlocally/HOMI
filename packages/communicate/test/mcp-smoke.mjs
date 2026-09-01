#!/usr/bin/env node
// Drive `communicate serve` as a raw MCP stdio client: initialize, tools/list
// (exact 7 names in order), one agents_list call.
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";

const pkgDir = fileURLToPath(new URL("..", import.meta.url));
const child = spawn("node", [path.join(pkgDir, "src", "cli.mjs"), "serve"], { stdio: ["pipe", "pipe", "inherit"] });
let buf = ""; const pending = new Map();
child.stdout.on("data", (d) => {
  buf += d;
  let i; while ((i = buf.indexOf("\n")) >= 0) {
    const line = buf.slice(0, i); buf = buf.slice(i + 1);
    if (!line.trim()) continue;
    const msg = JSON.parse(line);
    if (msg.id !== undefined && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); }
  }
});
const rpc = (id, method, params) => new Promise((res, rej) => {
  pending.set(id, res);
  child.stdin.write(JSON.stringify({ jsonrpc: "2.0", id, method, params }) + "\n");
  setTimeout(() => rej(new Error(`timeout: ${method}`)), 30000);
});
const notify = (method, params) => child.stdin.write(JSON.stringify({ jsonrpc: "2.0", method, params }) + "\n");

const EXPECT = ["agents_list", "whereis", "route", "send", "codex_queue", "codex_ask", "status", "ask"];
try {
  const init = await rpc(1, "initialize", { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "smoke", version: "0" } });
  if (init.result?.serverInfo?.name !== "communicate") throw new Error("bad serverInfo: " + JSON.stringify(init.result?.serverInfo));
  if (!/agent bus/.test(init.result?.instructions ?? "")) throw new Error("instructions missing from initialize result");
  notify("notifications/initialized", {});
  const list = await rpc(2, "tools/list", {});
  const names = list.result.tools.map((t) => t.name);
  if (JSON.stringify(names) !== JSON.stringify(EXPECT)) throw new Error(`tool list mismatch: ${names}`);
  const call = await rpc(3, "tools/call", { name: "agents_list", arguments: {} });
  const out = call.result.content?.[0]?.text ?? "";
  if (!/NAME|no reachable agents/.test(out)) throw new Error("agents_list output unexpected: " + out.slice(0, 120));
  console.log("PASS: mcp-smoke — initialize (with instructions), 8 tools in order, agents_list returns the table");
  child.kill(); process.exit(0);
} catch (e) { console.error("FAIL: " + e.message); child.kill(); process.exit(1); }
