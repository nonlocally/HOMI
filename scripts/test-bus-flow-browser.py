#!/usr/bin/env python3
"""Shipped Flow widget: hierarchy, real traffic, controls, and async scope safety.

Run: uv run --with playwright python scripts/test-bus-flow-browser.py
No live agents or network requests. COMM_FLOW_SCREENSHOT captures the fixture.
"""
import copy
import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]


def fixture():
    ids = ["root", "a-org", "a1", "a2", "a3", "b-org", "b1", "b2", "b3",
           "c-org", "c1", "c2", "c3", "deep", "isolate"]
    agents = [{"id": id, "name": "Conductor" if id == "root" else "Repeated name" if id in ("a2", "b2") else
               "<img src=x onerror=alert(1)>" if id == "deep" else id,
               "kind": "codex", "user": "aadarwal" if id < "c" else "peer", "device": "lab-mini",
               "device_id": "device-one", "status": "queueable", "buses": ["qit-wilde"]} for id in ids]
    edges = []

    def connect(a, b, count):
        edges.extend([{"source": a, "target": b, "message_count": count},
                      {"source": b, "target": a, "message_count": count + 1}])

    for id in ids[1:-1]:
        connect("root", id, 2)
    for group in "abc":
        for index in range(1, 4):
            connect(f"{group}-org", f"{group}{index}", 20)
    connect("c1", "deep", 10)
    edges.append({"source": "a1", "target": "a1", "message_count": 3})
    return {"scope": "fixture:qit-wilde", "standalone": True, "agents": agents,
            "buses": [{"name": "qit-wilde", "agents": agents,
                       "graph": {"basis": "retained_bus_messages", "edges": edges}}]}


