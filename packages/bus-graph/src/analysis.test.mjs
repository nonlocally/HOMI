import test from 'node:test';
import assert from 'node:assert/strict';
import {analyzeGraph, graphSignature, weightedGraph, ANALYSIS_NODE_LIMIT, CARD_WIDTH, CARD_HEIGHT} from './analysis.mjs';

const edge = (source, target, message_count = 1) => ({source, target, message_count});
const model = (ids, connections = []) => ({agents: ids.map(id => ({id, name: 'Same displayed name'})), connections});
const near = (actual, expected, epsilon = 1e-9) => assert.ok(Math.abs(actual - expected) < epsilon, `${actual} ≈ ${expected}`);
const clique = ids => ids.flatMap((id, i) => ids.slice(i + 1).map(other => edge(id, other)));

function assertReadable(result) {
  const entries = [...result.positions];
  assert.equal(entries.length, result.stats.agents);
  for (let i = 0; i < entries.length; i++) {
    const [id, position] = entries[i];
    assert.ok(Number.isFinite(position.x) && Number.isFinite(position.y), `finite position ${id}`);
    for (let j = i + 1; j < entries.length; j++) {
      const other = entries[j][1];
      assert.ok(Math.abs(position.x - other.x) >= CARD_WIDTH || Math.abs(position.y - other.y) >= CARD_HEIGHT, `cards ${id} and ${entries[j][0]} overlap`);
    }
  }
}

function checkEigenResidual(result, input) {
  const {edges} = weightedGraph(input);
  for (const component of result.components) {
    const index = new Map(component.ids.map((id, i) => [id, i]));
    const localEdges = edges.filter(value => index.has(value.source));
    const degree = Array(component.ids.length).fill(0);
    for (const value of localEdges) {
      degree[index.get(value.source)] += value.weight;
      degree[index.get(value.target)] += value.weight;
    }
    for (const mode of component.modes) {
      const product = [...mode.vector];
      for (const value of localEdges) {
        const i = index.get(value.source), j = index.get(value.target);
        const weight = value.weight / Math.sqrt(degree[i] * degree[j]);
        product[i] -= weight * mode.vector[j];
        product[j] -= weight * mode.vector[i];
      }
      near(mode.vector.reduce((sum, value) => sum + value * value, 0), 1);
      near(mode.vector.reduce((sum, value, i) => sum + value * Math.sqrt(degree[i]), 0), 0);
      product.forEach((value, i) => near(value, mode.eigenvalue * mode.vector[i], 1e-8));
    }
    if (component.modes.length === 2) near(component.modes[0].vector.reduce((sum, value, i) => sum + value * component.modes[1].vector[i], 0), 0);
  }
}

function independentModularity(input, result) {
  const {ids, edges} = weightedGraph(input);
  const degree = new Map(ids.map(id => [id, 0]));
  let m = 0, internal = 0;
  for (const edge of edges) {
    m += edge.weight;
    degree.set(edge.source, degree.get(edge.source) + edge.weight);
    degree.set(edge.target, degree.get(edge.target) + edge.weight);
    if (result.communityByAgent.get(edge.source) === result.communityByAgent.get(edge.target)) internal += edge.weight;
  }
  if (!m) return null;
  return internal / m - result.communities.reduce((penalty, community) => penalty + (community.members.reduce((sum, id) => sum + degree.get(id), 0) / (2 * m)) ** 2, 0);
}

test('empty and isolated agents require no artificial links or eigenvalues', () => {
  const empty = analyzeGraph();
  assert.deepEqual(empty.stats, {agents: 0, relationships: 0, components: 0, isolates: 0, communities: 0, modularity: null});
  assert.equal(empty.warning, null);
  const isolated = analyzeGraph(model(['z', 'a', 'b']));
  assert.equal(isolated.stats.communities, 3);
  assert.equal(isolated.stats.modularity, null);
  assert.ok(isolated.components.every(component => component.eigenvalues.length === 0 && component.modes.length === 0));
  assertReadable(isolated);
});

test('aggregate both directions before logarithm; hidden endpoints and self messages never participate', () => {
  const input = model(['a', 'b'], [edge('a', 'b', 3), edge('b', 'a', 4), edge('a', 'b', 2), edge('a', 'a', 999), edge('a', 'secret', 999), edge('b', 'secret', 999), edge('a', 'b', 0), edge('a', 'b', NaN), edge('a', 'b', Infinity)]);
  const graph = weightedGraph(input);
  assert.deepEqual(graph.edges, [{source: 'a', target: 'b', count: 9, weight: Math.log(10)}]);
  const result = analyzeGraph(input);
  assert.equal(result.stats.relationships, 1);
  assert.equal(JSON.stringify(result).includes('secret'), false);
  assert.equal(graphSignature(input), graphSignature(model(['b', 'a'], [edge('b', 'a', 9)])));
  assert.notEqual(graphSignature(input), graphSignature(model(['a', 'b'], [edge('b', 'a', 10)])));
});

test('two-node component has exact normalized-Laplacian spectrum and a zero second axis', () => {
  const input = model(['b', 'a'], [edge('a', 'b', 40)]);
  const result = analyzeGraph(input);
  near(result.components[0].eigenvalues[0], 2);
  near(result.components[0].modes[0].vector[0], 1 / Math.sqrt(2));
  near(result.components[0].modes[0].vector[1], -1 / Math.sqrt(2));
  assert.equal(result.rawPositions.get('a').y, result.rawPositions.get('b').y);
  near(result.stats.modularity, 0);
  checkEigenResidual(result, input);
  assertReadable(result);
});

