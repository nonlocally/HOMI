// server.ts — the MCP face. Each tool is a 1:1 projection onto a daemon op via
// kernel.call(); ZERO fabric logic lives here ("two faces, one kernel"). Tools
// are registered from ONE fixed, append-only array so tools/list is byte-stable
// across restarts (the 2026-07-28 cacheable-list rule; backward-benign today).
// Descriptions STEER: messaging is the default plane; seats are the explicit
// interactive escape hatch for surfaces you cannot mailbox.
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import { call } from "./kernel.js";

const LONG = 10_000; // extra socket slack for long-poll ops

function text(obj: unknown) {
  return { content: [{ type: "text" as const, text: JSON.stringify(obj, null, 2) }] };
}

// The fixed tool array. ORDER IS STABLE — append only.
type Tool = {
  name: string;
  description: string;
  schema: z.ZodRawShape;
  run: (a: any) => Promise<any>;
};

const TOOLS: Tool[] = [
  {
    name: "agents_list",
    description:
      "The roster: every agent with measured liveness (probed/reported). Start here. Prefer send/ask — they work even when the target is not running.",
    schema: {},
    run: () => call({ op: "agents" }),
  },
  {
    name: "whoami",
    description: "This device and the identities claimed on it.",
    schema: {},
    run: async () => {
      const st = await call({ op: "status" });
      return { device: st?.self?.device, identities: Object.keys(st?.identities || {}) };
    },
  },
  {
    name: "send",
    description:
      "Deliver a message to an agent by name, or name@device across a link. Durable store-and-forward: the target need not be alive. The DEFAULT way to talk to any agent.",
    schema: {
      to: z.string().describe("agent name, or name@device"),
      text: z.string(),
      from: z.string().optional().describe("your identity (attribution + reply address)"),
    },
    run: (a) => call({ op: "send", to: a.to, text: a.text, from: a.from || "mcp" }),
  },
  {
    name: "ask",
    description:
      "Send a question and block until the correlated reply arrives (or timeout). Use send for fire-and-forget.",
    schema: {
      to: z.string(),
      text: z.string(),
      from: z.string().optional(),
      timeout_s: z.number().optional().default(60),
    },
    run: (a) =>
      call(
        { op: "ask", to: a.to, text: a.text, from: a.from || "asker", timeout: a.timeout_s ?? 60 },
        { timeoutMs: (a.timeout_s ?? 60) * 1000 + LONG },
      ),
  },
  {
    name: "inbox_read",
    description: "Read an agent's mailbox (durable JSONL). Non-destructive.",
    schema: {
      name: z.string(),
      tail: z.number().optional().default(50),
      after_msg_id: z.string().optional(),
    },
    run: (a) => call({ op: "inbox", name: a.name, tail: a.tail ?? 50, after_msg_id: a.after_msg_id }),
  },
  {
    name: "wait_for_message",
    description:
      "Block until a NEW message lands for name, or timeout. Your inbound wake when your runtime has no socket push — loop on this. (Claude sessions get native socket wake and rarely need it.)",
    schema: {
      name: z.string(),
      timeout_s: z.number().optional().default(60),
      after_msg_id: z.string().optional(),
    },
    run: (a) =>
      call(
        { op: "wait", name: a.name, timeout: a.timeout_s ?? 60, after_msg_id: a.after_msg_id },
        { timeoutMs: (a.timeout_s ?? 60) * 1000 + LONG },
      ),
  },
  {
    name: "claim",
    description:
      "Claim a durable identity on this device: a stable socket, a sweep-proof roster entry, and a mailbox that survives the process behind it.",
    schema: { name: z.string() },
    run: (a) => call({ op: "claim", name: a.name }),
  },
  {
    name: "release",
    description: "Release a claimed identity.",
    schema: { name: z.string() },
    run: (a) => call({ op: "release", name: a.name }),
  },
  {
    name: "group_send",
    description: "Send one message to several agents at once.",
    schema: { names: z.array(z.string()), text: z.string(), from: z.string().optional() },
    run: (a) => call({ op: "group", names: a.names, text: a.text, from: a.from || "mcp" }),
  },
  {
    name: "notify",
    description:
      "Summon the human with a durable reason. Only when you are blocked on a decision only they can make.",
    schema: { reason: z.string(), from: z.string().optional() },
    run: (a) => call({ op: "notify", reason: a.reason, from: a.from || "mcp" }),
  },
  // --- seats: the interactive escape hatch (cluster shells, REPLs, TUIs) ---
  {
    name: "seat_ls",
    description:
      "List interactive seats (terminal surfaces). Seats drive surfaces you cannot mailbox — cluster shells, REPLs, TUIs — NOT agent↔agent talk; use send/ask for that.",
    schema: {},
    run: () => call({ op: "seat", sub: "ls" }),
  },
  {
    name: "seat_spawn",
    description: "Create a seat running a command (optionally on a linked device with --device).",
    schema: { cmd: z.string(), cwd: z.string().optional(), device: z.string().optional() },
    run: (a) => call({ op: "seat", sub: "spawn", cmd: a.cmd, cwd: a.cwd, device: a.device }, { timeoutMs: 40_000 }),
  },
  {
    name: "seat_send",
    description: "Type into a seat (sanitized; separate verified Enter). seat may be device:pane.",
    schema: { seat: z.string(), text: z.string() },
    run: (a) => call({ op: "seat", sub: "send", seat: a.seat, text: a.text }, { timeoutMs: 30_000 }),
  },
  {
    name: "seat_read",
    description: "Read a seat's screen (secrets redacted unless raw=true).",
    schema: { seat: z.string(), lines: z.number().optional().default(40), raw: z.boolean().optional() },
    run: (a) => call({ op: "seat", sub: "read", seat: a.seat, lines: a.lines ?? 40, raw: !!a.raw }, { timeoutMs: 30_000 }),
  },
  {
    name: "seat_state",
    description: "One word: working/idle/booting/approval/dead. The non-blocking done-check — never poll seat_read for this.",
    schema: { seat: z.string() },
    run: (a) => call({ op: "seat", sub: "state", seat: a.seat }, { timeoutMs: 30_000 }),
  },
  {
    name: "seat_wait",
    description: "Block until a seat settles to idle (or timeout); returns final state + tail.",
    schema: { seat: z.string(), timeout_s: z.number().optional().default(120) },
    run: (a) =>
      call({ op: "seat", sub: "wait", seat: a.seat, timeout: a.timeout_s ?? 120 }, { timeoutMs: (a.timeout_s ?? 120) * 1000 + LONG }),
  },
  {
    name: "spawn",
    description:
      "Create an agent: claim a durable identity, launch the CLI in a seat, bind them, and (for claude) adopt via rename-sync. The agent is then reachable by mail AND watchable in a seat.",
    schema: {
      name: z.string(),
      cli: z.enum(["claude", "codex"]).optional(),
      cmd: z.string().optional().describe("raw command, if not using cli"),
      cwd: z.string().optional(),
    },
    run: (a) => {
      const cmd = a.cmd || (a.cli === "codex" ? (process.env.HOMI_CODEX_CMD || "codex") : (process.env.HOMI_CLAUDE_CMD || "claude"));
      const adopt = a.cli !== "codex" && !a.cmd; // claude default adopts
      return call({ op: "spawn", name: a.name, cmd, cwd: a.cwd, adopt }, { timeoutMs: 60_000 });
    },
  },
  {
    name: "status",
    description: "Full fabric status: sockets, identities, links, queues — measured, provenance-labelled.",
    schema: {},
    run: () => call({ op: "status" }),
  },
];

export function buildServer(): McpServer {
  const server = new McpServer({ name: "homi", version: "0.1.0" });
  for (const t of TOOLS) {
    server.registerTool(
      t.name,
      { description: t.description, inputSchema: t.schema },
      async (args: any) => {
        try {
          return text(await t.run(args || {}));
        } catch (e: any) {
          return { content: [{ type: "text" as const, text: JSON.stringify({ ok: false, err: String(e?.message || e) }) }], isError: true };
        }
      },
    );
  }
  return server;
}
