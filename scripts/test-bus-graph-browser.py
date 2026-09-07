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
                origin = f"http://127.0.0.1:{server.server_port}"
                page.goto(f"{origin}/?bus=qit-wilde#token={reader['token']}")
                page.wait_for_function("!document.getElementById('app').hidden")
                expect(page.locator("#table-wrap")).to_be_visible()
                expect(page.locator("#graph-panel")).to_be_hidden()
                expect(page.locator("#view-graph")).to_have_attribute("href", "/graph?bus=qit-wilde")
                page.locator("#view-graph").click()
                page.wait_for_url(f"{origin}/graph?bus=qit-wilde")
                graph = page.locator("#graph-panel")
                expect(graph).to_be_visible()
                expect(page.locator("#graph-topbar")).to_be_visible()
                expect(page.locator("#graph-bus")).to_have_value("qit-wilde")
                expect(page.locator("#graph-back")).to_have_attribute("href", "/?bus=qit-wilde")
                expect(page.locator("#table-wrap")).to_be_hidden()
                expect(graph.get_by_role("button", name="Expand graph canvas", exact=True)).to_have_count(0)
                page.reload()
                expect(graph).to_be_visible()
                expect(page.locator("#graph-bus")).to_have_value("qit-wilde")
                page.locator("#graph-bus").select_option("")
                page.wait_for_url(f"{origin}/graph")
                page.locator("#graph-bus").select_option("qit-wilde")
                page.wait_for_url(f"{origin}/graph?bus=qit-wilde")
                nodes = graph.locator(".svelte-flow__node-agent")
                expect(nodes).to_have_count(21)
                expect(graph.locator(".svelte-flow__edge")).to_have_count(21)
                text = graph.inner_text()
                assert "secret-agent-sentinel" not in text and "secret-bus-sentinel" not in text
                assert "private payload sentinel" not in text
                assert "lab-workstation" in text and "aadarwal" in text
                assert "<img src=x onerror=alert(1)>" in text
                assert graph.locator("img").count() == 0
                # Twenty-one agents on one device must wrap, not form one tall column.
                positions = nodes.evaluate_all("els => els.map(e => e.style.transform)")
                assert len({pos.split(',')[0] for pos in positions}) >= 3, positions
                viewport = graph.locator(".svelte-flow__viewport")
                canvas = graph.locator(".cg-canvas")

                def settle_viewport():
                    # Wait for the actual fit/zoom animation to stop, including a
                    # stable interval longer than its 180 ms explicit duration.
                    page.evaluate("window.graphTestViewport = null")
                    page.wait_for_function("""() => {
                        const el = document.querySelector('.svelte-flow__viewport');
                        const value = el?.getAttribute('style');
                        const now = performance.now();
                        if (!window.graphTestViewport || window.graphTestViewport.value !== value) {
                            window.graphTestViewport = {value, at: now};
                            return false;
                        }
                        return now - window.graphTestViewport.at > 220;
                    }""", timeout=5000)

                def transform():
                    return viewport.evaluate("""el => {
                        const m = new DOMMatrix(getComputedStyle(el).transform);
                        return {x: m.m41, y: m.m42, zoom: m.a};
                    }""")

                def assert_no_overflow():
                    assert page.evaluate("""() => document.documentElement.scrollWidth <= innerWidth + 1
                        && document.documentElement.scrollHeight <= innerHeight + 1"""), "graph page overflows"
                    box = graph.bounding_box()
                    assert box["x"] == 0 and abs(box["width"] - page.viewport_size["width"]) <= 1, box
                    assert abs(box["y"] + box["height"] - page.viewport_size["height"]) <= 1, box

                def refresh():
                    with page.expect_response(lambda response: response.url.endswith('/v1')) as pending:
                        # Programmatic click leaves the test's held mouse button
                        # down, exactly as an automatic polling refresh would.
                        page.locator("#graph-refresh").evaluate("el => el.click()")
                    pending.value.body()
                    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")

                def blank_point():
                    point = canvas.evaluate("""el => {
                        const r = el.getBoundingClientRect();
                        for (const dy of [40, r.height / 2, r.height - 100]) {
                            for (const dx of [30, r.width - 30, r.width / 2]) {
                                const x = r.x + dx, y = r.y + dy;
                                const target = document.elementFromPoint(x, y);
                                if (target?.classList.contains('svelte-flow__pane')) return {x,y};
                            }
                        }
                        return null;
                    }""")
                    assert point, "no blank canvas region found"
                    return point

                settle_viewport()
                assert_no_overflow()
                target = graph.locator(f'[data-id="agent:{agents[0]}"]')
                before_canvas = canvas.bounding_box()
                before_viewport = transform()
                target.click()
                expect(graph.locator(".cg-inspector")).to_contain_text("Coordinator")
                assert canvas.bounding_box() == before_canvas, "inspector shrank canvas"
                assert transform() == before_viewport, "inspector moved viewport"
                inspector_box = graph.locator(".cg-inspector").bounding_box()
                page.mouse.move(inspector_box["x"] + 80, inspector_box["y"] + 100)
                page.mouse.wheel(0, 160)
                settle_viewport()
                assert transform() == before_viewport, "scrolling inspector moved canvas"
                graph.get_by_role("button", name="Close agent details").click()
                # Drag continues through a snapshot received while the mouse is
                # down, and the release position survives the next snapshot.
                before = target.get_attribute("style")
                box = target.bounding_box()
                x, y = box["x"] + box["width"] / 2, box["y"] + 25
                page.mouse.move(x, y)
                page.mouse.down()
                page.mouse.move(x + 70, y + 40, steps=12)
                expect(target).not_to_have_attribute("style", before)
                halfway = target.get_attribute("style")
                refresh()
                expect(target).to_have_attribute("style", halfway)
                page.mouse.move(x + 150, y + 85, steps=12)
                expect(target).not_to_have_attribute("style", halfway)
                moved = target.get_attribute("style")
                page.mouse.up()
                expect(target).to_have_attribute("style", moved)
                refresh()
                expect(target).to_have_attribute("style", moved)
                # Left-button background drag and unmodified two-finger wheel
                # motion pan; only an explicit Control+wheel gesture zooms.
                point = blank_point()
                before_pan = transform()
                page.mouse.move(point["x"], point["y"])
                page.mouse.down()
                page.mouse.move(point["x"] + 65, point["y"] + 30, steps=10)
                page.mouse.up()
                settle_viewport()
                after_pan = transform()
                assert after_pan["zoom"] == before_pan["zoom"] and after_pan != before_pan, (before_pan, after_pan)
                point = blank_point()
                page.mouse.move(point["x"], point["y"])
                page.mouse.wheel(90, 130)
                settle_viewport()
                after_wheel = transform()
                assert after_wheel["zoom"] == after_pan["zoom"] and after_wheel != after_pan, (after_pan, after_wheel)
                page.keyboard.down("Control")
                page.mouse.wheel(0, -150)
                page.keyboard.up("Control")
                settle_viewport()
                assert transform()["zoom"] > after_wheel["zoom"], (after_wheel, transform())
                assert_no_overflow()
                page.locator("#search").fill("Coordinator")
                expect(nodes).to_have_count(1)
                expect(graph.locator(".svelte-flow__edge")).to_have_count(0)
                page.locator("#search").fill("")
                expect(nodes).to_have_count(21)
                page.locator("#graph-filter-toggle").click()
                expect(page.locator("#graph-filter-panel")).to_be_visible()
                page.locator('#status-filters [data-status="queueable"]').click()
                expect(nodes).to_have_count(10)
                page.locator('#status-filters [data-status="all"]').click()
                expect(nodes).to_have_count(21)
                page.keyboard.press("Escape")
                expect(page.locator("#graph-filter-panel")).to_be_hidden()
                graph.get_by_role("button", name="Fit", exact=True).click()
                settle_viewport()
                if os.environ.get("COMM_GRAPH_SCREENSHOT"):
                    page.screenshot(path=os.environ["COMM_GRAPH_SCREENSHOT"], full_page=True)
                page.set_viewport_size({"width": 390, "height": 844})
                expect(graph).to_be_visible()
                assert_no_overflow()
                page.set_viewport_size({"width": 1500, "height": 1150})
                graph.get_by_role("button", name="Fit", exact=True).click()
                settle_viewport()
                # Removing the selected agent during a held drag must cancel
                # it immediately, including incident edges and detail contents.
                target.click()
                expect(graph.locator(".cg-inspector")).to_contain_text("Coordinator")
                box = target.bounding_box()
                x, y = box["x"] + box["width"] / 2, box["y"] + 25
                page.mouse.move(x, y)
                page.mouse.down()
                page.mouse.move(x + 25, y + 20, steps=5)
                call(publisher["token"], "leave", agent=agents[0], bus="qit-wilde")
                refresh()
                expect(nodes).to_have_count(20)
                expect(graph.locator(".svelte-flow__edge")).to_have_count(0)
                expect(graph.locator(".cg-inspector")).to_have_count(0)
                page.mouse.move(x + 50, y + 40, steps=5)
                page.mouse.up()
                refresh()
                expect(nodes).to_have_count(20)
                expect(target).to_have_count(0)
                # Revocation also clears a second in-progress drag and cannot
                # let its pending refresh resurrect the old authorized view.
                target = nodes.first
                box = target.bounding_box()
                x, y = box["x"] + box["width"] / 2, box["y"] + 25
                page.mouse.move(x, y)
                page.mouse.down()
                page.mouse.move(x + 25, y + 20, steps=5)
                refresh()
                call(admin, "revoke", principal=reader["principal"])
                refresh()
                expect(page.locator("#app")).to_be_hidden()
                expect(nodes).to_have_count(0)
                page.mouse.move(x + 50, y + 40, steps=5)
                page.mouse.up()
                expect(nodes).to_have_count(0)
                assert set(operations) == {"snapshot"}, operations
                assert not errors, errors
                browser.close()
                print("PASS: standalone graph — direct route/reload, full viewport, overlay inspector, drag through refresh, pan/wheel/zoom, filters, mobile, mid-drag removal/revocation, zero command dispatch")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    main()
