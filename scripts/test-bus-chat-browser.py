#!/usr/bin/env python3
"""Deterministic human chat UI checks. Fake API only; no real agent dispatch.

Run: uv run --with playwright python scripts/test-bus-chat-browser.py
Requires Playwright Chromium and a current bus-graph bundle.
COMM_CHAT_SCREENSHOT optionally saves the open drawer for visual review.
"""
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
import time
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def fixture():
    now = time.time()
    agent = {"id": "fixture-registration-a", "name": "Research", "kind": "claude",
             "user": "aadarwal", "device": "fixture-lab", "device_id": "fixture-device-a",
             "status": "live", "last_seen": now, "buses": ["general", "photonics"]}
    other = {**agent, "id": "fixture-registration-b", "name": "Research", "kind": "codex",
             "device": "fixture-laptop", "device_id": "fixture-device-b", "status": "queueable",
             "buses": ["general"]}
    return {"ok": True, "server_id": "chat-fixture", "user": "aadarsh", "display_name": "Aadarwal",
            "browser_session": True, "read_only": True, "is_admin": False,
            "chat": {"enabled": True, "buses": ["general", "photonics"],
                     "openwebui": [{"bus": "general", "agent": agent["id"]},
                                  {"bus": "photonics", "agent": agent["id"]}]},
            "buses": [{"name": "general", "agents": [agent, other]},
                      {"name": "photonics", "agents": [agent]}]}


class FakeAPI:
    def __init__(self):
        self.snapshot = fixture()
        self.chats = {}
        self.messages = {}
        self.requests = []
        self.seen = {}
        self.uncertain_send = False
        self.page_cap = 100
        self.delay_chat = None
        self.delay_started = threading.Event()
        self.delay_release = threading.Event()
        self.lock = threading.RLock()
        self.open("general", self.snapshot["buses"][0]["agents"][0])

    def open(self, bus, agent):
        chat_id = "chat-" + bus + "-" + agent["id"]
        self.chats.setdefault(chat_id, {"id": chat_id, "bus": bus, "agent": copy.deepcopy(agent),
                                      "created_at": 1, "updated_at": 2, "can_send": True, "unread": 0})
        self.messages.setdefault(chat_id, [])
        self.seen.setdefault(chat_id, set())
        return self.chats[chat_id]

    def reply(self, chat_id, text, in_reply_to=None):
        with self.lock:
            seq = len(self.messages[chat_id]) + 1
            msg = {"id": f"fixture-reply-{chat_id}-{seq}", "seq": seq, "role": "assistant",
                   "content": text, "created_at": time.time(), "status": "replied"}
            if in_reply_to:
                msg["in_reply_to"] = in_reply_to
            self.messages[chat_id].append(msg)
            self.chats[chat_id]["unread"] += 1
            return msg

    def handle(self, request):
        with self.lock:
            self.requests.append(copy.deepcopy(request))
            op = request["op"]
            if op == "snapshot":
                return copy.deepcopy(self.snapshot), 200
            if not self.snapshot["chat"]["enabled"] or not self.snapshot["browser_session"]:
                return {"ok": False, "error": "Human chat is not enabled"}, 403
            if op == "chat_list":
                return {"ok": True, "chats": list(copy.deepcopy(self.chats).values())}, 200
            if op == "chat_open":
                bus = next(b for b in self.snapshot["buses"] if b["name"] == request["bus"])
                agent = next(a for a in bus["agents"] if a["id"] == request["agent"])
                return {"ok": True, "chat": copy.deepcopy(self.open(bus["name"], agent))}, 200
            chat_id = request["chat"]
            if chat_id not in self.chats:
                return {"ok": False, "error": "Conversation unavailable"}, 404
            if op == "chat_messages":
                rows = [m for m in self.messages[chat_id] if m["seq"] > request.get("after", 0)]
                page = copy.deepcopy(rows[:min(request.get("limit", 100), self.page_cap)])
                self.seen[chat_id].update(m["seq"] for m in page)
                response = {"ok": True, "chat": copy.deepcopy(self.chats[chat_id]), "messages": page,
                            "has_more": len(rows) > len(page),
                            "next_after": page[-1]["seq"] if page else request.get("after", 0)}
            elif op == "chat_read":
                assert all(seq in self.seen[chat_id] for seq in range(1, request["through"] + 1)), request
                unread = sum(m["role"] == "assistant" and m["seq"] > request["through"] for m in self.messages[chat_id])
                self.chats[chat_id]["unread"] = unread
                return {"ok": True, "unread": unread}, 200
            elif op == "chat_send":
                assert "sender" not in request and "agent" not in request
                prior = next((m for rows in self.messages.values() for m in rows
                              if m.get("request_id") == request["request_id"]), None)
                if prior:
                    assert prior["content"] == request["message"]
                    return {"ok": True, "chat": chat_id, "message": copy.deepcopy(prior), "deduplicated": True}, 200
                seq = len(self.messages[chat_id]) + 1
                message = {"id": f"fixture-user-{chat_id}-{seq}", "seq": seq, "role": "user",
                           "content": request["message"], "created_at": time.time(), "status": "queued",
                           "request_id": request["request_id"]}
                self.messages[chat_id].append(message)
                self.seen[chat_id].add(seq)
                if self.uncertain_send:
                    self.uncertain_send = False
                    return {"ok": False, "error": "Fixture connection interrupted after acceptance"}, 503
                return {"ok": True, "chat": chat_id, "message": copy.deepcopy(message), "deduplicated": False}, 200
            else:
                raise AssertionError(request)
        if op == "chat_messages" and self.delay_chat == chat_id:
            self.delay_started.set()
            self.delay_release.wait(timeout=15)
        return response, 200


