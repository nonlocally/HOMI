#!/usr/bin/env python3
"""Offline cross-repository human-chat acceptance against real local services.

Python 3.11+, httpx, Pydantic 2, starlette, and Node with native TypeScript
support are required. The selected gateway checkout must contain its normal
sources and test-loader.mjs. All state is temporary. Only the OWUI provider
and fixed-origin-to-loopback network adapters are fixtures: the Pipe, gateway
handleBusProxy/authorization, broker HTTP server, queue, ack and reply are real.
No request can reach a production endpoint; all bearer credentials are fixtures.

Example:
  python scripts/test-bus-chat-stack.py \
    --gateway-source /path/to/communicate-site \
    --platform-source /path/to/openweb-marimo-platform
"""

import argparse
import asyncio
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from starlette.requests import Request


OWNER = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
TOKEN = "fixture-owner-session-token-0123456789"
OTHER_TOKEN = "fixture-other-session-token-0123456789"
SECRET = "fixture-existing-gateway-secret-0123456789"
NOW = 1800000000
BRIDGE = "https://bus.nonlocally.org/_bus/chat/bridge"

# This server calls the imported production handler. It exposes no test route
# on production, and does not modify source files in either dependency repo.
NODE_ADAPTER = r"""
import assert from 'node:assert/strict';
import http from 'node:http';
import { pathToFileURL } from 'node:url';
const config = JSON.parse(process.env.CHAT_STACK_FIXTURE);
const { handleBusProxy, BUS_PUBLIC_ORIGIN } = await import(pathToFileURL(config.gateway + '/bus-proxy.ts').href);
const fetchLocal = globalThis.fetch;
globalThis.fetch = async () => { throw new Error('Unconfigured outbound network is forbidden in this test'); };
const stats = { sessions: 0, groups: 0, origins: [], bridge: [] };
const tokenUsers = new Map([[config.token, config.owner], [config.otherToken, config.other]]);
const provider = async (input, init) => {
  const url = String(input), headers = new Headers(init?.headers);
  assert.equal(init?.redirect, 'error');
  assert.equal(init?.cache, 'no-store');
  if (url === 'https://mit.nonlocally.org/api/v1/auths/') {
    stats.sessions++;
    const user = tokenUsers.get((headers.get('authorization') ?? '').replace(/^Bearer /, ''));
    return user ? Response.json({id:user, role:'user'}) : Response.json({error:'fixture unauthorized'}, {status:401});
  }
  assert.equal(headers.get('authorization'), 'Bearer fixture-group-reader-admin-token');
  const user = url.split('/').at(-1);
  assert.ok([config.owner, config.other].includes(user));
  assert.equal(url, 'https://mit.nonlocally.org/api/v1/users/' + user);
  stats.groups++;
  return Response.json({id:user, role:'user', name:'Stack fixture', groups:[{id:config.group, name:'Fixture group'}]});
};
const originFetch = async (input, init) => {
  const url = new URL(String(input)), headers = new Headers(init?.headers);
  assert.equal(url.origin, 'https://bus-origin.stack.invalid');
  assert.equal(url.pathname, '/_bus/chat');
  assert.equal(headers.get('authorization'), null);
  assert.equal(headers.get('cookie'), null);
  assert.equal(headers.get('x-communicate-expected-user'), null);
  assert.equal(headers.get('x-communicate-bus-gateway'), config.secret);
  const payload = JSON.parse(await new Response(init?.body).text());
  const view = JSON.parse(headers.get('x-communicate-bus-view'));
  assert.deepEqual(view.buses, [config.bus]);
  assert.equal(view.reader, headers.get('x-communicate-bus-reader'));
  assert.equal(view.reader_hash, headers.get('x-communicate-bus-reader-hash'));
  stats.origins.push({op:payload.op, reader:view.reader});
  return fetchLocal(config.broker + url.pathname, init);
};
const cfg = {
  deployment:'stack-fixture.invalid', now:()=>config.now * 1000,
  sessionSecret:'fixture-independent-browser-session-secret',
  clientId:'fixture-github-client', clientSecret:'fixture-github-secret', allowedUsers:new Map(),
  origin:'https://bus-origin.stack.invalid', gatewaySharedSecret:config.secret, originFetch,
  openwebui:{url:'https://mit.nonlocally.org', adminToken:'fixture-group-reader-admin-token',
    ssoKey:'fixture-independent-sso-handoff-secret', fetch:provider},
};
const server = http.createServer(async (req, res) => {
  try {
    if (req.url === '/__fixture/stats') {
      res.writeHead(200, {'Content-Type':'application/json'}); res.end(JSON.stringify(stats)); return;
    }
    assert.ok(['/_bus/chat/bridge', '/_bus/chat'].includes(req.url));
    const bytes = []; for await (const chunk of req) bytes.push(chunk);
    const body = Buffer.concat(bytes);
    assert.ok(body.length <= 131072);
    const request = new Request(BUS_PUBLIC_ORIGIN + req.url, {
      method:req.method, headers:req.headers,
      ...(!['GET','HEAD'].includes(req.method) ? {body} : {}),
    });
    const response = await handleBusProxy(request, cfg);
    stats.bridge.push({path:req.url, status:response.status});
    res.writeHead(response.status, Object.fromEntries(response.headers));
    res.end(Buffer.from(await response.arrayBuffer()));
  } catch (error) {
    console.error('Stack adapter assertion failed:', error.message);
    res.writeHead(500);res.end('Stack adapter failed');
  }
});
server.listen(0, '127.0.0.1', () => console.log(JSON.stringify({port:server.address().port})));
process.on('SIGTERM', () => server.close(() => process.exit(0)));
"""


