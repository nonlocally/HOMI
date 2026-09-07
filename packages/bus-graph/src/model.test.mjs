import test from 'node:test';
import assert from 'node:assert/strict';
import {snapshotModel, layoutNodes, decorate, agentDetails, agentNodeId} from './model.mjs';

const agent = (id, extra = {}) => ({id, name: id, user: 'aadarwal', kind: 'codex', device: 'mini', device_id: 'device-one', status: 'queueable', buses: ['qit-wilde'], ...extra});
const message = (source, target, extra = {}) => ({source, target, message_count: 1, last_sent_at: 100, status_counts: {delivered: 1}, ...extra});
const snapshot = (agents, edges, extra = {}) => ({agents, buses: [{name: 'qit-wilde', agents, graph: {basis: 'retained_bus_messages', retention_seconds: 604800, edges}}], ...extra});

test('bus membership never fabricates message links', () => {
  const result = snapshotModel(snapshot([agent('a'), agent('b')], []));
  assert.equal(result.agents.length, 2);
  assert.deepEqual(result.connections, []);
  assert.equal(result.observed, true);
  assert.equal(snapshotModel({agents: result.agents, buses: [{name: 'qit-wilde', agents: result.agents}]}).observed, false);
});

test('edges require both visible agents and membership on their source bus', () => {
  const a = agent('a'), b = agent('b'), privateAgent = agent('private');
  const result = snapshotModel({agents: [a, b], buses: [
    {name: 'qit-wilde', agents: [a, b], graph: {basis: 'retained_bus_messages', edges: [message('a', 'b'), message('a', 'private')]}},
    {name: 'other-bus', agents: [privateAgent], graph: {basis: 'retained_bus_messages', edges: [message('b', 'a'), message('private', 'a')]}},
  ]});
  assert.deepEqual(result.connections.map(edge => [edge.source, edge.target]), [['a', 'b']]);
});

test('parallel buses aggregate retained directed counts and actual receipt states', () => {
  const agents = [agent('a'), agent('b')];
  const base = snapshot(agents, [message('a', 'b', {message_count: 3, status_counts: {accepted: 1, leased: 1, cancelled: 1, unused: 100}}), message('b', 'a')]);
  base.buses.push({name: 'general', agents, graph: {basis: 'retained_bus_messages', edges: [message('a', 'b', {message_count: 2, last_sent_at: 200, status_counts: {queued: 2}})]}});
  const result = snapshotModel(base);
  assert.equal(result.connections.length, 2);
  assert.equal(result.connections[0].message_count, 5);
  assert.equal(result.connections[0].last_sent_at, 200);
  assert.deepEqual(result.connections[0].status_counts, {accepted: 1, leased: 1, cancelled: 1, queued: 2});
  assert.deepEqual(result.connections[0].buses, ['qit-wilde', 'general']);
});

test('21 agents on one device get a balanced three-column grid', () => {
  const model = snapshotModel(snapshot(Array.from({length: 21}, (_, index) => agent(`a${index}`)), []));
  const nodes = layoutNodes(model).filter(node => node.type === 'agent');
  assert.equal(new Set(nodes.map(node => node.position.x)).size, 3);
  assert.equal(new Set(nodes.map(node => node.position.y)).size, 7);
  assert.equal(new Set(nodes.map(node => JSON.stringify(node.position))).size, 21);
  assert.ok(Math.max(...nodes.map(node => node.position.y)) < 1200);
});

test('refresh retains moved nodes; removed or reassigned nodes lose old positions', () => {
  const model = snapshotModel(snapshot([agent('a'), agent('b')], [message('a', 'b')]));
  const nodes = layoutNodes(model);
  nodes.find(node => node.id === agentNodeId('a')).position = {x: 1234, y: 5678};
  const updated = snapshotModel(snapshot([agent('a', {status: 'offline', device: 'renamed'}), agent('c')], []));
  const next = layoutNodes(updated, nodes);
  assert.deepEqual(next.find(node => node.id === agentNodeId('a')).position, {x: 1234, y: 5678});
  assert.ok(!next.some(node => node.id === agentNodeId('b')));
  const reassigned = snapshotModel(snapshot([agent('a', {device_id: 'different-device'})], []));
  assert.notDeepEqual(layoutNodes(reassigned, next).find(node => node.id === agentNodeId('a')).position, {x: 1234, y: 5678});
});

test('selection highlights only actual neighbors and prunes departed details', () => {
  const model = snapshotModel(snapshot([agent('a'), agent('b'), agent('c'), agent('d')], [message('a', 'b'), message('c', 'a'), message('c', 'd')]));
  const selected = decorate(model, layoutNodes(model), agentNodeId('a'));
  assert.equal(selected.nodes.find(node => node.id === agentNodeId('b')).data.neighbor, true);
  assert.equal(selected.nodes.find(node => node.id === agentNodeId('c')).data.neighbor, true);
  assert.equal(selected.nodes.find(node => node.id === agentNodeId('d')).data.dimmed, true);
  assert.equal(selected.edges.find(edge => edge.source === agentNodeId('c') && edge.target === agentNodeId('d')).label, undefined);
  const details = agentDetails(model, selected.selectedId);
  assert.equal(details.sent, 1); assert.equal(details.received, 1); assert.equal(details.peerCount, 2);
  const reduced = snapshotModel(snapshot([agent('d')], []));
  assert.equal(decorate(reduced, layoutNodes(reduced), selected.selectedId).selectedId, null);
  assert.equal(agentDetails(reduced, selected.selectedId), null);
});

test('all-data clear has no hidden node, link, or inspector record', () => {
  const model = snapshotModel();
  const decorated = decorate(model, layoutNodes(model), agentNodeId('private'));
  assert.deepEqual(model.agents, []); assert.deepEqual(model.groups, []);
  assert.deepEqual(model.connections, []); assert.deepEqual(decorated.nodes, []);
  assert.deepEqual(decorated.edges, []); assert.equal(decorated.selectedId, null);
});

test('only display fields enter the model; message bodies and credentials are ignored', () => {
  const input = agent('a', {token: 'private-token', device_metadata: {hostname: 'mini', token: 'private-meta'}});
  const result = snapshotModel(snapshot([input, agent('b')], [message('a', 'b', {body: 'private-message', token: 'private-edge'})]));
  const serialized = JSON.stringify(result);
  assert.ok(!serialized.includes('private-'));
  assert.equal(result.agents[0].device_metadata.hostname, 'mini');
});

test('agent-controlled ids cannot collide with structural device nodes', () => {
  const result = snapshotModel(snapshot([agent('device:one'), agent('__proto__'), agent('agent:one')], [message('__proto__', 'device:one')]));
  const nodes = layoutNodes(result);
  assert.equal(new Set(nodes.map(node => node.id)).size, nodes.length);
  assert.equal(result.connections.length, 1);
});
