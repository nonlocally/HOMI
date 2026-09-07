import test from 'node:test';
import assert from 'node:assert/strict';
import {inferFlow, layoutFlow, validateParentOverride, parentOverrideOptions, FLOW_NODE_LIMIT} from './flow.mjs';
import {weightedGraph, CARD_WIDTH, CARD_HEIGHT} from './analysis.mjs';

const edge = (source, target, message_count = 1) => ({source, target, message_count});
const model = (ids, connections = []) => ({agents: ids.map(id => ({id, name: 'Repeated name'})), connections});
const branchModel = () => model(['root', 'A', 'A1', 'A2', 'A3', 'B', 'B1', 'B2', 'B3', 'isolate'], [
  ...['A', 'A1', 'A2', 'A3', 'B', 'B1', 'B2', 'B3'].flatMap(id => [edge('root', id, 20), edge(id, 'root', 10)]),
  ...['A1', 'A2', 'A3'].flatMap(id => [edge('A', id, 15), edge(id, 'A', 15)]),
  ...['B1', 'B2', 'B3'].map(id => edge('B', id, 25)),
]);

function assertForest(result, input) {
  const graph = weightedGraph(input);
  const pairs = new Set(graph.edges.map(edge => JSON.stringify([edge.source, edge.target])));
  assert.equal(result.parentByAgent.size + result.roots.length, graph.ids.length);
  assert.equal(result.rankByAgent.size, graph.ids.length);
  for (const {source, target, weight} of result.backbone) {
    assert.ok(pairs.has(JSON.stringify([source, target].sort())), `real relationship ${source}→${target}`);
    assert.ok(weight > 0);
    assert.equal(result.rankByAgent.get(target), result.rankByAgent.get(source) + 1);
    const visited = new Set();
    for (let id = target; id != null; id = result.parentByAgent.get(id)) {
      assert.equal(visited.has(id), false, 'visual hierarchy is acyclic');
      visited.add(id);
    }
  }
}

function assertReadable(result, ids) {
  assert.equal(result.positions.size, ids.length);
  for (let i = 0; i < ids.length; i++) {
    const a = result.positions.get(ids[i]);
    assert.ok(Number.isFinite(a.x) && Number.isFinite(a.y));
    for (let j = i + 1; j < ids.length; j++) {
      const b = result.positions.get(ids[j]);
      assert.ok(Math.abs(a.x - b.x) >= CARD_WIDTH - 1e-6 || Math.abs(a.y - b.y) >= CARD_HEIGHT - 1e-6, `overlap ${ids[i]} / ${ids[j]}`);
    }
  }
  for (const {source, target} of result.backbone) {
    const axis = result.direction === 'DOWN' ? 'y' : 'x';
    assert.ok(result.positions.get(target)[axis] > result.positions.get(source)[axis], 'parent precedes child');
  }
}

test('empty and isolated agents remain distinct without invented relationships', async () => {
  assert.equal(inferFlow().rootId, null);
  assert.equal((await layoutFlow()).positions.size, 0);
  const input = model(['z', 'a', 'root']);
  const result = await layoutFlow(input);
  assert.deepEqual(result.roots, ['a', 'root', 'z']);
  assert.equal(result.backbone.length, 0);
  assertReadable(result, input.agents.map(a => a.id));
});

test('root removal recovers organizer branches despite direct conductor traffic to every worker', () => {
  const input = branchModel(), result = inferFlow(input);
  assert.equal(result.rootId, 'root');
  assert.equal(result.automaticRoot, true);
  assert.deepEqual(result.branches.map(branch => [branch.hub, branch.members]), [
    ['A', ['A', 'A1', 'A2', 'A3']], ['B', ['B', 'B1', 'B2', 'B3']],
  ]);
  assert.equal(result.rankByAgent.get('A1'), 2);
  assert.equal(result.parentByAgent.get('B3'), 'B');
  assert.equal(input.connections.length, 25, 'source messages are not rewritten');
  assertForest(result, input);
});

test('inference has arbitrary depth and chooses strongest preceding-level relationship', () => {
  const input = model(['r', 'h', 'a', 'b', 'c', 'd', 'e'], [edge('r', 'h'), edge('h', 'a', 30), edge('h', 'b', 20), edge('a', 'c', 2), edge('b', 'c', 40), edge('c', 'd'), edge('d', 'e')]);
  const result = inferFlow(input, {conductorId: 'r'});
  assert.equal(result.rootId, 'r');
  assert.equal(result.automaticRoot, false);
  assert.equal(result.parentByAgent.get('c'), 'b');
  assert.equal(result.rankByAgent.get('e'), 5);
  assertForest(result, input);
});