def module_at(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def require(condition, description):
    if not condition:
        raise AssertionError(description)


async def run(gateway, platform):
    node = shutil.which("node")
    require(node is not None, "Node is required")
    mapping = json.loads((gateway / "openwebui-groups.json").read_text())
    group, descriptor = next(iter(mapping.items()))
    bus = descriptor["bus"]
    broker_module = module_at("stack_bus_broker", Path(__file__).resolve().parents[1] / "lib/bus_broker.py")
    pipe_module = module_at("stack_bus_pipe", platform / "communicate_bus/openwebui_pipe.py")
    require(pipe_module.BRIDGE_URL == BRIDGE, "Pipe bridge must remain pinned to the production origin")
    fixture_env = {key: value for key, value in os.environ.items() if not key.startswith("BUS_")}
    fixture_env.update({"BUS_GATEWAY_SHARED_SECRET": SECRET, "BUS_OPENWEBUI_READERS": "1",
                        "BUS_CHAT_READERS": json.dumps({"owui." + OWNER: {"identity": "stack-owner", "buses": [bus]}})})
    with tempfile.TemporaryDirectory(prefix="bus-chat-stack-") as directory, patch.dict(os.environ, fixture_env, clear=True):
        broker = broker_module.Broker(str(Path(directory) / "broker"), clock=lambda: NOW)
        server = broker_module.BusHTTPServer(("127.0.0.1", 0), broker_module.handler_factory(broker))
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        broker_url = f"http://127.0.0.1:{server.server_port}"
        process = None
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=5) as local_client:
                async def admin(op, **fields):
                    response = await local_client.post(broker_url + "/v1", json={"op": op, **fields},
                        headers={"Authorization": "Bearer " + broker.admin_token, "X-Communicate-Bus-Gateway": SECRET})
                    result = response.json()
                    require(response.status_code == 200 and result.get("ok"), "Fixture broker agent operation failed: " + op)
                    return result
                if bus != "general":
                    await admin("create", bus=bus)
                target = (await admin("register", bus=bus, session_key="stack-target", name="Same fixture name", kind="codex"))["id"]
                other_target = (await admin("register", bus=bus, session_key="stack-other-target", name="Same fixture name", kind="codex"))["id"]
                runner = Path(directory) / "gateway.mjs"
                runner.write_text(NODE_ADAPTER)
                env = {"PATH": os.environ.get("PATH", ""), "CHAT_STACK_FIXTURE": json.dumps({"gateway": str(gateway), "broker": broker_url,
                    "token": TOKEN, "otherToken": OTHER_TOKEN, "owner": OWNER, "other": OTHER,
                    "secret": SECRET, "bus": bus, "group": group, "now": NOW})}
                process = await asyncio.create_subprocess_exec(node, "--no-warnings", "--loader", str(gateway / "test-loader.mjs"), str(runner),
                    env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                startup = await asyncio.wait_for(process.stdout.readline(), timeout=10)
                require(bool(startup), "Gateway adapter did not start")
                gateway_url = "http://127.0.0.1:" + str(json.loads(startup)["port"])
                outgoing = []
                real_client = httpx.AsyncClient
                async def to_gateway(request):
                    require(str(request.url) == BRIDGE, "Pipe attempted a non-bridge request")
                    require(request.method == "POST", "Pipe attempted a non-POST request")
                    outgoing.append(json.loads(request.content))
                    response = await local_client.post(gateway_url + "/_bus/chat/bridge", content=request.content,
                                                       headers=dict(request.headers))
                    return httpx.Response(response.status_code, headers=response.headers, content=response.content)
                def pipe_client(**kwargs):
                    require(kwargs == {"follow_redirects": False, "trust_env": False}, "Pipe weakened network restrictions")
                    return real_client(**kwargs, transport=httpx.MockTransport(to_gateway))
                pipe = pipe_module.Pipe()
                pipe.valves = pipe.Valves(agents=[{"bus": bus, "id": target, "name": "Same fixture name",
                    "user": "stack-owner", "device": "fixture-device"}], allowed_user_ids=[OWNER], reply_wait_seconds=3,
                    poll_interval_seconds=0.1, request_timeout_seconds=2)
                def invocation(turn="turn-1", user=OWNER, token=TOKEN):
                    message = {"id": turn, "role": "user", "content": "Identical intentional fixture text"}
                    request = Request({"type": "http", "headers": [(b"authorization", ("Bearer " + token).encode())]})
                    request.state.user = SimpleNamespace(id=user)
                    return {"body": {"model": f"communicate_bus.{bus}--{target}", "messages": [
                        {"role": "system", "content": "Do not forward this system prompt"},
                        {"role": "user", "content": "Do not resend history"}, copy.deepcopy(message)]},
                        "__user__": {"id": user}, "__request__": request,
                        "__metadata__": {"user_id": user, "chat_id": "stack-owui-chat", "user_message_id": turn,
                            "user_message": message, "message_id": "assistant-" + turn}}
                async def receive():
                    for _ in range(100):
                        rows = (await admin("poll", agent=target))["messages"]
                        if rows:
                            require(len(rows) == 1, "Exactly one agent request should be leased")
                            return rows[0]
                        await asyncio.sleep(0.02)
                    raise AssertionError("Pipe send did not reach the exact fixture agent")
                async def reply(message, content):
                    require(message["target"] == target, "Wrong registered recipient")
                    require(message["sender_type"] == "human" and message["sender"]["id"] == "human.stack-owner", "Human attribution was not verified")
                    require(message["message"] == "Identical intentional fixture text", "History or middleware text reached the agent")
                    require(message["reply_to"] == message["id"], "Reply envelope did not correlate to the original human send")
                    require((await admin("poll", agent=other_target))["messages"] == [], "Same-name registration received the message")
                    await admin("ack", agent=target, id=message["id"], lease=message["lease"], status="queued")
                    await admin("reply", sender=target, id=message["id"], message=content)
                async def bridge(payload, user=OWNER, token=TOKEN, path="/_bus/chat/bridge"):
                    return await local_client.post(gateway_url + path, json=payload,
                        headers={"Authorization": "Bearer " + token, "X-Communicate-Expected-User": user})
                with patch.object(pipe_module.httpx, "AsyncClient", side_effect=pipe_client):
                    pending = asyncio.create_task(pipe.pipe(**invocation()))
                    try:
                        first = await receive()
                        await reply(first, "Explicit first stack reply")
                        answer = await asyncio.wait_for(pending, timeout=5)
                    finally:
                        if not pending.done():
                            pending.cancel()
                            await asyncio.gather(pending, return_exceptions=True)
                    require("Explicit first stack reply" in answer, "Explicit broker reply was not rendered by the real Pipe")
                    require("Open bus conversation" in answer and first["chat"] in answer, "Pipe omitted the shared conversation link")
                    # Same native user message, regenerated assistant response.
                    retry = invocation()
                    retry["__metadata__"]["message_id"] = "regenerated-assistant-id"
                    again = await pipe.pipe(**retry)
                    require("Explicit first stack reply" in again, "Retry did not retrieve the original correlated broker reply")
                    require((await admin("poll", agent=target))["messages"] == [], "Retry dispatched a duplicate agent request")
                    history = (await bridge({"op": "chat_messages", "chat": first["chat"]})).json()
                    require(len(history["messages"]) == 2, "Broker did not persistently deduplicate retry")
                    # A deliberate identical message is a second user turn.
                    pending = asyncio.create_task(pipe.pipe(**invocation("turn-2")))
                    try:
                        second = await receive()
                        require(second["id"] != first["id"], "Repeated intentional text was incorrectly deduplicated")
                        require(second["chat"] == first["chat"], "Second turn did not share the same broker inbox")
                        await reply(second, "Explicit second stack reply")
                        second_answer = await asyncio.wait_for(pending, timeout=5)
                    finally:
                        if not pending.done():
                            pending.cancel()
                            await asyncio.gather(pending, return_exceptions=True)
                    require("Explicit second stack reply" in second_answer and "Explicit first stack reply" not in second_answer,
                            "Pipe displayed a reply correlated with the wrong turn")
                    before = len(outgoing)
                    denied = await pipe.pipe(**invocation(user=OTHER, token=OTHER_TOKEN))
                    require("not enabled" in denied and len(outgoing) == before, "Pipe did not deny another user before network")
                # Bypass the local valve check to test the gateway+broker boundary.
                denied = await bridge({"op": "chat_messages", "chat": first["chat"]}, OTHER, OTHER_TOKEN)
                require(denied.status_code == 403, "Verified non-allowlisted reader reached the human inbox")
                mismatch = await bridge({"op": "chat_list"}, OWNER, OTHER_TOKEN)
                require(mismatch.status_code == 403, "Gateway trusted the expected-user header instead of its live identity lookup")
                require((await bridge({"op": "chat_list"}, path="/_bus/chat")).status_code == 404, "Private broker bridge endpoint was publicly exposed")
                history = (await bridge({"op": "chat_messages", "chat": first["chat"]})).json()
                require([row["role"] for row in history["messages"]] == ["user", "assistant", "user", "assistant"], "Shared inbox history differs from the delivered turns")
                require((await admin("poll", agent=target))["messages"] == [], "Acceptance left an extra agent message queued")
                sends = [payload for payload in outgoing if payload["op"] == "chat_send"]
                require(len(sends) == 3 and sends[0]["request_id"] == sends[1]["request_id"] != sends[2]["request_id"], "Pipe idempotency keys do not match actual broker deduplication")
                stats = (await local_client.get(gateway_url + "/__fixture/stats")).json()
                require(stats["sessions"] >= len(stats["origins"]), "Gateway did not validate every forwarded session")
                require(any(item["reader"] == "owui." + OTHER for item in stats["origins"]), "Non-owner denial was not exercised at the real broker boundary")
                require(all(item["op"].startswith("chat_") for item in stats["origins"]), "Gateway forwarded a non-chat operation")
                print("PASS: real Pipe → gateway handleBusProxy → broker HTTP → exact fixture agent poll/ack/reply → correlated Pipe answer")
                print("PASS: persistent retry deduplication, intentional repeated text, shared inbox, same-name isolation, per-request identity verification, non-owner denial, private origin endpoint")
        finally:
            if process:
                if process.returncode is None:
                    process.terminate()
                try:
                    _, stderr = await asyncio.wait_for(process.communicate(), timeout=3)
                except TimeoutError:
                    process.kill()
                    _, stderr = await process.communicate()
                if stderr:
                    sys.stderr.write(stderr.decode(errors="replace"))
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway-source", type=Path, required=True)
    parser.add_argument("--platform-source", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(run(args.gateway_source.resolve(), args.platform_source.resolve()))


if __name__ == "__main__":
    main()
