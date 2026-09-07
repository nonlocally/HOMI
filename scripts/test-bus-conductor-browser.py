#!/usr/bin/env python3
"""Headless view-only conductor controls against the shipped widget; no API calls.

Run: uv run --with playwright python scripts/test-bus-conductor-browser.py
"""
import asyncio
import copy
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]


def fixture():
    agents = [{"id": f"{group}{index}", "name": "Conductor by name only" if group == "c" and index == 2 else f"{group.upper()} researcher {index}",
               "kind": "codex" if index % 2 == 0 else "claude", "user": "viewer", "device": "lab-mini",
               "device_id": "device-one", "status": "queueable", "buses": ["qit-wilde"]}
              for group in "abc" for index in range(3)]
    edges = [{"source": f"{group}{i}", "target": f"{group}{j}", "message_count": 10}
             for group in "abc" for i in range(3) for j in range(3) if i != j]
    edges += [{"source": "a0", "target": "b0", "message_count": 1}, {"source": "c0", "target": "a0", "message_count": 2}]
    return {"scope": "qit-wilde", "standalone": True, "agents": agents,
            "buses": [{"name": "qit-wilde", "agents": agents, "graph": {"basis": "retained_bus_messages", "edges": edges}}]}


async def positions(page):
    return await page.evaluate("""() => Object.fromEntries([...document.querySelectorAll('.svelte-flow__node-agent')].map(node => {
      const m = new DOMMatrixReadOnly(node.style.transform);
      return [node.dataset.id.slice(6), {x:m.m41,y:m.m42}];
    }))""")


async def message_total(page):
    return await page.evaluate("""() => [...document.querySelectorAll('.svelte-flow__edge')].reduce((sum,node) => sum + parseInt(node.getAttribute('aria-label')),0)
      + [...document.querySelectorAll('[data-internal-messages]')].reduce((sum,node) => sum + Number(node.dataset.internalMessages),0)""")


async def settle(page):
    await page.wait_for_timeout(230)  # The explicit Fit control animates for 180 ms.


