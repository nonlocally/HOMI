import test from 'node:test';
import assert from 'node:assert/strict';
import { routeFlowEdges, clearFlowRoutingCache, FLOW_ROUTING_LIMIT } from './flow-routing.mjs';

const node = (id, x, y, extra = {}) => ({id, type: 'agent', position: {x, y}, width: 250, height: 120, data: {}, ...extra});
const edge = (source, target, count = 7) => ({id: `${source}->${target}`, source, target, label: String(count), data: {messageCount: count}, ariaLabel: `${count} retained messages from ${source} to ${target}`, markerEnd: {type: 'arrowclosed'}});

function crosses(a, b, n) {
  const left = n.position.x, right = left + (n.measured?.width || n.width), top = n.position.y, bottom = top + (n.measured?.height || n.height);
  if (a.x === b.x) return a.x > left + 1e-6 && a.x < right - 1e-6 && Math.max(a.y, b.y) > top + 1e-6 && Math.min(a.y, b.y) < bottom - 1e-6;
  if (a.y === b.y) return a.y > top + 1e-6 && a.y < bottom - 1e-6 && Math.max(a.x, b.x) > left + 1e-6 && Math.min(a.x, b.x) < right - 1e-6;
  assert.fail('Every segment must be orthogonal');
}

function assertClear(nodes, routes) {
  for (const edge of routes) {
    assert.equal(edge.data.flowRoute.warning, null);
    const points = edge.data.flowRoute.points;
    assert.ok(points.length >= 2);
    for (const p of points) assert.ok(Number.isFinite(p.x) && Number.isFinite(p.y));
    for (let i = 1; i < points.length; i++) for (const n of nodes) {
      assert.equal(crosses(points[i - 1], points[i], n), false, `${edge.id} crosses ${n.id} on segment ${i}`);
    }
  }
}

test('Flow keeps all directed connections, numeric counts, markers and aria labels', () => {
  const nodes = [node('a', 0, 0), node('b', 600, 0), node('c', 1200, 200)];
  const edges = [edge('a', 'b'), edge('b', 'a', 11), edge('a', 'c', 3), edge('b', 'c', 19)];
  const routed = routeFlowEdges(nodes, edges);
  assert.equal(routed.length, edges.length);
  for (let i = 0; i < edges.length; i++) {
    const got = routed[i], original = edges[i];
    for (const key of ['id', 'source', 'target', 'label', 'ariaLabel', 'markerEnd']) assert.deepEqual(got[key], original[key]);
    assert.equal(got.data.messageCount, original.data.messageCount);
    assert.equal(got.type, 'flow');
  }
  assertClear(nodes, routed);
});

test('a shortcut routes around intervening cards in both orientations', () => {
  const right = [node('a', 0, 0), node('obstacle', 450, -70, {height: 260}), node('b', 1000, 0)];
  assertClear(right, routeFlowEdges(right, [edge('a', 'b')], {direction: 'RIGHT'}));
  const down = [node('a', 0, 0), node('obstacle', -70, 300, {width: 400}), node('b', 0, 750)];
  const routed = routeFlowEdges(down, [edge('a', 'b')], {direction: 'DOWN'});
  assertClear(down, routed);
  assert.equal(routed[0].sourceHandle, 'out-bottom');
  assert.equal(routed[0].targetHandle, 'in-top');
});

test('visibility grid finds a multi-turn route through blocked dogleg corridors', () => {
  const nodes = [node('a', 0, 250), node('b', 1100, 250),
    node('middle', 350, 180, {height: 280}),
    node('upper-left', 150, -100, {width: 400, height: 160}),
    node('lower-left', 150, 500, {width: 400, height: 160}),
    node('upper-right', 750, -100, {width: 500, height: 160}),
    node('lower-right', 750, 500, {width: 500, height: 160})];
  const routed = routeFlowEdges(nodes, [edge('a', 'b')]);
  assertClear(nodes, routed);
  assert.ok(routed[0].data.flowRoute.points.length >= 5);
});

