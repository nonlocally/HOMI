import assert from "node:assert/strict";
import { modelOrigin, addModelConnection, prepareModelChoice } from "../src/model-setup.mjs";
import { runOnboarding, shouldGuide, parseOnboardingArgs, planOnboarding } from "../src/onboarding.mjs";

const key = 'fixture-key-$HOME-`inert`';
assert.deepEqual(modelOrigin("https://models.example.test/v1/"), {
  base: "https://models.example.test/v1", origin: "https://models.example.test",
});
for (const value of ["http://models.example.test/v1", "https://user:secret@models.example.test/v1",
  "https://models.example.test/v1?token=secret", "https://models.example.test/v1#secret",
  "https://models.example.test/api/chat/completions"])
  assert.throws(() => modelOrigin(value));

let called = false;
addModelConnection(["glm", "--base-url", "https://models.example.test/v1", "--model", "glm"], key, {
  run: (command, args, options) => {
    called = true;
    assert(!JSON.stringify({ command, args }).includes(key));
    assert.equal(options.input, key + "\n");
    assert.deepEqual(options.stdio, ["pipe", "pipe", "pipe"]);
    assert(!options.shell);
    assert(args.includes("--key-stdin"));
    return { status: 0, stdout: '{"ok":true}' };
  },
});
assert(called);
assert.throws(() => addModelConnection(["glm"], key, {
  run: () => ({ status: 1, stdout: key, stderr: key, error: Error(key) }),
}), (error) => !error.message.includes(key));
for (const bad of ["", "line\nline", "nul\0byte", "x".repeat(8193)])
  assert.throws(() => addModelConnection(["glm"], bad, { run: () => { throw Error("must not launch"); } }));

const inputs = ["glm", "https://models.example.test/v1", "glm"];
let applied = 0;
const choice = await prepareModelChoice({
  ask: async () => inputs.shift(), secret: async () => key,
  add: async (args, input) => {
    applied++;
    assert.equal(input, key);
    assert.deepEqual(args, ["glm", "--base-url", "https://models.example.test/v1", "--model", "glm", "--anthropic-base-url", "https://models.example.test"]);
  },
});
assert(!JSON.stringify(choice).includes(key));
assert.equal(applied, 0);
await choice.apply();
assert.equal(applied, 1);
assert.equal(await prepareModelChoice({ ask: async () => "", secret: async () => { throw Error("must not ask for key"); } }), null);
for (const name of ["research.glm", "x".repeat(49)])
  await assert.rejects(prepareModelChoice({ ask: async () => name, secret: async () => { throw Error("must not ask for key"); } }), /48 lowercase/);
for (const invalidKey of ["key with spaces", "clé"])
  await assert.rejects(prepareModelChoice({ ask: async (prompt) => prompt.startsWith("Canonical") ? "https://models.example.test/v1" : "glm", secret: async () => invalidKey }), /ASCII token/);

const tools = Object.fromEntries(Object.entries({ node: "22.0.0", python3: "3.12.0", bash: "5.2.0", claude: "2.1.281", tmux: "3.5" })
  .map(([id, version]) => [id, { path: `/fixture/bin/${id}`, version }]));
const logs = [], calls = [];
const oldCodexPlan = planOnboarding(parseOnboardingArgs(["--model", "--codex", "--install-missing"]), {
  platform: "linux", uid: 501, tools: { ...tools, codex: { path: "/fixture/bin/codex", version: "0.151.0" } },
});
assert(oldCodexPlan.blocked.some((reason) => reason.includes("0.156")), "model setup must report older Codex before activation");
const fixture = { stdinTTY: true, stdoutTTY: true,
  probe: async () => ({ platform: "linux", tools, uid: 501 }),
  log: (line) => logs.push(line), confirm: async () => true,
  ask: async () => { throw Error("injected prepareModel owns prompt"); },
  prepareModel: async () => ({ description: "glm at fixture origin", apply: async () => calls.push("model") }),
  setup: async () => calls.push("setup"), doctor: async () => calls.push("doctor"),
};
assert(shouldGuide(["--model"]));
const result = await runOnboarding(["--model", "--claude", "--no-service"], fixture);
assert.equal(result.status, "complete");
assert.deepEqual(calls, ["setup", "model", "doctor"]);
assert(!JSON.stringify({ result, logs }).includes(key));
calls.length = 0;
assert.equal((await runOnboarding(["--model", "--claude", "--dry-run"], {
  ...fixture, prepareModel: async () => { throw Error("dry-run must not prompt"); },
})).status, "dry-run");
assert.deepEqual(calls, []);
await assert.rejects(runOnboarding(["--model", "--claude", "--yes"], { ...fixture, stdinTTY: false }), /terminal/);
assert.deepEqual(calls, []);
assert.equal((await runOnboarding(["--model", "--claude"], { ...fixture, prepareModel: async () => null })).status, "cancelled");
assert.deepEqual(calls, []);
await assert.rejects(runOnboarding(["--model", "--claude"], { ...fixture,
  prepareModel: async () => ({ description: "fixture", apply: async () => { throw Error(key); } }),
}), (error) => error.message.includes("core setup completed") && !error.message.includes(key));
assert.deepEqual(calls, ["setup"]);
console.log("PASS: private model setup, literal stdin, redacted failures, cancellation, dry-run and explicit activation");
