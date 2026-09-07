import ELK from 'elkjs/lib/elk.bundled.js';
import {weightedGraph, graphSignature, CARD_WIDTH, CARD_HEIGHT} from './analysis.mjs';

export const FLOW_NODE_LIMIT = 256;
const compare = (a, b) => a < b ? -1 : a > b ? 1 : 0;
const NODE_GAP = 70;
const LAYER_GAP = 170;
const COMPONENT_GAP = 150;
const elk = new ELK({algorithms: ['layered']});
// ELK uses a promise API even without a Web Worker. Serialize its bounded jobs;
// callers may invalidate old results while a new snapshot is waiting its turn.
let layoutQueue = Promise.resolve();

function neighborsFor(graph) {
  const neighbors = new Map(graph.ids.map(id => [id, new Map()]));
  for (const edge of graph.edges) {
    neighbors.get(edge.source).set(edge.target, edge.weight);
    neighbors.get(edge.target).set(edge.source, edge.weight);
  }
  return neighbors;
}

function componentsOf(ids, neighbors) {
  const remaining = new Set(ids), result = [];
  for (const first of ids) {
    if (!remaining.delete(first)) continue;
    const members = [first];
    for (let i = 0; i < members.length; i++) {
      for (const next of neighbors.get(members[i]).keys()) if (remaining.delete(next)) members.push(next);
    }
    result.push(members.sort(compare));
  }
  return result.sort((a, b) => b.length - a.length || compare(a[0], b[0]));
}

function weightWithin(id, members, neighbors) {
  let value = 0;
  for (const [neighbor, weight] of neighbors.get(id)) if (members.has(neighbor)) value += weight;
  return value;
}

function validateWithNeighbors(neighbors, inference, childId, parentId) {
  if (!neighbors.has(childId) || !neighbors.has(parentId)) return {valid: false, reason: 'Both agents must be visible on this bus.'};
  if (childId === parentId) return {valid: false, reason: 'An agent cannot be its own parent.'};
  if (inference.roots.includes(childId)) return {valid: false, reason: 'A component root cannot have a parent. Choose a different root first.'};
  if (!neighbors.get(childId).has(parentId)) return {valid: false, reason: 'A parent must have an observed connection to this agent.'};
  const visited = new Set();
  for (let id = parentId; id != null; id = inference.parentByAgent.get(id)) {
    if (id === childId || visited.has(id)) return {valid: false, reason: 'That choice would create a cycle in the visual hierarchy.'};
    visited.add(id);
  }
  return {valid: true, reason: null};
}

function cycleMembers(parents) {
  const done = new Set(), cyclic = new Set();
  for (const first of parents.keys()) {
    const path = [], index = new Map();
    let id = first;
    while (id != null && !done.has(id) && !index.has(id)) {
      index.set(id, path.length);
      path.push(id);
      id = parents.get(id);
    }
    if (index.has(id)) for (const member of path.slice(index.get(id))) cyclic.add(member);
    for (const member of path) done.add(member);
  }
  return cyclic;
}

// Parent choices describe the drawing only. A real relationship in either
// message direction is required, and no message is sent or rewritten here.
export function validateParentOverride(model, inference, childId, parentId) {
  return validateWithNeighbors(neighborsFor(weightedGraph(model)), inference, childId, parentId);
}

export function parentOverrideOptions(model, inference, childId) {
  const neighbors = neighborsFor(weightedGraph(model));
  return new Map([...(neighbors.get(childId)?.keys() || [])].map(parentId =>
    [parentId, validateWithNeighbors(neighbors, inference, childId, parentId)]));
}

