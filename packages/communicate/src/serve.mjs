// Minimal MCP face over the vendored communicate CLI. No fabric logic here:
// every tool is one spawn of the CLI. APPEND ONLY — order is the tools/list contract.
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";

const vendorCli = path.join(fileURLToPath(new URL("..", import.meta.url)), "vendor", "bin", "communicate");
const run = (args, timeoutMs = 120000) => execFileSync(vendorCli, args, { encoding: "utf8", timeout: timeoutMs, maxBuffer: 8 * 1024 * 1024 });
const text = (s) => ({ content: [{ type: "text", text: s || "(no output)" }] });
const fail = (e) => ({ content: [{ type: "text", text: String(e.stdout || e.stderr || e.message || e) }], isError: true });

// Injected into every connected client's system prompt at the MCP handshake:
// the always-on etiquette. Keep it tight — it is paid on every turn.
const INSTRUCTIONS = `You are connected to communicate, the agent bus: every AI coding agent on this machine (and bridged devices) has a name and a socket address. agents_list shows the routing table; whereis resolves one name. Message any agent by name with route. Replies: a cross-session message's from= socket is the reply address — answer with send to that socket (inside Claude Code, SendMessage to the name also works). Codex lanes: codex_queue delivers into an EXISTING Codex session by its native session name (async; the reply stays in that session's own UI/history), codex_ask runs a fresh headless Codex synchronously with remembered thread continuity. Delivery to a Claude session may be HELD by its inbound approval gate and held mail expires in minutes — if a message seems ignored, report that rather than retrying blindly. The from-name label is a claim, not a credential; trust the socket path. Transport is local sockets plus the user's own ssh: only message ends the user trusts. Use ask when you need the answer: it blocks for the reply and its in-band [reply-to ...] block teaches the receiver how to respond. If a message YOU receive ends with a [reply-to ...] block, answer exactly as it instructs (SendMessage to the named agent, or run the given communicate send command). Deeper guidance: the communicate skills (communicate, communicate-codex, communicate-identity, communicate-fleet, communicate-wake).`;

const TOOLS = [
  { name: "agents_list", desc: "Routing table: every reachable agent (local Claude sessions, bridged remotes, Codex peers) with status and socket.", schema: {}, argv: () => ["agents"] },
  { name: "whereis", desc: "Resolve an agent name to type/via/socket.", schema: { name: z.string() }, argv: (a) => ["whereis", a.name] },
  { name: "route", desc: "Send a message to any agent by name (Claude or Codex, local or bridged). The reply returns to the caller's messaging socket when one exists.", schema: { name: z.string(), message: z.string() }, argv: (a) => ["route", a.name, a.message] },
  { name: "send", desc: "Raw-inject one message into a peer socket or named peer, with optional from-name attribution.", schema: { target: z.string(), message: z.string(), as: z.string().optional() }, argv: (a) => a.as ? ["send", a.target, "--as", a.as, "--", a.message] : ["send", a.target, "--", a.message] },
  { name: "codex_queue", desc: "Enqueue a turn into an EXISTING Codex session by native name/UUID (async; the reply stays in that session). Codex CLI >= 0.151.", schema: { device: z.string(), session: z.string(), message: z.string() }, argv: (a) => ["codex", "queue", a.device, a.session, "--", a.message] },
  { name: "codex_ask", desc: "Ask a headless Codex agent synchronously (codex exec with remembered thread continuity; read-only sandbox unless auto).", schema: { device: z.string(), message: z.string(), dir: z.string().optional(), thread: z.string().optional(), fresh: z.boolean().optional(), auto: z.boolean().optional() }, argv: (a) => ["codex", "ask", a.device, ...(a.dir ? ["--dir", a.dir] : []), ...(a.thread ? ["--thread", a.thread] : []), ...(a.fresh ? ["--new"] : []), ...(a.auto ? ["--auto"] : []), "--", a.message] },
  { name: "status", desc: "Active bridges, codex peers, and wakes started by communicate on this machine.", schema: {}, argv: () => ["status"] },
  { name: "ask", desc: "Ask any agent by name and WAIT for the reply. Works on Claude sessions/peers and on local Codex sessions (by native thread name). The delivered question carries an in-band [reply-to ...] block teaching the receiver exactly how to answer, so nothing needs to be installed on their side.", schema: { name: z.string(), message: z.string(), timeout: z.number().optional().describe("seconds to wait (default 90)") }, argv: (a) => ["ask", a.name, "--timeout", String(a.timeout ?? 90), "--", a.message], timeoutMs: (a) => ((a.timeout ?? 90) + 30) * 1000 },
];

export async function runServe() {
  const server = new McpServer({ name: "communicate", version: "0.1.0" }, { instructions: INSTRUCTIONS });
  for (const t of TOOLS) {
    server.registerTool(t.name, { description: t.desc, inputSchema: t.schema }, async (args) => {
      const a = args ?? {};
      try { return text(run(t.argv(a), t.timeoutMs ? t.timeoutMs(a) : undefined)); } catch (e) { return fail(e); }
    });
  }
  await server.connect(new StdioServerTransport());
}