test('reciprocal messages have distinct lanes, labels, ports and real arrow directions', () => {
  for (const direction of ['RIGHT', 'DOWN']) {
    const nodes = [node('a', 0, 0), node('b', direction === 'RIGHT' ? 800 : 0, direction === 'DOWN' ? 600 : 0)];
    const [forward, reverse] = routeFlowEdges(nodes, [edge('a', 'b'), edge('b', 'a')], {direction});
    assertClear(nodes, [forward, reverse]);
    assert.notEqual(forward.data.flowRoute.path, reverse.data.flowRoute.path);
    assert.notDeepEqual([forward.data.flowRoute.labelX, forward.data.flowRoute.labelY], [reverse.data.flowRoute.labelX, reverse.data.flowRoute.labelY]);
    const forwardStart = forward.data.flowRoute.points[0], reverseEnd = reverse.data.flowRoute.points.at(-1);
    assert.deepEqual(forwardStart, reverseEnd);
    assert.equal(reverse.sourceHandle, direction === 'DOWN' ? 'out-top' : 'out-left');
    assert.equal(reverse.targetHandle, direction === 'DOWN' ? 'in-bottom' : 'in-right');
  }
});

test('fan-out message counts occupy distinct clear rectangles along shared corridors', () => {
  for (const direction of ['RIGHT', 'DOWN']) {
    const nodes = [node('hub', 0, 540), ...Array.from({length: 7}, (_, i) => node(`b${i}`, 800, i * 180))];
    if (direction === 'DOWN') for (const n of nodes) [n.position.x, n.position.y] = [n.position.y * 2, n.position.x];
    const edges = nodes.slice(1).flatMap(n => [edge('hub', n.id, 19), edge(n.id, 'hub', 91)]);
    const routed = routeFlowEdges(nodes, edges, {direction});
    assertClear(nodes, routed);
    const labels = routed.map(e => {
      const {labelX: x, labelY: y} = e.data.flowRoute;
      return {left: x - 12, right: x + 12, top: y - 10, bottom: y + 10};
    });
    const overlap = (a, b) => a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;
    for (let i = 0; i < labels.length; i++) {
      for (let j = i + 1; j < labels.length; j++) assert.equal(overlap(labels[i], labels[j]), false, `count labels ${i} and ${j} overlap`);
      for (const n of nodes) assert.equal(overlap(labels[i], {left: n.position.x, right: n.position.x + n.width, top: n.position.y, bottom: n.position.y + n.height}), false, `count label ${i} overlaps ${n.id}`);
    }
    assert.equal(routed.reduce((sum, e) => sum + Number(e.label), 0), 770);
  }
});

test('self loops leave and return to different ports without passing through their card', () => {
  for (const direction of ['RIGHT', 'DOWN']) {
    const nodes = [node('a', 150, 170), node('nearby', 550, 170)];
    const routed = routeFlowEdges(nodes, [edge('a', 'a', 23)], {direction});
    assertClear(nodes, routed);
    assert.equal(routed[0].label, '23');
    assert.notDeepEqual(routed[0].data.flowRoute.points[0], routed[0].data.flowRoute.points.at(-1));
  }
});

test('dragged/pinned endpoints and other moved obstacles invalidate cached routes', () => {
  const nodes = [node('a', 0, 0), node('b', 800, 0), node('obstacle', 400, 400)];
  const original = routeFlowEdges(nodes, [edge('a', 'b')])[0];
  const moved = nodes.map(n => n.id === 'obstacle' ? {...n, position: {x: 400, y: 0}} : n);
  const updated = routeFlowEdges(moved, [edge('a', 'b')])[0];
  assert.notEqual(updated.data.flowRoute.path, original.data.flowRoute.path);
  assertClear(moved, [updated]);
  const endMoved = moved.map(n => n.id === 'b' ? {...n, position: {x: 850, y: 300}} : n);
  const final = routeFlowEdges(endMoved, [edge('a', 'b')])[0];
  assert.notEqual(final.data.flowRoute.path, updated.data.flowRoute.path);
  assert.deepEqual(final.data.flowRoute.points.at(-1), {x: 850, y: 360});
  assertClear(endMoved, [final]);
});

test('collapsed communities route using the actual aggregate rectangle and measured size', () => {
  const nodes = [node('a', 0, 0), node('group', 700, 50, {type: 'community', measured: {width: 290, height: 140}})];
  const edges = [edge('a', 'group', 117), edge('group', 'a', 99)];
  const routed = routeFlowEdges(nodes, edges);
  assertClear(nodes, routed);
  assert.deepEqual(routed[0].data.flowRoute.points.at(-1), {x: 700, y: 120});
  assert.equal(routed.reduce((sum, e) => sum + Number(e.label), 0), 216);
});