export function inferFlow(model = {}, {conductorId = null, parentOverrides = new Map()} = {}) {
  const graph = weightedGraph(model), neighbors = neighborsFor(graph);
  const weightedDegrees = new Map(graph.ids.map(id => [id, [...neighbors.get(id).values()].reduce((sum, weight) => sum + weight, 0)]));
  const parentByAgent = new Map(), rankByAgent = new Map(), acceptedOverrides = new Map();
  const roots = [], components = [];
  const selected = neighbors.has(conductorId) ? conductorId : null;
  for (const ids of componentsOf(graph.ids, neighbors)) {
    // Distinct connections identify an overall hub before traffic volume breaks
    // ties. Names and provider/device identities play no role in inference.
    const memberSet = new Set(ids);
    const root = selected && memberSet.has(selected) ? selected : [...ids].sort((a, b) =>
      neighbors.get(b).size - neighbors.get(a).size ||
      weightedDegrees.get(b) - weightedDegrees.get(a) || compare(a, b))[0];
    roots.push(root);
    components.push({root, ids});
    for (const members of componentsOf(ids.filter(id => id !== root), neighbors)) {
      const branchSet = new Set(members);
      const internalWeights = new Map(members.map(id => [id, weightWithin(id, branchSet, neighbors)]));
      // Restrict hub candidates to real root-neighbors: otherwise root→hub
      // would fabricate a relationship when the strongest local hub is deeper.
      const hub = members.filter(id => neighbors.get(root).has(id)).sort((a, b) =>
        internalWeights.get(b) - internalWeights.get(a) ||
        neighbors.get(root).get(b) - neighbors.get(root).get(a) || compare(a, b))[0];
      parentByAgent.set(hub, root);
      const distance = new Map([[hub, 0]]), queue = [hub];
      for (let i = 0; i < queue.length; i++) {
        for (const next of neighbors.get(queue[i]).keys()) {
          if (!branchSet.has(next) || distance.has(next)) continue;
          distance.set(next, distance.get(queue[i]) + 1);
          queue.push(next);
        }
      }
      for (const id of members) {
        if (id === hub) continue;
        const parent = [...neighbors.get(id)].filter(([other]) => distance.get(other) === distance.get(id) - 1)
          .sort(([a, aw], [b, bw]) => bw - aw || compare(a, b))[0][0];
        parentByAgent.set(id, parent);
      }
    }
  }
  const originalParents = new Map(parentByAgent);
  const overrides = parentOverrides instanceof Map ? [...parentOverrides] : [];
  let rejected = 0;
  for (const [child, parent] of overrides.sort(([a], [b]) => compare(a, b))) {
    // Removed identities disappear silently; never retain them in a warning,
    // branch identifier, or other metadata after access narrows.
    if (!neighbors.has(child) || !neighbors.has(parent)) continue;
    // Validate the final choices together. A valid rotation can momentarily
    // cycle while one parent is changed before another (A→B, B→root).
    if (child !== parent && !roots.includes(child) && neighbors.get(child).has(parent)) {
      parentByAgent.set(child, parent);
      acceptedOverrides.set(child, parent);
    } else rejected++;
  }
  for (let cyclic = cycleMembers(parentByAgent); cyclic.size; cyclic = cycleMembers(parentByAgent)) {
    // Every cycle contains a changed edge because the inferred forest is
    // acyclic. Reject all choices in each cycle, then recheck: restoring an
    // original edge can expose another conflict. Unrelated choices survive.
    for (const child of cyclic) if (acceptedOverrides.has(child)) {
      parentByAgent.set(child, originalParents.get(child));
      acceptedOverrides.delete(child);
      rejected++;
    }
  }
  const children = new Map(graph.ids.map(id => [id, []]));
  for (const [child, parent] of parentByAgent) children.get(parent).push(child);
  for (const members of children.values()) members.sort(compare);
  const branches = [];
  for (const root of roots) {
    rankByAgent.set(root, 0);
    for (const hub of children.get(root)) {
      const members = [hub];
      rankByAgent.set(hub, 1);
      for (let i = 0; i < members.length; i++) for (const child of children.get(members[i])) {
        rankByAgent.set(child, rankByAgent.get(members[i]) + 1);
        members.push(child);
      }
      branches.push({id: `flow:${JSON.stringify([root, hub])}`, root, hub, members: members.sort(compare)});
    }
  }
  const backbone = [...parentByAgent].map(([target, source]) => ({source, target, weight: neighbors.get(source).get(target)}))
    .sort((a, b) => compare(a.source, b.source) || compare(a.target, b.target));
  return {
    signature: graphSignature(model), rootId: selected || roots[0] || null,
    roots, automaticRoot: selected === null, parentByAgent, rankByAgent, branches, backbone,
    components, acceptedOverrides,
    warning: rejected ? `${rejected} parent choice${rejected === 1 ? ' was' : 's were'} ignored because it would break the visual hierarchy.` : null,
  };
}

function fallbackPositions(flow, direction) {
  const positions = new Map();
  let crossOffset = 0;
  for (const component of flow.components) {
    const levels = new Map();
    for (const id of component.ids) {
      const rank = flow.rankByAgent.get(id);
      if (!levels.has(rank)) levels.set(rank, []);
      levels.get(rank).push(id);
    }
    let largest = 1;
    for (const [rank, ids] of levels) {
      largest = Math.max(largest, ids.length);
      ids.sort(compare).forEach((id, i) => positions.set(id, direction === 'DOWN'
        ? {x: crossOffset + i * (CARD_WIDTH + NODE_GAP), y: rank * (CARD_HEIGHT + LAYER_GAP)}
        : {x: rank * (CARD_WIDTH + LAYER_GAP), y: crossOffset + i * (CARD_HEIGHT + NODE_GAP)}));
    }
    crossOffset += largest * ((direction === 'DOWN' ? CARD_WIDTH : CARD_HEIGHT) + NODE_GAP) + COMPONENT_GAP;
  }
  return positions;
}

