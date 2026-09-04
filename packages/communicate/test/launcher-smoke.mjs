#!/usr/bin/env node
// A checkout (including paths with spaces) must win over an older npm install.
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync, symlinkSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";
import os from "node:os";
const pkg = fileURLToPath(new URL("..", import.meta.url));
const root = path.resolve(pkg, "../..");
const temp = mkdtempSync(path.join(os.tmpdir(), "comm-launcher-"));
const repo = path.join(temp, "checkout with spaces");
symlinkSync(root, repo);
const data = path.join(temp, "data");
mkdirSync(path.join(data, "current", "vendor", "bin"), { recursive: true });
mkdirSync(path.join(temp, "bin"));
writeFileSync(path.join(data, "repo-path"), repo + "\n", { mode: 0o600 });
writeFileSync(path.join(data, "current", "vendor", "bin", "communicate"), "#!/bin/sh\necho stale-install\n", { mode: 0o755 });
writeFileSync(path.join(temp, "bin", "node"), "#!/bin/sh\nprintf '%s\\n' \"$@\"\n", { mode: 0o755 });
writeFileSync(path.join(temp, "bin", "npx"), "#!/bin/sh\necho registry-fallback >&2\nexit 99\n", { mode: 0o755 });
const env = { ...process.env, HOME: temp, COMMUNICATE_DATA: data, COMM_STATE: path.join(temp, "state"),
  PATH: path.join(temp, "bin") + path.delimiter + process.env.PATH };
for (const key of ["CLAUDE_PLUGIN_ROOT", "PLUGIN_ROOT", "COMMUNICATE_HOME"]) delete env[key];
try {
  const mcp = JSON.parse(readFileSync(path.join(root, "plugins/communicate/.mcp.json"), "utf8")).mcpServers.communicate;
  const r = spawnSync(mcp.command, mcp.args, { env, encoding: "utf8" });
  if (r.status !== 0 || !r.stdout.includes("checkout with spaces/packages/communicate/src/cli.mjs\nserve"))
    throw new Error("checkout MCP not selected: " + r.stdout + r.stderr);
  const cli = spawnSync(path.join(root, "plugins/communicate/bin/communicate"), ["--help"], { env, encoding: "utf8" });
  if (cli.status !== 0 || cli.stdout.includes("stale-install") || !cli.stdout.includes("communicate bus"))
    throw new Error("neighboring checkout CLI not selected: " + cli.stdout + cli.stderr);
  console.log("PASS: launcher-smoke — checkout MCP and CLI win over stale installs; spaced paths remain literal");
} finally { rmSync(temp, { recursive: true, force: true }); }
