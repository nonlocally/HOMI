#!/usr/bin/env python3
"""Spectral/community controls against a temporary broker; no live agents.

Run: uv run --with playwright python scripts/test-bus-spectral-browser.py
COMM_SPECTRAL_SCREENSHOT optionally records the fixture canvas.
"""
import json
import os
from pathlib import Path
import re
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
    with tempfile.TemporaryDirectory(prefix="communicate-spectral-browser-") as temp:
        broker = Broker(temp)

        def call(token, op, **fields):
            result = broker.handle(token, {"op": op, **fields})
            assert result["ok"], result
            return result

        admin = broker.admin_token
        call(admin, "create", bus="qit-wilde")
        call(admin, "create", bus="private-bus-sentinel")
        call(admin, "register", session_key="private", name="private-agent-sentinel", bus="private-bus-sentinel")

        def device(name, user):
            invitation = call(admin, "invite", bus="qit-wilde", user=user)
            return call(None, "redeem", invite=invitation["invite"], device=name,
                        device_metadata={"hostname": name, "platform": "fixture"})

        publishers = [device("lab-mini", "aadarwal"), device("research-laptop", "peer")]
        reader = device("view-only-fixture", "reader")
        names = ["A organizer", "Repeated name", "A proof", "B organizer", "Repeated name",
                 "B proof", "C organizer", "<img src=x onerror=alert(1)>", "C proof", "Isolated agent"]
        agents, tokens = [], []
        for index, name in enumerate(names):
            token = publishers[int(index >= 5)]["token"]
            agents.append(call(token, "register", bus="qit-wilde", session_key=f"spectral:{index}",
                               name=name, kind="codex" if index % 2 else "claude", status="queueable")["id"])
            tokens.append(token)

        def send(source, target):
            return call(tokens[source], "send", bus="qit-wilde", sender=agents[source], target=agents[target],
                        message="private-message-sentinel")

        for group in ([0, 1, 2], [3, 4, 5], [6, 7, 8]):
            for source in group:
                for target in group:
                    if source != target:
                        for _ in range(3):
                            send(source, target)
        send(0, 3)
        send(3, 6)
        server = BusHTTPServer(("127.0.0.1", 0), handler_factory(broker))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1500, "height": 1050})
                errors, operations = [], []
                page.on("pageerror", lambda error: errors.append(str(error)))

                def request_seen(request):
                    if request.method == "POST" and request.url.endswith("/v1"):
                        operations.append(json.loads(request.post_data)["op"])

                page.on("request", request_seen)
                origin = f"http://127.0.0.1:{server.server_port}"
                page.goto(f"{origin}/graph?bus=qit-wilde#token={reader['token']}")
                graph = page.locator("#graph-panel")
                expect(graph).to_be_visible()
                nodes = graph.locator(".svelte-flow__node-agent")
                expect(nodes).to_have_count(10)
                expect(graph.locator(".svelte-flow__edge")).to_have_count(20)

                def refresh():
                    with page.expect_response(lambda response: response.url.endswith('/v1')) as pending:
                        page.locator("#graph-refresh").click()
                    pending.value.body()
                    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")

                def world_positions():
                    return nodes.evaluate_all("""elements => Object.fromEntries(elements.map(el => {
                        const m = new DOMMatrix(getComputedStyle(el).transform);
                        return [el.dataset.id, {x:m.m41,y:m.m42,width:el.offsetWidth,height:el.offsetHeight}];
                    }))""")

                def same_positions(a, b):
                    # DOMMatrix serializes browser floats at different precision
                    # after a node is removed/reinserted. Compare screen geometry,
                    # not insignificant differences below one hundredth pixel.
                    return a.keys() == b.keys() and all(
                        abs(a[node][key] - b[node][key]) < 0.01
                        for node in a for key in ('x', 'y', 'width', 'height'))

                def no_overlap():
                    boxes = list(world_positions().values())
                    for i, a in enumerate(boxes):
                        for b in boxes[i + 1:]:
                            dx = min(a['x'] + a['width'], b['x'] + b['width']) - max(a['x'], b['x'])
                            dy = min(a['y'] + a['height'], b['y'] + b['height']) - max(a['y'], b['y'])
                            assert dx <= 1 or dy <= 1, (a, b)

                def no_private_data():
                    text = graph.inner_text()
                    assert "private-agent-sentinel" not in text and "private-bus-sentinel" not in text
                    assert "private-message-sentinel" not in text
                    assert graph.locator("img").count() == 0

                no_overlap()
                no_private_data()
                layout = graph.get_by_role("combobox", name="Graph layout")
                expect(layout).to_have_value("spectral")
                canvas = graph.locator(".cg-canvas")
                before_canvas = canvas.bounding_box()
                before = world_positions()
                graph.get_by_role("button", name="Analysis", exact=True).click()
                analysis = graph.get_by_role("complementary", name="Graph analysis", exact=True)
                expect(analysis).to_be_visible()
                expect(analysis).to_contain_text("Leiden")
                expect(analysis).to_contain_text("Laplacian")
                assert float(analysis.locator('[data-analysis="modularity"]').inner_text()) > 0.3
                expect(analysis.locator(".cg-component")).to_have_count(2)
                analysis.locator(".cg-component summary").first.click()
                spectrum = analysis.locator('[data-analysis="eigenvalues"]').first.inner_text()
                assert "NaN" not in spectrum and len(spectrum.split(',')) == 8, spectrum
                assert canvas.bounding_box() == before_canvas and same_positions(world_positions(), before)
                graph.get_by_role("button", name="Close analysis", exact=True).click()
                layout.select_option("devices")
                expect(graph.locator(".svelte-flow__node-device")).to_have_count(2)
                assert world_positions() != before
                layout.select_option("spectral")
                expect(graph.locator(".svelte-flow__node-device")).to_have_count(0)
                page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
                assert same_positions(world_positions(), before), (before, world_positions())

                def community_panel():
                    panel = graph.get_by_role("complementary", name="Communities", exact=True)
                    if not panel.is_visible():
                        graph.get_by_role("button", name="Communities", exact=True).click()
                    return panel

                def collapse_all():
                    panel = community_panel()
                    labels = panel.get_by_role("button", name=re.compile(r"^Collapse Group ")).evaluate_all(
                        "els => els.map(el => el.getAttribute('aria-label'))")
                    for label in labels:
                        button = panel.get_by_role("button", name=label, exact=True)
                        if button.is_enabled():
                            button.click()
                    graph.get_by_role("button", name="Close communities", exact=True).click()

                panel = community_panel()
                expect(panel.locator(".cg-community-row")).to_have_count(4)
                panel.get_by_role("button", name="Focus Group 1", exact=True).click()
                expect(panel.locator(".cg-community-members li")).to_have_count(3)
                expect(graph.locator(".cg-agent.is-focused")).to_have_count(3)
                graph.get_by_role("button", name="Close communities", exact=True).click()
                collapse_all()
                groups = graph.locator(".svelte-flow__node-community")
                expect(groups).to_have_count(3)
                expect(nodes).to_have_count(1)
                expect(graph.locator(".svelte-flow__edge")).to_have_count(2)

                def rendered_message_total():
                    internal = graph.locator('[data-internal-messages]').evaluate_all(
                        "els => els.reduce((sum,e) => sum + Number(e.dataset.internalMessages),0)")
                    external = graph.locator('.svelte-flow__edge-label').evaluate_all(
                        "els => els.reduce((sum,e) => sum + Number(e.textContent),0)")
                    return internal + external

                def expect_message_total(expected):
                    # Newly expanded nodes are measured before Svelte Flow
                    # mounts their edge labels. Wait for visible accounting.
                    page.wait_for_function("""expected => {
                        const graph = document.getElementById('graph-panel');
                        const internal = [...graph.querySelectorAll('[data-internal-messages]')]
                            .reduce((sum, el) => sum + Number(el.dataset.internalMessages), 0);
                        const external = [...graph.querySelectorAll('.svelte-flow__edge-label')]
                            .reduce((sum, el) => sum + Number(el.textContent), 0);
                        return internal + external === expected;
                    }""", arg=expected, timeout=5000)
                    assert rendered_message_total() == expected

                expect_message_total(56)
                no_private_data()
                panel = community_panel()
                panel.get_by_role("button", name="Expand Group 1", exact=True).click()
                expect(groups).to_have_count(2)
                expect(nodes).to_have_count(4)
                graph.get_by_role("button", name="Close communities", exact=True).click()
                expect_message_total(56)
                panel = community_panel()
                panel.get_by_role("button", name="Collapse Group 1", exact=True).click()
                graph.get_by_role("button", name="Close communities", exact=True).click()
                expect(groups).to_have_count(3)
                expect(nodes).to_have_count(1)
                # Collapsed traffic updates immediately; its partition and
                # expanded member positions wait for explicit recomputation.
                before = world_positions()
                send(1, 2)
                refresh()
                assert same_positions(world_positions(), before), "traffic polling moved the layout"
                expect_message_total(57)
                expect(graph.locator(".cg-stale-note")).to_be_visible()
                panel = community_panel()
                panel.get_by_role("checkbox", name="Show community layer").uncheck()
                expect(groups).to_have_count(0)
                expect(nodes).to_have_count(10)
                expect(graph.locator(".cg-node-community")).to_have_count(0)
                panel.get_by_role("checkbox", name="Show community layer").check()
                graph.get_by_role("button", name="Close communities", exact=True).click()
                graph.get_by_role("button", name="Recompute", exact=True).click()
                expect(graph.locator(".cg-stale-note")).to_have_count(0)
                no_overlap()
                # Move and pin an agent, then verify a fresh analysis keeps its
                # deliberate position. This checks the actual pointer path.
                target = graph.locator(f'[data-id="agent:{agents[0]}"]')
                page.wait_for_timeout(250)  # Explicit 180 ms fit animation.
                box = target.bounding_box()
                x, y = box["x"] + box["width"] / 2, box["y"] + 20
                page.mouse.move(x, y)
                page.mouse.down()
                page.mouse.move(x + 70, y + 50, steps=8)
                page.mouse.up()
                moved = world_positions()[f"agent:{agents[0]}"]
                target.click()
                graph.get_by_role("button", name="Pin agent", exact=True).click()
                graph.get_by_role("button", name="Recompute", exact=True).click()
                assert same_positions({agents[0]: world_positions()[f"agent:{agents[0]}"]}, {agents[0]: moved})
                expect(graph.get_by_role("button", name="Unpin agent", exact=True)).to_be_visible()
                graph.get_by_role("button", name="Close agent details", exact=True).click()
                # Scope removal must purge hidden members as well as rendered
                # aggregate IDs (which themselves encode group membership).
                collapse_all()
                expect(groups).to_have_count(3)
                call(tokens[0], "leave", agent=agents[0], bus="qit-wilde")
                refresh()
                expect(nodes).to_have_count(9)
                expect(groups).to_have_count(0)
                assert agents[0] not in graph.inner_html()
                panel = community_panel()
                assert "A organizer" not in panel.inner_text()
                graph.get_by_role("button", name="Close communities", exact=True).click()
                graph.get_by_role("button", name="Analysis", exact=True).click()
                for summary in analysis.locator(".cg-component summary").all():
                    summary.click()
                assert "A organizer" not in analysis.inner_text() and agents[0] not in graph.inner_html()
                graph.get_by_role("button", name="Close analysis", exact=True).click()
                if os.environ.get("COMM_SPECTRAL_SCREENSHOT"):
                    page.screenshot(path=os.environ["COMM_SPECTRAL_SCREENSHOT"], full_page=True)
                for width in (390, 1500):
                    page.set_viewport_size({"width": width, "height": 844})
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1 && document.documentElement.scrollHeight <= innerHeight + 1")
                    panel = community_panel()
                    box = panel.bounding_box()
                    assert box["x"] >= 0 and box["x"] + box["width"] <= width + 1
                    graph.get_by_role("button", name="Close communities", exact=True).click()
                collapse_all()
                call(admin, "revoke", principal=reader["principal"])
                refresh()
                expect(page.locator("#app")).to_be_hidden()
                expect(nodes).to_have_count(0)
                expect(groups).to_have_count(0)
                expect(graph.locator(".cg-inspector")).to_have_count(0)
                assert not any(agent in graph.inner_html() for agent in agents)
                assert set(operations) == {"snapshot"}, operations
                assert not errors, errors
                browser.close()
                print("PASS: spectral graph — modes, Leiden groups, collapse accounting, stale analysis, pins, permissions, mobile, no commands")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    main()