async def main():
    data = fixture()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1500, "height": 1000})
        errors, requests = [], []
        page.on("pageerror", lambda error: errors.append(str(error)))

        async def block_network(route):
            requests.append(route.request.url)
            await route.abort()

        await page.route("**/*", block_network)
        await page.set_content('<html><head><meta name="viewport" content="width=device-width,initial-scale=1"><style>html,body,#graph{width:100%;height:100%;margin:0;overflow:hidden;background:#090909;font-family:Arial,sans-serif}</style></head><body><main id="graph"></main></body></html>')
        await page.add_style_tag(content=(ROOT / "lib/assets/bus-graph.css").read_text())
        await page.add_script_tag(content=(ROOT / "lib/assets/bus-graph.js").read_text())
        await page.evaluate("s=>{window.graph=CommunicateGraph.mount(document.getElementById('graph'));window.graph.update(s)}", data)
        await page.locator(".svelte-flow__node-agent").nth(8).wait_for(state="visible")
        await page.get_by_role("combobox", name="Graph layout").select_option("spectral")
        await settle(page)
        assert await page.locator("[data-conductor=true]").count() == 0  # Never infer from a name.
        initial = await positions(page)
        node = page.locator('.svelte-flow__node-agent[data-id="agent:a0"]')
        await node.locator(".cg-node-name").click()
        await page.get_by_role("button", name="Set apart as conductor", exact=True).click()
        await page.locator("[data-conductor=true]").wait_for()
        await settle(page)
        chosen = await positions(page)
        assert chosen["a0"]["y"] + 120 <= min(pos["y"] for key, pos in chosen.items() if key != "a0") - 219.9
        for key in initial:
            if key != "a0":
                assert abs(initial[key]["x"] - chosen[key]["x"]) < .02 and abs(initial[key]["y"] - chosen[key]["y"]) < .02
        await page.wait_for_function("""() => [...document.querySelectorAll('.svelte-flow__edge')].reduce((sum,node) => sum + parseInt(node.getAttribute('aria-label')),0) + [...document.querySelectorAll('[data-internal-messages]')].reduce((sum,node) => sum + Number(node.dataset.internalMessages),0) === 183""")
        assert await message_total(page) == 183
        paths = await page.locator('.svelte-flow__edge[aria-label*="agent:a0"]').evaluate_all("es=>es.map(e=>e.querySelector('path').getAttribute('d'))")
        assert paths and all("NaN" not in path and "undefined" not in path for path in paths)
        await page.get_by_role("button", name="Close agent details", exact=True).click()
        community_label = await node.locator(".cg-node-community").inner_text()
        await page.get_by_role("button", name="Communities", exact=True).click()
        await page.get_by_role("button", name=f"Collapse {community_label}", exact=True).click()
        await page.locator(".svelte-flow__node-community").wait_for()
        assert await node.count() == 1 and await node.locator("[data-conductor=true]").count() == 1
        await page.wait_for_function("""() => [...document.querySelectorAll('.svelte-flow__edge')].reduce((sum,node) => sum + parseInt(node.getAttribute('aria-label')),0) + [...document.querySelectorAll('[data-internal-messages]')].reduce((sum,node) => sum + Number(node.dataset.internalMessages),0) === 183""")
        assert await message_total(page) == 183
        group = page.locator(".svelte-flow__node-community").first
        group_data = await group.get_attribute("aria-label")
        assert "2 agents" in group_data, group_data
        # Expanding a moved group must not translate the separated conductor.
        await page.get_by_role("button", name="Close communities", exact=True).click()
        group_bounds = await group.bounding_box()
        group_before = await group.get_attribute("style")
        await page.mouse.move(group_bounds["x"] + 75, group_bounds["y"] + 30)
        await page.mouse.down()
        await page.mouse.move(group_bounds["x"] + 130, group_bounds["y"] + 60, steps=5)
        await page.mouse.up()
        assert await group.get_attribute("style") != group_before
        await page.get_by_role("button", name="Communities", exact=True).click()
        await page.get_by_role("button", name=f"Expand {community_label}", exact=True).click()
        assert abs((await positions(page))["a0"]["y"] - chosen["a0"]["y"]) < .02
        await page.get_by_role("button", name="Close communities", exact=True).click()
        await node.locator(".cg-node-name").click()
        await page.get_by_role("button", name="Pin agent", exact=True).click()
        bounds = await node.bounding_box()
        await page.mouse.move(bounds["x"] + 80, bounds["y"] + 30)
        await page.mouse.down()
        await page.mouse.move(bounds["x"] + 145, bounds["y"] + 60, steps=5)
        await page.mouse.up()
        pinned = (await positions(page))["a0"]
        assert abs(pinned["x"] - chosen["a0"]["x"]) > 10
        await page.get_by_role("button", name="Recompute", exact=True).click()
        await settle(page)
        assert (await positions(page))["a0"] == pinned
        inspector = page.get_by_role("complementary", name="Selected agent details", exact=True)
        await inspector.get_by_role("button", name="Clear conductor placement", exact=True).click()
        await settle(page)
        assert (await positions(page))["a0"] == pinned
        assert await page.locator("[data-conductor=true]").count() == 0
        await page.get_by_role("button", name="Unpin agent", exact=True).click()
        await page.get_by_role("button", name="Set apart as conductor", exact=True).click()
        await settle(page)
        before_refresh = (await positions(page))["a0"]
        changed = copy.deepcopy(data)
        changed["buses"][0]["graph"]["edges"][0]["message_count"] += 5
        await page.evaluate("s=>window.graph.update(s)", changed)
        assert (await positions(page))["a0"] == before_refresh
        assert await page.locator("[data-conductor=true]").count() == 1
        await inspector.get_by_role("button", name="Clear conductor placement", exact=True).click()
        await settle(page)
        assert (await positions(page))["a0"] == pinned  # Its ordinary pre-separation home.
        await page.get_by_role("button", name="Set apart as conductor", exact=True).click()
        await settle(page)
        reassigned = copy.deepcopy(changed)
        reassigned["agents"][0]["device_id"] = "device-two"
        await page.evaluate("s=>window.graph.update(s)", reassigned)
        assert await page.locator("[data-conductor=true]").count() == 0
        assert await page.locator(".cg-conductor-summary").count() == 0
        await page.get_by_role("button", name="Set apart as conductor", exact=True).click()
        await settle(page)
        reduced = copy.deepcopy(reassigned)
        reduced["agents"] = [agent for agent in reduced["agents"] if agent["id"] != "a0"]
        reduced["buses"][0]["agents"] = reduced["agents"]
        await page.evaluate("s=>window.graph.update(s)", reduced)
        assert await page.locator("[data-conductor=true]").count() == 0
        assert await page.locator(".cg-conductor-summary").count() == 0
        assert await page.locator(".cg-inspector").count() == 0
        await page.evaluate("s=>window.graph.update(s)", data)
        await node.locator(".cg-node-name").click()
        await page.get_by_role("button", name="Set apart as conductor", exact=True).click()
        await settle(page)
        await page.evaluate("s=>window.graph.update({...s,scope:'other-bus'})", data)
        assert await page.locator("[data-conductor=true]").count() == 0
        await node.locator(".cg-node-name").click()
        await page.get_by_role("button", name="Set apart as conductor", exact=True).click()
        await page.evaluate("()=>window.graph.clear()")
        assert await page.locator(".svelte-flow__node").count() == 0
        assert await page.locator(".cg-conductor-summary").count() == 0
        assert not errors, errors
        assert not requests, requests
        await browser.close()
    print("PASS: explicit conductor placement, collapse/directed totals, vertical paths, drag/pin/recompute, clear/home, polling, identity/scope removal; zero requests")


if __name__ == "__main__":
    asyncio.run(main())