def handler(api):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            path = urlsplit(self.path).path
            relative = {"/": "lib/bus_ui.html", "/graph": "lib/bus_ui.html",
                        "/assets/bus-graph.js": "lib/assets/bus-graph.js",
                        "/assets/bus-graph.css": "lib/assets/bus-graph.css"}.get(path)
            if not relative:
                self.send_error(404)
                return
            data = (ROOT / relative).read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html" if relative.endswith("html") else
                             "application/javascript" if relative.endswith("js") else "text/css")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            assert self.path == "/v1", self.path
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            data, status = api.handle(request)
            encoded = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            try:
                self.wfile.write(encoded)
            except (BrokenPipeError, ConnectionResetError):
                pass  # An obsolete request was correctly aborted by the UI.

    return Handler


def main():
    api = FakeAPI()
    original_id = next(iter(api.chats))
    api.reply(original_id, '<img src=x onerror="alert(1)">\nLiteral agent reply')
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(api))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            origin = f"http://127.0.0.1:{server.server_port}"
            page.goto(origin + "/graph#token=fixture-browser-token")
            graph = page.locator("#graph-panel")
            expect(graph).to_be_visible()
            expect(graph.get_by_role("button", name="Inbox, 1 unread replies")).to_be_visible()
            expect(graph.locator('.cg-canvas')).to_have_attribute('data-flow-pending', 'false')
            first = graph.locator('[data-id="agent:fixture-registration-a"]')
            first.click()
            inspector = graph.get_by_role("complementary", name="Selected agent details")
            expect(inspector.get_by_label("Chat on bus")).to_be_visible()
            inspector.get_by_label("Chat on bus").select_option("general")
            expect(inspector.get_by_role("link", name="Open in Nonlocally")).to_have_attribute(
                "href", "https://mit.nonlocally.org/?models=communicate_bus.general--fixture-registration-a")
            before = graph.locator(".cg-canvas").bounding_box()
            inspector.get_by_role("button", name="Chat", exact=True).click()
            dialog = page.get_by_role("dialog")
            expect(dialog).to_be_visible()
            expect(page.locator("#chat-messages")).to_contain_text("Literal agent reply")
            assert graph.locator(".cg-canvas").bounding_box() == before, "chat shrank graph canvas"
            assert page.locator("#chat-messages img").count() == 0
            expect(page.locator("#chat-identity")).to_have_text("aadarwal · fixture-lab · general")
            page.get_by_text("Exact session and device", exact=True).click()
            expect(page.locator("#chat-registration")).to_have_text("Agent ID: fixture-registration-a")
            expect(page.locator("#chat-device-id")).to_have_text("Device ID: fixture-device-a")
            assert not any(r["op"] == "chat_send" for r in api.requests)
            # Only a user's submit dispatches, including normal keyboard behavior.
            composer = page.locator("#chat-input")
            composer.fill("First line")
            composer.press("Shift+Enter")
            composer.type("Second line")
            assert not any(r["op"] == "chat_send" for r in api.requests)
            composer.press("Enter")
            expect(composer).to_have_value("")
            expect(page.locator("#chat-messages")).to_contain_text("Queued for agent · awaiting reply")
            assert api.messages[original_id][-1]["content"] == "First line\nSecond line"
            assert api.messages[original_id][-1]["role"] == "user"
            sent = api.messages[original_id][-1]
            api.reply(original_id, "Fixture explicit reply", sent["id"])
            page.locator("#graph-refresh").evaluate("element => element.click()")
            expect(page.locator("#chat-messages")).to_contain_text("Fixture explicit reply")
            expect(page.locator("#chat-messages")).to_contain_text("Agent replied")
            # A response lost after persistence must retry with the same key.
            api.uncertain_send = True
            composer.fill("One uncertain send")
            composer.press("Enter")
            expect(page.locator("#chat-error")).to_contain_text("Fixture connection interrupted")
            page.locator("#chat-retry").click()
            expect(composer).to_have_value("")
            uncertain = [r for r in api.requests if r["op"] == "chat_send" and r["message"] == "One uncertain send"]
            assert len(uncertain) == 2 and uncertain[0]["request_id"] == uncertain[1]["request_id"]
            assert sum(m["content"] == "One uncertain send" for m in api.messages[original_id]) == 1
            if os.environ.get("COMM_CHAT_SCREENSHOT"):
                page.screenshot(path=os.environ["COMM_CHAT_SCREENSHOT"])
            # Native dialog focus stays contained; Escape restores the graph.
            for _ in range(16):
                page.keyboard.press("Tab")
                assert page.evaluate("document.activeElement === document.body || document.querySelector('#chat-dialog').contains(document.activeElement)"), page.evaluate("document.activeElement.outerHTML")
            page.keyboard.press("Escape")
            expect(dialog).not_to_be_visible()
            expect(inspector).to_be_visible()
            catalog = api.snapshot["chat"].pop("openwebui")
            page.locator("#graph-refresh").click()
            expect(inspector.get_by_role("link", name="Open in Nonlocally")).to_have_count(0)
            expect(inspector.get_by_role("button", name="Chat", exact=True)).to_be_visible()
            inspector.get_by_role("button", name="Chat", exact=True).click()
            expect(page.locator("#chat-nonlocally")).to_be_hidden()
            api.snapshot["chat"]["openwebui"] = catalog
            page.locator("#graph-refresh").evaluate("element => element.click()")
            expect(page.locator("#chat-nonlocally")).to_be_visible()
            page.get_by_role("button", name="Close chat", exact=True).click()
            # Inbox has broker history after reload, and exact deep links reopen it.
            count = sum(r["op"] == "chat_send" for r in api.requests)
            page.goto(origin + "/graph?bus=general&chat=" + original_id)
            expect(page.locator("#chat-messages")).to_contain_text("Fixture explicit reply")
            page.reload()
            expect(page.locator("#chat-messages")).to_contain_text("Fixture explicit reply")
            assert sum(r["op"] == "chat_send" for r in api.requests) == count
            page.set_viewport_size({"width": 390, "height": 844})
            expect(dialog).to_be_visible()
            expect(page.locator("#chat-send")).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
            box = dialog.bounding_box()
            assert box["x"] == 0 and box["width"] == 390, box
            if os.environ.get("COMM_CHAT_SCREENSHOT"):
                path = Path(os.environ["COMM_CHAT_SCREENSHOT"])
                page.screenshot(path=str(path.with_name(path.stem + "-mobile" + path.suffix)))
            # Selection aborts an old response even if the server later completes.
            page.set_viewport_size({"width": 1440, "height": 1000})
            page.get_by_role("button", name="Close chat", exact=True).click()
            second = api.open("general", api.snapshot["buses"][0]["agents"][1])
            api.reply(second["id"], "Second exact session history")
            page.locator("#graph-refresh").click()
            graph.get_by_role("button", name="Inbox", exact=False).click()
            api.delay_chat = original_id
            page.locator(".chat-inbox-item").filter(has_text="fixture-lab").click()
            assert api.delay_started.wait(3)
            page.locator("#chat-back").click()
            page.locator(".chat-inbox-item").filter(has_text="fixture-laptop").click()
            expect(page.locator("#chat-messages")).to_contain_text("Second exact session history")
            expect(page.locator("#chat-nonlocally")).to_be_hidden()
            api.delay_release.set()
            expect(page.locator("#chat-messages")).not_to_contain_text("Literal agent reply")
            # Non-allowlisted humans lose composer/history without signing out.
            api.snapshot["chat"] = {"enabled": False, "buses": []}
            page.locator("#graph-refresh").evaluate("element => element.click()")
            expect(dialog).not_to_be_visible()
            expect(page.locator("#chat-messages")).to_be_empty()
            expect(graph.get_by_role("button", name="Inbox", exact=False)).to_have_count(0)
            expect(page.locator("#chat-form")).to_be_hidden()
            # Both device tokens and other humans remain directory-only.
            api.snapshot["browser_session"] = False
            api.snapshot["chat"] = {"enabled": True, "buses": ["general"]}
            page.goto(origin + "/#token=fixture-device-token")
            expect(page.locator("#app")).to_be_visible()
            expect(page.locator("#inbox-button")).to_be_hidden()
            expect(page.locator(".chat-launch")).to_have_count(0)
            api.snapshot["browser_session"] = True
            api.snapshot["user"] = "other-human"
            api.snapshot["chat"] = {"enabled": False, "buses": []}
            page.reload()
            expect(page.locator("#app")).to_be_visible()
            expect(page.locator("#inbox-button")).to_be_hidden()
            # Authorized directory view shares the exact same drawer.
            api.snapshot = fixture()
            page.reload()
            expect(page.locator("#inbox-button")).to_be_visible()
            page.locator("#agent-rows tr").filter(has_text="fixture-lab").get_by_role("button", name="Chat with Research").click()
            expect(page.locator("#chat-bus-field")).to_be_visible()
            page.locator("#chat-bus").select_option("photonics")
            page.locator("#chat-start").click()
            expect(page.locator("#chat-identity")).to_have_text("aadarwal · fixture-lab · photonics")
            expect(page.locator("#chat-form")).to_be_visible()
            # Paged history acknowledges only records actually fetched.
            private_id = "chat-photonics-fixture-registration-a"
            for index in range(105):
                api.reply(private_id, f"Paged fixture reply {index:03d}")
            api.messages[private_id][0].update(role="user", status="accepted")
            page.locator("#refresh").evaluate("element => element.click()")
            expect(page.locator("#chat-messages")).to_contain_text("Paged fixture reply 099")
            expect(page.locator("#chat-more")).to_be_visible()
            expect(page.locator("#chat-messages")).not_to_contain_text("Paged fixture reply 104")
            page.locator("#chat-more").click()
            expect(page.locator("#chat-messages")).to_contain_text("Paged fixture reply 104")
            expect(page.locator("#chat-more")).to_be_hidden()
            expect(page.locator(".chat-message-status").first).to_contain_text("Accepted by broker")
            api.messages[private_id][0]["status"] = "delivered"
            page.locator("#refresh").evaluate("element => element.click()")
            expect(page.locator(".chat-message-status").first).to_contain_text("Delivered to agent · awaiting reply")
            assert any(r["op"] == "chat_messages" and r["chat"] == private_id and r["after"] == 0 and r["limit"] == 1 for r in api.requests)
            # The API can shorten any page to fit a byte budget below limit.
            api.page_cap = 3
            for index in range(6):
                api.reply(private_id, f"Short-page fixture {index}")
            page.locator("#refresh").evaluate("element => element.click()")
            expect(page.locator("#chat-messages")).to_contain_text("Short-page fixture 2")
            expect(page.locator("#chat-messages")).not_to_contain_text("Short-page fixture 5")
            expect(page.locator("#chat-more")).to_be_visible()
            page.locator("#chat-more").click()
            expect(page.locator("#chat-messages")).to_contain_text("Short-page fixture 5")
            expect(page.locator("#chat-more")).to_be_hidden()
            assert not errors, errors
            browser.close()
            print("PASS: fake-only human chat — graph overlay, exact identity/model links, inbox/history/reload, plain text, explicit sends, queued/reply statuses, idempotent retry, keyboard/focus, mobile, aborted selection, revocation, directory, human-only controls, short/paged history, old receipt updates and exact deployed Pipe catalog gating")
    finally:
        api.delay_release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
