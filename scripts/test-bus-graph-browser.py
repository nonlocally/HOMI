#!/usr/bin/env python3
"""Headless graph interactions against a temporary real broker, no live agents.

Run: uv run --with playwright python scripts/test-bus-graph-browser.py
Requires Playwright Chromium (python -m playwright install chromium).
COMM_GRAPH_SCREENSHOT optionally saves the fake roster for visual review.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from bus_broker import Broker, BusHTTPServer, handler_factory


def main():
    for name in ("BUS_GATEWAY_SHARED_SECRET", "BUS_READER_USERS", "BUS_ADMIN_READERS", "BUS_OPENWEBUI_READERS"):
        os.environ.pop(name, None)
    with tempfile.TemporaryDirectory(prefix="communicate-graph-browser-") as temp:
        broker = Broker(temp)

        def call(token, op, **fields):
            result = broker.handle(token, {"op": op, **fields})
            assert result["ok"], result
            return result

        admin = broker.admin_token
        call(admin, "create", bus="qit-wilde")
        call(admin, "create", bus="secret-bus-sentinel")
        call(admin, "register", session_key="secret", name="secret-agent-sentinel", bus="secret-bus-sentinel")

        def device(name, owner):
            invite = call(admin, "invite", bus="qit-wilde", user=owner)
            return call(None, "redeem", invite=invite["invite"], device=name,
                        device_metadata={"hostname": name, "platform": "test", "tailscale_hostname": name})

        publisher = device("lab-workstation", "aadarwal")
        reader = device("viewer-fixture", "peer")
        names = ["Coordinator", "Literature", "Proofs", "Reviewer", "Experiments", "Analyst",
                 "Simulation", "Synthesis", "Compiler", "Benchmarks", "Planner", "Notes",
                 "Audit", "References", "Figures", "Checks", "Editor", "Methods", "Results",
                 "Validation", "<img src=x onerror=alert(1)>"]
        agents = []
        for i, name in enumerate(names):
            agents.append(call(publisher["token"], "register", bus="qit-wilde", session_key=f"fake:{i}",
                               name=name, kind="codex" if i % 2 else "claude",
                               status="queueable" if i % 2 else "live",
                               description=f"Fixture research role {i}")["id"])
        for i in range(1, len(agents)):
            call(publisher["token"], "send", bus="qit-wilde", sender=agents[0], target=agents[i], message="private payload sentinel")
        call(publisher["token"], "send", bus="qit-wilde", sender=agents[1], target=agents[0], message="private reply sentinel")
        server = BusHTTPServer(("127.0.0.1", 0), handler_factory(broker))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1500, "height": 1150}, device_scale_factor=1)
                errors, operations = [], []
                page.on("pageerror", lambda error: errors.append(str(error)))

                def request_seen(request):
                    if request.method == "POST" and request.url.endswith("/v1"):
                        operations.append(json.loads(request.post_data)["op"])

                page.on("request", request_seen)
                page.goto(f"http://127.0.0.1:{server.server_port}/#token={reader['token']}")
                graph = page.locator("#graph-panel")
                page.wait_for_function("!document.getElementById('app').hidden")
                if graph.is_hidden():
                    diagnostic = page.evaluate("""() => {
                        try { CommunicateGraph.mount(document.getElementById('graph-panel')); return 'mount succeeded'; }
                        catch (error) { return String(error.stack); }
                    }""")
                    raise AssertionError({"errors": errors, "graph_mount": diagnostic})
                expect(graph).to_be_visible()
                nodes = graph.locator(".svelte-flow__node-agent")
                expect(nodes).to_have_count(21)
                expect(graph.locator(".svelte-flow__edge")).to_have_count(21)
                expect(page.locator("#table-wrap")).to_be_hidden()
                text = graph.inner_text()
                assert "secret-agent-sentinel" not in text and "secret-bus-sentinel" not in text
                assert "private payload sentinel" not in text
                assert "lab-workstation" in text and "aadarwal" in text
                assert "<img src=x onerror=alert(1)>" in text
                assert graph.locator("img").count() == 0
                # Twenty-one agents on one device must wrap, not form one tall column.
                positions = nodes.evaluate_all("els => els.map(e => e.style.transform)")
                assert len({pos.split(',')[0] for pos in positions}) >= 3, positions
                target = graph.locator(f'[data-id="agent:{agents[0]}"]')
                target.click()
                expect(graph.locator(".cg-inspector")).to_contain_text("Coordinator")
                before = target.get_attribute("style")
                box = target.bounding_box()
                page.mouse.move(box["x"] + box["width"] / 2, box["y"] + 25)
                page.mouse.down()
                page.mouse.move(box["x"] + box["width"] / 2 + 70, box["y"] + 65, steps=12)
                page.mouse.up()
                expect(target).not_to_have_attribute("style", before)
                moved = target.get_attribute("style")
                await_snapshot = page.expect_response(lambda response: response.url.endswith('/v1'))
                with await_snapshot:
                    page.locator("#refresh").click()
                expect(target).to_have_attribute("style", moved)
                # Graph/List retains the arrangement without any agent operation.
                page.locator("#view-list").click()
                expect(page.locator("#table-wrap")).to_be_visible()
                page.locator("#view-graph").click()
                expect(target).to_have_attribute("style", moved)
                page.locator("#search").fill("Coordinator")
                expect(nodes).to_have_count(1)
                expect(graph.locator(".svelte-flow__edge")).to_have_count(0)
                page.locator("#search").fill("")
                expect(nodes).to_have_count(21)
                expand = graph.get_by_role("button", name="Expand graph canvas", exact=True)
                expand.click()
                expect(graph.get_by_role("button", name="Collapse graph canvas", exact=True)).to_have_attribute("aria-expanded", "true")
                viewport = graph.locator(".svelte-flow__viewport")
                graph.get_by_role("button", name="Fit", exact=True).click()
                page.wait_for_timeout(250)  # Finish the explicit fit animation before measuring zoom.
                before_zoom = viewport.get_attribute("style")
                graph.get_by_role("button", name="Zoom In", exact=True).click()
                expect(viewport).not_to_have_attribute("style", before_zoom)
                graph.get_by_role("button", name="Fit", exact=True).click()
                page.wait_for_timeout(250)
                if os.environ.get("COMM_GRAPH_SCREENSHOT"):
                    page.screenshot(path=os.environ["COMM_GRAPH_SCREENSHOT"], full_page=True)
                page.keyboard.press("Escape")
                expect(expand).to_have_attribute("aria-expanded", "false")
                expect(expand).to_be_focused()
                page.set_viewport_size({"width": 390, "height": 844})
                expect(graph).to_be_visible()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), "mobile page overflows"
                # Removing membership clears nodes, incident edges and selected details.
                call(publisher["token"], "leave", agent=agents[0], bus="qit-wilde")
                page.locator("#refresh").click()
                expect(nodes).to_have_count(20)
                expect(graph.locator(".svelte-flow__edge")).to_have_count(0)
                expect(graph.locator(".cg-inspector")).to_have_count(0)
                call(admin, "revoke", principal=reader["principal"])
                page.locator("#refresh").click()
                expect(page.locator("#app")).to_be_hidden()
                expect(nodes).to_have_count(0)
                assert set(operations) == {"snapshot"}, operations
                assert not errors, errors
                browser.close()
                print("PASS: headless graph — real scoped broker, 21-node layout, edges, drag, refresh, filters, mobile, revocation, zero command dispatch")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    main()