test('path spectrum, degree-orthogonality and eigen residuals match mathematical definition', () => {
  const input = model(['a', 'b', 'c', 'd'], [edge('a', 'b'), edge('b', 'c'), edge('c', 'd')]);
  const result = analyzeGraph(input);
  result.components[0].eigenvalues.forEach((value, i) => near(value, [.5, 1.5, 2][i]));
  checkEigenResidual(result, input);
  assertReadable(result);
});

test('degenerate complete-graph modes use a canonical projected basis, not arbitrary solver axes', () => {
  const ids = ['a', 'b', 'c', 'd'];
  const input = model(ids, clique(ids));
  const result = analyzeGraph(input);
  result.components[0].eigenvalues.forEach(value => near(value, 4 / 3));
  const [first, second] = result.components[0].modes;
  first.vector.forEach((value, i) => near(value, i === 0 ? Math.sqrt(3) / 2 : -1 / (2 * Math.sqrt(3))));
  second.vector.forEach((value, i) => near(value, [0, Math.sqrt(2 / 3), -1 / Math.sqrt(6), -1 / Math.sqrt(6)][i]));
  checkEigenResidual(result, input);
  assertReadable(result);
});

test('equivalent star leaves remain mathematically valid and cards are separated', () => {
  const ids = ['hub', ...Array.from({length: 35}, (_, i) => `leaf-${String(i).padStart(2, '0')}`)];
  const input = model(ids, ids.slice(1).map(id => edge('hub', id)));
  const result = analyzeGraph(input);
  result.components[0].eigenvalues.slice(0, -1).forEach(value => near(value, 1));
  near(result.components[0].eigenvalues.at(-1), 2);
  checkEigenResidual(result, input);
  assertReadable(result);
});

test('disconnected components and isolated IDs are independently analyzed and packed', () => {
  const input = model(['a', 'b', 'x', 'y', 'z', 'alone'], [edge('a', 'b'), ...clique(['x', 'y', 'z'])]);
  const result = analyzeGraph(input);
  assert.deepEqual(result.components.map(component => component.ids.length), [3, 2, 1]);
  assert.equal(result.stats.isolates, 1);
  assertReadable(result);
  checkEigenResidual(result, input);
});

test('seeded Leiden finds connected bridged cliques and reports actual weighted modularity', () => {
  const left = ['a', 'b', 'c', 'd', 'e'], right = ['v', 'w', 'x', 'y', 'z'];
  const input = model([...left, ...right, 'isolate'], [...clique(left), ...clique(right), edge('e', 'v')]);
  const result = analyzeGraph(input);
  assert.equal(result.warning, null);
  assert.deepEqual(result.communities.map(community => community.members), [left, right, ['isolate']]);
  near(result.stats.modularity, 20 / 21 - .5);
  near(result.stats.modularity, independentModularity(input, result));
  checkEigenResidual(result, input);
  assertReadable(result);
});

test('analysis is invariant to input order, edge direction reversal and duplicate display names', () => {
  const ids = ['a', 'b', 'c', 'd', 'e', 'f'];
  const connections = [...clique(ids.slice(0, 3)), ...clique(ids.slice(3)), edge('c', 'd', 3)];
  const input = model(ids, connections);
  const reordered = model([...ids].reverse(), connections.map(value => ({...value, source: value.target, target: value.source})).reverse());
  assert.deepEqual(analyzeGraph(input), analyzeGraph(reordered));
  assert.deepEqual(analyzeGraph(input), analyzeGraph(input));
});

test('large finite message counts remain finite and weighted spectra stay valid', () => {
  const input = model(['a', 'b', 'c'], [edge('a', 'b', Number.MAX_SAFE_INTEGER), edge('b', 'a', Number.MAX_SAFE_INTEGER), edge('b', 'c', 1)]);
  const result = analyzeGraph(input);
  assert.equal(result.warning, null);
  assert.ok(Number.isFinite(result.stats.modularity));
  checkEigenResidual(result, input);
  assertReadable(result);
});

test('bounded large-network fallback preserves every identity and labels its actual method', () => {
  const ids = Array.from({length: ANALYSIS_NODE_LIMIT + 1}, (_, i) => `a${i}`);
  const input = model(ids, ids.slice(1).map((id, i) => edge(ids[i], id)));
  const result = analyzeGraph(input);
  assert.equal(result.stats.agents, ids.length);
  assert.match(result.warning, /limited to 256 agents/);
  assert.match(result.method.layout, /Grid fallback/);
  assert.match(result.method.community, /Connected components/);
  assert.equal(result.stats.modularity, null);
  assert.equal(result.components[0].eigenvalues.length, 0);
  assertReadable(result);
});

test('revoked or removed endpoints disappear from positions, spectra and communities', () => {
  const input = model(['a', 'b', 'c'], [edge('a', 'b'), edge('b', 'c')]);
  const first = analyzeGraph(input);
  const next = analyzeGraph({...input, agents: input.agents.filter(agent => agent.id !== 'b')});
  assert.notEqual(first.signature, next.signature);
  assert.deepEqual([...next.positions.keys()].sort(), ['a', 'c']);
  assert.equal(next.communityByAgent.has('b'), false);
  assert.equal(next.rawPositions.has('b'), false);
  assert.equal(next.stats.relationships, 0);
  assert.ok(next.components.every(component => !component.ids.includes('b')));
  assert.ok(next.communities.every(community => !community.members.includes('b')));
});