test('geometry is deterministic under edge/node reordering, while decoration can change', () => {
  const nodes = [node('a', 0, 0), node('b', 600, 150), node('c', 1200, -40)];
  const edges = [edge('a', 'b'), edge('b', 'a'), edge('c', 'a')];
  const first = new Map(routeFlowEdges(nodes, edges).map(e => [e.id, e.data.flowRoute]));
  const next = routeFlowEdges([...nodes].reverse(), [...edges].reverse().map(e => ({...e, label: '42', style: 'opacity:0.2'})));
  for (const edge of next) { assert.deepEqual(edge.data.flowRoute, first.get(edge.id)); assert.equal(edge.label, '42'); assert.equal(edge.style, 'opacity:0.2'); }
});

test('overlapping cards retain edges with explicit finite fallback routes', () => {
  const nodes = [node('a', 0, 0), node('b', 50, 0)];
  const routed = routeFlowEdges(nodes, [edge('a', 'b')]);
  assert.equal(routed.length, 1);
  assert.match(routed[0].data.flowRoute.warning, /may cross cards/);
  assert.ok(routed[0].data.flowRoute.points.every(p => Number.isFinite(p.x) && Number.isFinite(p.y)));
});

test('large drawings retain every edge and label with an explicit bounded routing notice', () => {
  const nodes = Array.from({length: FLOW_ROUTING_LIMIT.nodes + 1}, (_, i) => node(`a${i}`, i * 350, 0));
  const edges = nodes.slice(1).map(n => edge(nodes[0].id, n.id));
  const routed = routeFlowEdges(nodes, edges);
  assert.equal(routed.length, edges.length);
  for (const e of routed) { assert.equal(e.label, '7'); assert.match(e.data.flowRoute.warning, /bounded corridor|may cross cards/); assert.ok(e.data.flowRoute.points.every(p => Number.isFinite(p.x) && Number.isFinite(p.y))); }
});

test('dense fallback bounds obstacle work and keeps every directed connection', () => {
  const nodes = Array.from({length: 128}, (_, i) => node(`n${i}`, i % 16 * 350, Math.floor(i / 16) * 200));
  const edges = nodes.flatMap((n, i) => Array.from({length: 17}, (_, j) => edge(n.id, nodes[(i + j + 1) % nodes.length].id, 3)));
  assert.ok(nodes.length * edges.length > FLOW_ROUTING_LIMIT.corridorWork);
  const routed = routeFlowEdges(nodes, edges);
  assert.equal(routed.length, edges.length);
  assert.equal(routed.reduce((sum, e) => sum + Number(e.label), 0), edges.length * 3);
  for (const e of routed) {
    assert.match(e.data.flowRoute.warning, /may cross cards/);
    assert.ok(e.data.flowRoute.points.every(p => Number.isFinite(p.x) && Number.isFinite(p.y)));
  }
});

test('empty or removed snapshots do not keep old routes or hidden endpoint data', () => {
  const nodes = [node('a', 0, 0), node('private-old', 500, 0)];
  routeFlowEdges(nodes, [edge('a', 'private-old')]);
  assert.deepEqual(routeFlowEdges([], []), []);
  const next = routeFlowEdges([node('a', 0, 0), node('b', 700, 0)], [edge('a', 'b')]);
  assert.equal(JSON.stringify(next).includes('private-old'), false);
});

test('explicit scope clearing releases cached geometry without requiring another Flow render', () => {
  const nodes = [node('private-a', 0, 0), node('private-b', 700, 0)];
  const edges = [edge('private-a', 'private-b')];
  const first = routeFlowEdges(nodes, edges)[0].data.flowRoute.points;
  assert.strictEqual(routeFlowEdges(nodes, edges)[0].data.flowRoute.points, first);
  clearFlowRoutingCache();
  clearFlowRoutingCache(); // Clearing a destroyed or already empty scope is safe.
  const renewed = routeFlowEdges(nodes, edges)[0].data.flowRoute.points;
  assert.notStrictEqual(renewed, first, 'Old private route objects must not survive in the cache');
  assert.deepEqual(renewed, first, 'A later authorized layout still computes the same drawing');
});