function requireCurrent(isCurrent) {
  if (isCurrent()) return;
  const error = new Error('Flow layout superseded');
  error.name = 'AbortError';
  throw error;
}

async function elkPositions(flow, direction, isCurrent) {
  const positions = new Map();
  let crossOffset = 0;
  for (const {root, ids} of flow.components) {
    requireCurrent(isCurrent);
    if (ids.length === 1) {
      positions.set(root, direction === 'DOWN' ? {x: crossOffset, y: 0} : {x: 0, y: crossOffset});
      crossOffset += (direction === 'DOWN' ? CARD_WIDTH : CARD_HEIGHT) + COMPONENT_GAP;
      continue;
    }
    // Surrogate IDs avoid collisions with ELK's graph ID and preserve arbitrary
    // (including malicious-looking) agent IDs as opaque data outside the engine.
    const idToElk = new Map(ids.map((id, i) => [id, `n${i}`]));
    const localEdges = flow.backbone.filter(edge => idToElk.has(edge.source));
    const output = await elk.layout({
      id: 'flow',
      layoutOptions: {
        'elk.algorithm': 'layered', 'elk.direction': direction,
        'elk.randomSeed': '17', 'elk.edgeRouting': 'ORTHOGONAL',
        'elk.padding': '[top=0,left=0,bottom=0,right=0]',
        'elk.spacing.nodeNode': String(NODE_GAP),
        'elk.layered.spacing.nodeNodeBetweenLayers': String(LAYER_GAP),
        'elk.layered.spacing.edgeNodeBetweenLayers': '35',
        'elk.layered.layering.strategy': 'LONGEST_PATH_SOURCE',
        'elk.layered.considerModelOrder.strategy': 'NODES_AND_EDGES',
        'elk.layered.nodePlacement.strategy': 'NETWORK_SIMPLEX',
        'elk.layered.thoroughness': '10',
      },
      children: ids.map(id => ({id: idToElk.get(id), width: CARD_WIDTH, height: CARD_HEIGHT})),
      edges: localEdges.map((edge, i) => ({id: `e${i}`, sources: [idToElk.get(edge.source)], targets: [idToElk.get(edge.target)],
        layoutOptions: {'elk.layered.priority.straightness': String(Math.max(1, Math.round(edge.weight * 100)))}})),
    });
    requireCurrent(isCurrent);
    const byElk = new Map((output.children || []).map(node => [node.id, node]));
    for (const id of ids) {
      const point = byElk.get(idToElk.get(id));
      if (!point || !Number.isFinite(point.x) || !Number.isFinite(point.y)) throw new Error('Incomplete ELK layout');
      positions.set(id, direction === 'DOWN' ? {x: point.x + crossOffset, y: point.y} : {x: point.x, y: point.y + crossOffset});
    }
    crossOffset += (direction === 'DOWN' ? output.width : output.height) + COMPONENT_GAP;
  }
  return positions;
}

export async function layoutFlow(model = {}, options = {}) {
  const isCurrent = typeof options.isCurrent === 'function' ? options.isCurrent : () => true;
  requireCurrent(isCurrent);
  const flow = inferFlow(model, options);
  const direction = options.direction === 'DOWN' ? 'DOWN' : 'RIGHT';
  const count = flow.rankByAgent.size;
  if (count > FLOW_NODE_LIMIT) return {...flow, direction, positions: fallbackPositions(flow, direction),
    method: 'Layer grid fallback', warning: [flow.warning, `Flow uses a layer grid above ${FLOW_NODE_LIMIT} agents; every visible agent is retained.`].filter(Boolean).join(' ')};
  try {
    const operation = layoutQueue.then(() => {
      requireCurrent(isCurrent);
      return elkPositions(flow, direction, isCurrent);
    });
    layoutQueue = operation.catch(() => undefined);
    return {...flow, direction, positions: await operation, method: 'ELK Layered · inferred communication hierarchy'};
  } catch (error) {
    if (error?.name === 'AbortError') throw error;
    return {...flow, direction, positions: fallbackPositions(flow, direction), method: 'Layer grid fallback',
      warning: [flow.warning, 'ELK could not arrange this graph. A readable layer grid preserves every visible agent.'].filter(Boolean).join(' ')};
  }
}