def main():
    data = fixture()
    total = sum(e["message_count"] for e in data["buses"][0]["graph"]["edges"])
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1150})
        errors, requests = [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/*", lambda route: (requests.append(route.request.url), route.abort()))
        page.set_content('<html><head><meta name="viewport" content="width=device-width,initial-scale=1"><style>html,body,#graph{width:100%;height:100%;margin:0;overflow:hidden;background:#090909;font-family:Arial,sans-serif}</style></head><body><main id="graph"></main></body></html>')
        page.add_style_tag(content=(ROOT / "lib/assets/bus-graph.css").read_text())
        page.add_script_tag(content=(ROOT / "lib/assets/bus-graph.js").read_text())
        page.evaluate("s=>{window.graph=CommunicateGraph.mount(document.getElementById('graph'));window.graph.update(s)}", data)
        canvas = page.locator(".cg-canvas")
        nodes = page.locator(".svelte-flow__node-agent")
        layout = page.get_by_role("combobox", name="Graph layout", exact=True)
        direction = page.get_by_role("combobox", name="Flow direction", exact=True)

        def ready():
            expect(canvas).to_have_attribute("data-flow-pending", "false", timeout=15000)
            page.wait_for_timeout(250)  # Explicit fit animation is 180ms.

        def node(id):
            return page.locator(f'.svelte-flow__node-agent[data-id="agent:{id}"]')

        def positions():
            return nodes.evaluate_all("""es => Object.fromEntries(es.map(e => {
                const m = new DOMMatrix(getComputedStyle(e).transform);
                return [e.dataset.id.slice(6), {x:m.m41,y:m.m42,w:e.offsetWidth,h:e.offsetHeight}];
            }))""")

        def same(a, b):
            return a.keys() == b.keys() and all(abs(a[id][key] - b[id][key]) < .02 for id in a for key in a[id])

        def no_overlap():
            boxes = list(positions().values())
            for i, a in enumerate(boxes):
                for b in boxes[i + 1:]:
                    assert min(a['x']+a['w'],b['x']+b['w']) <= max(a['x'],b['x']) + .1 or min(a['y']+a['h'],b['y']+b['h']) <= max(a['y'],b['y']) + .1, (a,b)

        def counts(expected):
            page.wait_for_function("""expected => [...document.querySelectorAll('.svelte-flow__edge')]
                .reduce((s,e)=>s+parseInt(e.getAttribute('aria-label')),0) +
                [...document.querySelectorAll('[data-internal-messages]')]
                .reduce((s,e)=>s+Number(e.dataset.internalMessages),0) === expected""", arg=expected)

        def close_details():
            close = page.get_by_role("button", name="Close agent details", exact=True)
            if close.is_visible():
                close.click()

        def choose(id):
            close_details()
            node(id).locator(".cg-node-name").click()
            inspector = page.get_by_role("complementary", name="Selected agent details", exact=True)
            name = next(agent['name'] for agent in data['agents'] if agent['id'] == id)
            expect(inspector.locator('h3')).to_have_text(name)
            return inspector

        expect(nodes).to_have_count(15)
        ready()
        expect(layout).to_have_value("flow")
        expect(direction).to_have_value("RIGHT")
        expect(node("root").locator("[data-flow-root=true]")).to_be_visible()
        assert page.locator("[data-conductor=true]").count() == 0
        initial = positions()
        for group in "abc":
            assert initial[f'{group}-org']['x'] > initial['root']['x'] + 250
            for index in range(1, 4):
                assert initial[f'{group}{index}']['x'] > initial[f'{group}-org']['x'] + 250
        assert initial['deep']['x'] > initial['c1']['x'] + 250
        no_overlap()
        counts(total)
        assert page.locator("img").count() == 0
        paths = page.locator(".svelte-flow__edge-path").evaluate_all("es=>es.map(e=>e.getAttribute('d'))")
        assert len(paths) == len(data['buses'][0]['graph']['edges'])
        assert all(path and "NaN" not in path and "undefined" not in path for path in paths)

        direction.select_option("DOWN")
        ready()
        down = positions()
        assert down['a-org']['y'] > down['root']['y'] + 120
        assert down['a1']['y'] > down['a-org']['y'] + 120
        no_overlap()
        counts(total)
        direction.select_option("RIGHT")
        ready()
        assert same(positions(), initial)

        # Correct one branch using a real shortcut edge, then return to inferred parent.
        inspector = choose("a1")
        parent = inspector.get_by_role("combobox", name="Layout parent", exact=True)
        parent.select_option("root")
        ready()
        assert abs(positions()['a1']['x'] - positions()['a-org']['x']) < .1
        expect(parent).to_have_value("root")
        parent.select_option("")
        ready()
        assert positions()['a1']['x'] > positions()['a-org']['x'] + 250
        inspector = choose("a-org")
        expect(inspector.locator('select[aria-label="Layout parent"] option[value="a1"]')).to_have_js_property('disabled', True)
        inspector.get_by_role("button", name="Use as flow root", exact=True).click()
        ready()
        expect(node("a-org").locator("[data-flow-root=true]")).to_be_visible()
        expect(inspector.get_by_role("combobox", name="Layout parent")).to_be_disabled()
        inspector.get_by_role("button", name="Use suggested flow root", exact=True).click()
        ready()
        expect(node("root").locator("[data-flow-root=true]")).to_be_visible()
        close_details()

        # All collapsed communities retain the root and every directed count.
        page.get_by_role("button", name="Communities", exact=True).click()
        labels = page.locator('button[aria-label^="Collapse Group "]').evaluate_all("es=>es.filter(e=>!e.disabled).map(e=>e.getAttribute('aria-label'))")
        for label in labels:
            page.get_by_role("button", name=label, exact=True).click()
        counts(total)
        expect(node("root")).to_be_visible()
        for label in labels:
            page.get_by_role("button", name=label.replace("Collapse", "Expand"), exact=True).click()
        page.get_by_role("button", name="Close communities", exact=True).click()
        expect(nodes).to_have_count(15)
        counts(total)

        # Pins and real dragging remain authoritative across an explicit reflow.
        inspector = choose("a1")
        inspector.get_by_role("button", name="Pin agent", exact=True).click()
        before = positions()['a1']
        box = node('a1').bounding_box()
        page.mouse.move(box['x'] + 25, box['y'] + 25)
        page.mouse.down()
        page.mouse.move(box['x'] + 90, box['y'] + 60, steps=8)
        page.mouse.up()
        moved = positions()['a1']
        assert abs(moved['x'] - before['x']) > 10
        page.get_by_role("button", name="Recompute", exact=True).click()
        ready()
        assert same({'a1':positions()['a1']},{'a1':moved})
        close_details()
        before = positions()
        changed = copy.deepcopy(data)
        changed['buses'][0]['graph']['edges'][0]['message_count'] += 7
        page.evaluate("s=>window.graph.update(s)", changed)
        counts(total+7)
        assert same(before, positions())
        expect(page.locator('.cg-stale-note')).to_be_visible()

        # A pending job may not restore removed identities or an old scope.
        page.get_by_role("button", name="Recompute", exact=True).click()
        reduced = copy.deepcopy(changed)
        reduced['agents'] = [a for a in reduced['agents'] if a['id'] != 'a1']
        reduced['buses'][0]['agents'] = reduced['agents']
        page.evaluate("s=>window.graph.update(s)", reduced)
        ready()
        expect(node('a1')).to_have_count(0)
        assert 'a1' not in page.locator('.cg-conductor-summary').inner_text()
        page.evaluate("s=>{window.graph.update(s);window.graph.clear()}", data)
        page.wait_for_timeout(700)
        expect(page.locator('.svelte-flow__node')).to_have_count(0)
        expect(page.locator('.svelte-flow__edge')).to_have_count(0)
        page.evaluate("s=>{window.graph.update(s);window.graph.update({...s,scope:'other',agents:[s.agents.at(-1)],buses:[]})}", data)
        ready()
        expect(nodes).to_have_count(1)
        expect(node('isolate')).to_be_visible()
        page.evaluate("s=>window.graph.update(s)", data)
        ready()
        # An async Flow job cannot overwrite the subsequently selected layout.
        direction.select_option('DOWN')
        layout.select_option('devices')
        ready()
        expect(layout).to_have_value('devices')
        expect(page.locator('.svelte-flow__node-device')).to_have_count(2)
        layout.select_option('flow')
        ready()
        for value in ['RIGHT', 'DOWN', 'RIGHT']:
            direction.select_option(value)
        ready()
        expect(direction).to_have_value('RIGHT')
        assert positions()['a1']['x'] > positions()['a-org']['x']

        # Signing out after leaving Flow must clear that mode's retained state too.
        layout.select_option('spectral')
        page.evaluate('()=>window.graph.clear()')
        expect(page.locator('.svelte-flow__node')).to_have_count(0)
        expect(page.locator('.svelte-flow__edge')).to_have_count(0)
        page.evaluate('s=>window.graph.update(s)', data)
        layout.select_option('flow')
        ready()
        expect(nodes).to_have_count(15)
        counts(total)

        if os.environ.get('COMM_FLOW_SCREENSHOT'):
            page.screenshot(path=os.environ['COMM_FLOW_SCREENSHOT'])
        page.set_viewport_size({'width':390,'height':844})
        page.get_by_role('button',name='Fit',exact=True).click()
        ready()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        for control in [layout, direction, page.get_by_role('button',name='Recompute',exact=True)]:
            box=control.bounding_box()
            assert box['x'] >= 0 and box['x']+box['width'] <= 391, box
        counts(total)
        assert not errors, errors
        assert not requests, requests
        browser.close()
    print('PASS: Flow branches, directions, parent/root choices, collapse/counts, drag/pins, stale polling, pending-job removal/scope/clear/layout races, mobile, zero requests')


if __name__ == '__main__':
    main()
