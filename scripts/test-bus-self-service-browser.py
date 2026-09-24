#!/usr/bin/env python3
"""Private-bus self-service UI with an isolated fake API, never real accounts.

Run: uv run --with playwright python scripts/test-bus-self-service-browser.py
Requires Playwright Chromium. No provider, device, or hosted-bus calls are made.
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


class FakeAPI:
    def __init__(self):
        self.user = "github-101"
        self.users = [{"id": "github-101", "label": "research-owner"},
                      {"id": "github-102", "label": "collaborator"},
                      {"id": "github-103", "label": "<literal-new-person>"}]
        self.buses = {"study": {"owner": "github-101", "members": ["github-101", "github-102"], "events": []}}
        self.requests = []
        self.lock = threading.RLock()

    def snapshot(self):
        rows = [{"name": "general", "visibility": "open", "owner_user": None, "role": "viewer",
                 "capabilities": {"invite": False, "manage_members": False, "leave": False}, "agents": []}]
        for name, bus in self.buses.items():
            if self.user not in bus["members"]:
                continue
            owner = self.user == bus["owner"]
            row = {"name": name, "visibility": "private", "owner_user": bus["owner"],
                   "role": "owner" if owner else "member", "agents": [],
                   "capabilities": {"invite": True, "manage_members": owner, "leave": not owner, "event_join": owner}}
            if owner:
                row["events"] = [{key: value for key, value in event.items() if key != "participants"}
                                 for event in bus.get("events", [])]
                row["members"] = [{"user": user, "role": "owner" if user == bus["owner"] else "member"}
                                  for user in bus["members"]]
            rows.append(row)
        return {"ok": True, "server_id": "self-service-fixture", "browser_session": True,
                "read_only": True, "is_admin": False, "can_create_bus": True, "user": self.user,
                "users": self.users, "buses": rows, "chat": {"enabled": False, "buses": []}}

    def handle(self, request):
        with self.lock:
            self.requests.append(copy.deepcopy(request))
            op = request["op"]
            if op == "snapshot":
                return self.snapshot()
            if op == "create":
                assert request["bus"] not in self.buses
                self.buses[request["bus"]] = {"owner": self.user, "members": [self.user]}
                return {"ok": True, "owner_user": self.user}
            bus = self.buses.get(request.get("bus"))
            if op == "event_create":
                assert bus["owner"] == self.user
                assert request["ttl"] == 14400 and request["max_uses"] == 40
                event = {"id": "fixture-event-1", "bus": request["bus"], "created_at": time.time(),
                         "expires_at": time.time() + request["ttl"], "max_uses": request["max_uses"],
                         "uses": 0, "active": True, "revoked": False, "participants": []}
                bus.setdefault("events", []).append(event)
                return {"ok": True, "event": event, "invite": "fixture-shared-event-code"}
            if op in ("event_get", "event_revoke", "event_remove"):
                bus, event = next((bus, event) for bus in self.buses.values() for event in bus.get("events", [])
                                  if event["id"] == request["event"])
                assert bus["owner"] == self.user
                if op == "event_get":
                    return {"ok": True, "event": copy.deepcopy(event)}
                if op == "event_revoke":
                    event.update(revoked=True, active=False)
                else:
                    next(p for p in event["participants"] if p["principal"] == request["principal"])["removed"] = True
                return {"ok": True}
            if op in ("member_add", "member_remove"):
                assert bus and request["user"] in {user["id"] for user in self.users}
                if op == "member_add":
                    assert self.user == bus["owner"]
                    bus["members"].append(request["user"])
                else:
                    assert request["user"] != bus["owner"]
                    assert self.user == bus["owner"] or request["user"] == self.user
                    bus["members"].remove(request["user"])
                return {"ok": True}
            if op == "invite":
                assert bus and request["user"] in bus["members"]
                assert self.user == bus["owner"] or request["user"] == self.user
                return {"ok": True, "invite": "fixture-one-use-invitation", "expires_at": 2100000000}
            if op == "invite_revoke":
                assert request["invite"] == "fixture-one-use-invitation"
                return {"ok": True}
            raise AssertionError(request)


def handler(api):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            route = urlsplit(self.path).path
            name = {"/": "lib/bus_ui.html", "/graph": "lib/bus_ui.html",
                    "/assets/bus-graph.js": "lib/assets/bus-graph.js",
                    "/assets/bus-graph.css": "lib/assets/bus-graph.css"}.get(route)
            if not name:
                self.send_error(404)
                return
            data = (ROOT / name).read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html" if name.endswith("html") else
                             "application/javascript" if name.endswith("js") else "text/css")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            assert self.path == "/v1", self.path
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            data = json.dumps(api.handle(request)).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    return Handler


def main():
    api = FakeAPI()
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(api))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("dialog", lambda dialog: dialog.accept())
            origin = f"http://127.0.0.1:{server.server_port}"
            page.goto(origin + "/graph?bus=study#token=fixture-browser-token")
            expect(page.locator("#graph-back")).to_have_text("← Manage buses")
            page.locator("#graph-back").click()
            expect(page.locator("#page-title")).to_have_text("study")
            expect(page.locator("#devices-button")).to_be_hidden()
            expect(page.locator("#inbox-button")).to_be_hidden()
            expect(page.locator("#leave-button")).to_be_hidden()
            page.locator("#members-button").click()
            expect(page.locator("#member-list")).to_contain_text("research-owner")
            expect(page.locator("#member-list button")).to_have_count(1)
            page.locator("#member-user").select_option("github-103")
            page.locator("#member-submit").click()
            expect(page.locator("#member-list")).to_contain_text("<literal-new-person>")
            assert page.locator("#member-list literal-new-person").count() == 0
            page.get_by_role("button", name="Remove <literal-new-person> from study", exact=True).click()
            expect(page.locator("#member-list")).not_to_contain_text("<literal-new-person>")
            page.locator('#members-dialog [data-close]').first.click()
            page.locator("#invite-button").click()
            expect(page.locator("#invite-user option")).to_have_count(3)
            page.locator("#invite-user").select_option("github-102")
            page.locator("#local-only").check()
            page.locator("#invite-submit").click()
            expect(page.locator("#invite-output")).to_contain_text("communicate bus connect commbus1.")
            assert api.requests[-1]["user"] == "github-102"
            page.locator("#revoke-invite").click()
            expect(page.locator("#invite-output")).to_have_text("Invitation revoked.")
            page.locator('#invite-dialog [data-close]').first.click()
            page.locator("#event-button").click()
            expect(page.locator("#event-ttl")).to_have_value("14400")
            expect(page.locator("#event-limit")).to_have_value("40")
            page.locator("#event-local").check()
            page.locator("#event-submit").click()
            expect(page.locator("#event-output")).to_contain_text("commbus1.")
            expect(page.locator("#event-list")).to_contain_text("0 of 40 devices")
            assert not any(r["op"] == "redeem" for r in api.requests), "creating a code must not enroll the viewer"
            event = api.buses["study"]["events"][0]
            event["uses"] = 2
            event["participants"] = [{"principal": "fixture-event-device-1", "user": "event-guest-1",
                                      "device": "Guest laptop", "joined_at": time.time(), "removed": False},
                                     {"principal": "fixture-event-device-2", "user": "event-guest-2",
                                      "device": "<literal guest>", "joined_at": time.time(), "removed": False}]
            page.locator("#refresh").evaluate("element => element.click()")
            expect(page.locator("#event-list")).to_contain_text("2 of 40 devices")
            expect(page.locator("#event-list")).not_to_contain_text("Guest laptop")
            assert not any(r["op"] == "event_get" for r in api.requests), "summaries must not preload participants"
            page.get_by_role("button", name="Close event code fixture-event-1", exact=True).click()
            expect(page.locator("#event-list")).to_contain_text("Closed")
            expect(page.locator("#event-result")).to_be_hidden()
            assert not any(p["removed"] for p in event["participants"]), "closing code must leave participants connected"
            page.get_by_role("button", name="View devices for event fixture-event-1", exact=True).click()
            expect(page.locator("#event-list")).to_contain_text("Guest laptop")
            assert sum(r["op"] == "event_get" for r in api.requests) == 1
            page.get_by_role("button", name="Remove event device fixture-event-device-1", exact=True).click()
            expect(page.locator("#event-list")).to_contain_text("1 device connected")
            assert sum(r["op"] == "event_get" for r in api.requests) == 2
            assert event["participants"][0]["removed"] and not event["participants"][1]["removed"]
            page.locator('#event-dialog [data-close]').first.click()
            page.locator("#create-button").click()
            page.locator("#new-bus").fill("new-study")
            page.locator("#create-submit").click()
            expect(page.locator("#page-title")).to_have_text("new-study")
            assert api.buses["new-study"]["owner"] == "github-101"
            # An ordinary member sees their own enrollment and leave controls only.
            api.user = "github-102"
            page.goto(origin + "/?bus=study")
            expect(page.locator("#members-button")).to_be_hidden()
            expect(page.locator("#event-button")).to_be_hidden()
            expect(page.locator("#leave-button")).to_be_visible()
            page.locator("#invite-button").click()
            expect(page.locator("#invite-user")).to_be_disabled()
            expect(page.locator("#invite-user")).to_have_value("github-102")
            page.locator('#invite-dialog [data-close]').first.click()
            page.locator("#leave-button").click()
            expect(page.locator("#page-title")).to_have_text("All buses")
            assert "github-102" not in api.buses["study"]["members"]
            # Owner management remains usable on a narrow mobile viewport.
            api.user = "github-101"
            page.set_viewport_size({"width": 390, "height": 844})
            page.goto(origin + "/?bus=study")
            page.locator("#members-button").click()
            expect(page.locator("#member-submit")).to_be_visible()
            expect(page.locator("#member-list .role")).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "mobile page overflows"
            bounds = page.locator("#members-dialog").bounding_box()
            assert bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= 390
            if os.environ.get("COMM_SELF_SERVICE_SCREENSHOT"):
                page.screenshot(path=os.environ["COMM_SELF_SERVICE_SCREENSHOT"])
            page.locator('#members-dialog [data-close]').first.click()
            page.locator("#event-button").click()
            expect(page.locator("#event-limit")).to_be_visible()
            bounds = page.locator("#event-dialog").bounding_box()
            assert bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= 390
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "mobile event dialog overflows"
            if os.environ.get("COMM_SELF_SERVICE_SCREENSHOT"):
                screenshot = Path(os.environ["COMM_SELF_SERVICE_SCREENSHOT"])
                page.screenshot(path=str(screenshot.with_name(screenshot.stem + "-event" + screenshot.suffix)))
            assert not errors, errors
            assert not any(r["op"] in ("revoke", "chat_send", "register", "join", "leave") for r in api.requests)
            browser.close()
        print("PASS self-service owner/member/event workflow, canonical IDs, invitation scope, graph entry, mobile layout")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