test('automatic root selection uses distinct degree before volume and never agent names', () => {
  const input = model(['a', 'b', 'c', 'd', 'e'], [edge('a', 'b', 1e9), edge('b', 'c'), edge('c', 'd'), edge('c', 'e')]);
  input.agents.find(a => a.id === 'a').name = 'Conductor';
  assert.equal(inferFlow(input).rootId, 'c');
  const tie = model(['a', 'b', 'c'], [edge('a', 'b', 10), edge('a', 'c'), edge('b', 'c', 2)]);
  assert.equal(inferFlow(tie).rootId, 'b');
});

test('hub choice never invents a root connection when the internal hub is deeper', () => {
  const input = model(['r', 'entry', 'hub', 'x', 'y'], [edge('r', 'entry'), edge('entry', 'hub'), edge('hub', 'x', 99), edge('hub', 'y', 99)]);
  const result = inferFlow(input, {conductorId: 'r'});
  assert.equal(result.parentByAgent.get('entry'), 'r');
  assert.equal(result.parentByAgent.get('hub'), 'entry');
  assertForest(result, input);
});

test('cyclic reciprocal traffic becomes an acyclic visual backbone without changing full traffic', async () => {
  const ids = ['a', 'b', 'c', 'd'];
  const input = model(ids, ids.flatMap((source, i) => ids.slice(i + 1).flatMap(target => [edge(source, target, 3), edge(target, source, 5)])));
  const before = JSON.stringify(input);
  const result = await layoutFlow(input);
  assert.equal(JSON.stringify(input), before);
  assertForest(result, input);
  assertReadable(result, ids);
});

test('parent correction changes depth but rejects cycles, root parents, absent and unrelated identities', () => {
  const input = branchModel(), initial = inferFlow(input);
  assert.equal(validateParentOverride(input, initial, 'A1', 'root').valid, true);
  assert.equal(validateParentOverride(input, initial, 'A', 'A1').valid, false);
  assert.equal(validateParentOverride(input, initial, 'root', 'A').valid, false);
  assert.equal(validateParentOverride(input, initial, 'A1', 'B').valid, false);
  assert.equal(validateParentOverride(input, initial, 'A1', 'hidden').valid, false);
  const result = inferFlow(input, {parentOverrides: new Map([['A1', 'root'], ['A', 'A2'], ['B1', 'hidden']])});
  assert.equal(result.parentByAgent.get('A1'), 'root');
  assert.equal(result.rankByAgent.get('A1'), 1);
  assert.equal(result.parentByAgent.get('A'), 'root');
  assert.equal(result.acceptedOverrides.size, 1);
  assert.match(result.warning, /1 parent choice/);
  assertForest(result, input);
});

test('multiple parent corrections validate the final forest independent of application order', () => {
  const input = branchModel();
  const choices = [['A', 'A1'], ['A1', 'root']];
  for (const overrides of [choices, [...choices].reverse()]) {
    const result = inferFlow(input, {parentOverrides: new Map(overrides)});
    assert.equal(result.warning, null);
    assert.equal(result.acceptedOverrides.size, 2);
    assert.equal(result.parentByAgent.get('A'), 'A1');
    assert.equal(result.parentByAgent.get('A1'), 'root');
    assert.equal(result.rankByAgent.get('A2'), 3);
    assertForest(result, input);
  }
  const cyclic = inferFlow(input, {parentOverrides: new Map([['A', 'A1'], ['A1', 'A'], ['B1', 'root']])});
  assert.equal(cyclic.parentByAgent.get('A'), 'root');
  assert.equal(cyclic.parentByAgent.get('A1'), 'A');
  assert.equal(cyclic.parentByAgent.get('B1'), 'root');
  assert.equal(cyclic.acceptedOverrides.size, 1, 'unrelated valid choices survive');
  assertForest(cyclic, input);
});

test('parent options use observed visible edges and preserve individual cycle verdicts', () => {
  const input = branchModel(), inference = inferFlow(input);
  input.connections.push(edge('A', 'hidden'), edge('A', 'B', 0));
  const options = parentOverrideOptions(input, inference, 'A');
  assert.deepEqual([...options.keys()], ['A1', 'A2', 'A3', 'root']);
  for (const [parent, verdict] of options) assert.deepEqual(verdict, validateParentOverride(input, inference, 'A', parent));
  assert.equal(parentOverrideOptions(input, inference, 'hidden').size, 0);
});

test('superseded queued and partial layouts abort without blocking the next request', async () => {
  const input = branchModel();
  let current = true;
  const queued = layoutFlow(input, {isCurrent: () => current});
  current = false;
  await assert.rejects(queued, {name: 'AbortError'});
  let checks = 0;
  await assert.rejects(layoutFlow(input, {isCurrent: () => ++checks < 4}), {name: 'AbortError'});
  const result = await layoutFlow(input);
  assert.match(result.method, /^ELK Layered/);
  assertReadable(result, input.agents.map(a => a.id));
});

test('removed identities, roots, overrides and hidden edges are purged from every inference surface', async () => {
  const input = branchModel();
  input.agents = input.agents.filter(a => !['root', 'A'].includes(a.id));
  input.connections.push(edge('secret', 'B', 100000), edge('B', 'B', 999999));
  const result = await layoutFlow(input, {conductorId: 'root', parentOverrides: new Map([['A1', 'A'], ['secret', 'B']])});
  // Inspect values and Map keys, not field names: components legitimately has
  // a "root" property even after the agent whose ID was "root" disappears.
  const strings = value => typeof value === 'string' ? [value]
    : value instanceof Map ? [...value].flatMap(strings)
    : Array.isArray(value) ? value.flatMap(strings)
    : value && typeof value === 'object' ? Object.values(value).flatMap(strings) : [];
  const values = strings(result);
  for (const removed of ['root', 'A', 'secret']) assert.equal(values.some(value =>
    value === removed || value.includes(JSON.stringify(removed))), false, `removed identity ${removed}`);
  assert.equal(result.acceptedOverrides.size, 0);
  assertForest(result, input);
  assertReadable(result, input.agents.map(a => a.id));
});

test('ELK produces readable layers in both directions while preserving inferred ranks', async () => {
  const input = branchModel();
  for (const direction of ['RIGHT', 'DOWN']) {
    const result = await layoutFlow(input, {direction});
    assert.match(result.method, /^ELK Layered/);
    assert.equal(result.warning, null);
    assertReadable(result, input.agents.map(a => a.id));
    const axis = direction === 'DOWN' ? 'y' : 'x';
    assert.equal(result.positions.get('A')[axis], result.positions.get('B')[axis]);
    assert.equal(result.positions.get('A1')[axis], result.positions.get('B3')[axis]);
  }
});

test('node order, reciprocal order and display names do not change deterministic layout', async () => {
  const input = branchModel();
  const a = await layoutFlow(input);
  const reversed = {...input, agents: [...input.agents].reverse().map(agent => ({...agent, name: 'something else'})), connections: [...input.connections].reverse().map(edge => ({...edge, source: edge.target, target: edge.source}))};
  const b = await layoutFlow(reversed);
  assert.deepEqual(a.positions, b.positions);
  assert.deepEqual(a.parentByAgent, b.parentByAgent);
  assert.equal(a.signature, b.signature);
});

test('bounded ELK handles 256-node star without missing cards', async () => {
  const ids = Array.from({length: FLOW_NODE_LIMIT}, (_, i) => `a${String(i).padStart(3, '0')}`);
  const input = model(ids, ids.slice(1).map(id => edge(ids[0], id)));
  const started = performance.now();
  const result = await layoutFlow(input);
  assert.match(result.method, /^ELK Layered/);
  assert.ok(performance.now() - started < 10000, 'bounded layout should complete promptly');
  assertReadable(result, ids);
});

test('large-graph fallback is explicit and retains all agents and visual ranks', async () => {
  const ids = Array.from({length: FLOW_NODE_LIMIT + 1}, (_, i) => `n${i}`);
  const input = model(ids, ids.slice(1).map((id, i) => edge(ids[i], id)));
  const result = await layoutFlow(input, {direction: 'DOWN', conductorId: 'n0'});
  assert.equal(result.method, 'Layer grid fallback');
  assert.match(result.warning, /above 256 agents/);
  assertReadable(result, ids);
  assertForest(result, input);
});
