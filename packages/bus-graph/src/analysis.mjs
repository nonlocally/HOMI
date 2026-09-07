import {Matrix, EigenvalueDecomposition} from 'ml-matrix';
import Network from 'networkanalysis-ts/network.js';
import LeidenAlgorithm from 'networkanalysis-ts/leidenAlgorithm.js';
import Clustering from 'networkanalysis-ts/clustering.js';
import Random from 'java-random';

// Dense eigendecomposition is deliberately bounded. Nothing is dropped when the
// limit is exceeded: the caller gets a labelled, readable fallback for all IDs.
export const ANALYSIS_NODE_LIMIT = 256;
export const CARD_WIDTH = 250;
export const CARD_HEIGHT = 120;
const GAP = 34;
const WIDTH = CARD_WIDTH + GAP;
const HEIGHT = CARD_HEIGHT + GAP;
const compare = (a, b) => a < b ? -1 : a > b ? 1 : 0;
const dot = (a, b) => a.reduce((sum, value, i) => sum + value * b[i], 0);

// All analysis starts from the permitted snapshot. Self-messages have no
// between-agent relationship; direction is combined only for this analysis.
// Sum the two directions and any bus contributions BEFORE compressing volume.
export function weightedGraph(model = {}) {
  const ids = [...new Set((model.agents || []).map(agent => agent?.id).filter(id => typeof id === 'string' && id))].sort(compare);
  const allowed = new Set(ids);
  const pairs = new Map();
  for (const edge of model.connections || []) {
    if (!edge || !allowed.has(edge.source) || !allowed.has(edge.target) || edge.source === edge.target) continue;
    if (!Number.isFinite(edge.message_count) || edge.message_count < 1) continue;
    const [source, target] = [edge.source, edge.target].sort(compare);
    const key = JSON.stringify([source, target]);
    const old = pairs.get(key);
    const count = Math.min(Number.MAX_SAFE_INTEGER, (old?.count || 0) + Math.floor(edge.message_count));
    pairs.set(key, {source, target, count});
  }
  const edges = [...pairs.values()].sort((a, b) => compare(a.source, b.source) || compare(a.target, b.target))
    .map(edge => ({...edge, weight: Math.log1p(edge.count)}));
  return {ids, edges};
}

function signatureFor(graph) {
  // A lossless canonical signature avoids hash collisions and never includes
  // names, credentials, message content, or unpermitted endpoints.
  return JSON.stringify([graph.ids, graph.edges.map(edge => [edge.source, edge.target, edge.count])]);
}

export const graphSignature = model => signatureFor(weightedGraph(model));

function connectedComponents(ids, edges) {
  const neighbors = new Map(ids.map(id => [id, []]));
  for (const edge of edges) {
    neighbors.get(edge.source).push(edge.target);
    neighbors.get(edge.target).push(edge.source);
  }
  const remaining = new Set(ids);
  const components = [];
  for (const first of ids) {
    if (!remaining.delete(first)) continue;
    const members = [first];
    for (let i = 0; i < members.length; i++) {
      for (const neighbor of neighbors.get(members[i])) if (remaining.delete(neighbor)) members.push(neighbor);
    }
    components.push(members.sort(compare));
  }
  return components.sort((a, b) => b.length - a.length || compare(a[0], b[0]));
}

function orient(vector) {
  let anchor = 0;
  for (let i = 1; i < vector.length; i++) if (Math.abs(vector[i]) > Math.abs(vector[anchor]) + 1e-12) anchor = i;
  return vector[anchor] < 0 ? vector.map(value => -value) : vector;
}

// For a repeated eigenvalue, individual solver eigenvectors are arbitrary.
// Project canonical ID-ordered unit vectors into the WHOLE eigenspace, then
// Gram–Schmidt. The resulting basis is independent of the solver's rotation.
function canonicalModes(values, vectors, n) {
  const modes = [];
  for (let start = 1; start < n && modes.length < 2;) {
    let end = start + 1;
    while (end < n && Math.abs(values[end] - values[start]) <= 1e-10) end++;
    const basis = [];
    for (let anchor = 0; anchor < n && basis.length < end - start && modes.length < 2; anchor++) {
      const vector = Array.from({length: n}, (_, row) => {
        let value = 0;
        for (let column = start; column < end; column++) value += vectors.get(row, column) * vectors.get(anchor, column);
        return value;
      });
      // Two passes reduce cancellation for nearly dependent projections.
      for (let pass = 0; pass < 2; pass++) for (const previous of basis) {
        const projection = dot(vector, previous);
        for (let row = 0; row < n; row++) vector[row] -= projection * previous[row];
      }
      const norm = Math.sqrt(dot(vector, vector));
      if (norm <= 1e-9) continue;
      const normalized = orient(vector.map(value => value / norm));
      basis.push(normalized);
      modes.push({eigenvalue: values[start], vector: normalized});
    }
    start = end;
  }
  return modes;
}

function spectralComponent(ids, edges) {
  const n = ids.length;
  if (n === 1) return {eigenvalues: [], modes: [], points: [{x: 0, y: 0}], layout: 'isolate'};
  const index = new Map(ids.map((id, i) => [id, i]));
  const degrees = Array(n).fill(0);
  for (const edge of edges) {
    degrees[index.get(edge.source)] += edge.weight;
    degrees[index.get(edge.target)] += edge.weight;
  }
  const laplacian = Matrix.eye(n);
  for (const edge of edges) {
    const source = index.get(edge.source), target = index.get(edge.target);
    const value = -edge.weight / Math.sqrt(degrees[source] * degrees[target]);
    laplacian.set(source, target, value);
    laplacian.set(target, source, value);
  }
  const decomposition = new EigenvalueDecomposition(laplacian, {assumeSymmetric: true});
  const values = decomposition.realEigenvalues;
  if (!values.every(Number.isFinite)) throw new Error('Nonfinite eigendecomposition');
  const modes = canonicalModes(values, decomposition.eigenvectorMatrix, n);
  if (modes.length !== Math.min(2, n - 1)) throw new Error('Incomplete spectral basis');
  // A connected component has exactly one zero mode. Never threshold away a
  // small positive Fiedler value: it may represent a weak but real bridge.
  const points = ids.map((_, i) => ({x: modes[0].vector[i], y: modes[1]?.vector[i] || 0}));
  const rangeX = Math.max(...points.map(p => p.x)) - Math.min(...points.map(p => p.x));
  const rangeY = Math.max(...points.map(p => p.y)) - Math.min(...points.map(p => p.y));
  const scale = Math.max(580, Math.sqrt(n) * 300) / Math.max(rangeX, rangeY, 1e-9);
  return {eigenvalues: values.slice(1), modes, layout: 'spectral', points: points.map(point => ({x: point.x * scale, y: point.y * scale}))};
}

function gridPoints(n) {
  const columns = Math.max(1, Math.ceil(Math.sqrt(n)));
  return Array.from({length: n}, (_, i) => ({x: (i % columns) * WIDTH, y: Math.floor(i / columns) * HEIGHT}));
}

// Only overlapping card rectangles exert a displacement. There are no springs
// or attraction forces here, so the relationship geometry stays spectral.
function separateCards(raw) {
  const points = raw.map(point => ({...point}));
  for (let iteration = 0; iteration < 80; iteration++) {
    let collisions = 0;
    for (let i = 0; i < points.length; i++) for (let j = i + 1; j < points.length; j++) {
      const a = points[i], b = points[j];
      const dx = b.x - a.x, dy = b.y - a.y;
      const overlapX = WIDTH - Math.abs(dx), overlapY = HEIGHT - Math.abs(dy);
      if (overlapX <= 0 || overlapY <= 0) continue;
      collisions++;
      if (overlapX / WIDTH < overlapY / HEIGHT) {
        const displacement = (overlapX / 2 + .01) * (dx < 0 ? -1 : 1);
        a.x -= displacement; b.x += displacement;
      } else {
        const displacement = (overlapY / 2 + .01) * (dy < 0 ? -1 : 1);
        a.y -= displacement; b.y += displacement;
      }
    }
    if (!collisions) return points;
  }
  // A dense tied eigenspace can retain tiny overlaps after bounded relaxation.
  // Find the nearest legal vertical gap for each residual card, guaranteeing
  // readability without an unbounded simulation or a global force layout.
  const placed = [];
  for (const point of points) {
    const intervals = placed.filter(other => Math.abs(other.x - point.x) < WIDTH)
      .map(other => [other.y - HEIGHT, other.y + HEIGHT]).sort((a, b) => a[0] - b[0]);
    const merged = [];
    for (const interval of intervals) {
      const last = merged.at(-1);
      if (last && interval[0] <= last[1]) last[1] = Math.max(last[1], interval[1]);
      else merged.push([...interval]);
    }
    const blocked = merged.find(([lower, upper]) => point.y > lower && point.y < upper);
    if (blocked) point.y = point.y - blocked[0] <= blocked[1] - point.y ? blocked[0] - .01 : blocked[1] + .01;
    placed.push(point);
  }
  return points;
}

function makeCommunities(groups) {
  return groups.map(members => [...members].sort(compare))
    .sort((a, b) => b.length - a.length || compare(a[0], b[0]))
    .map((members, i) => ({id: `community:${JSON.stringify(members)}`, label: `Group ${i + 1}`, members, colorIndex: i % 8}));
}

function leidenCommunities(graph) {
  if (!graph.edges.length) return {communities: makeCommunities(graph.ids.map(id => [id])), modularity: null};
  // The core API avoids the run helper's console logging and node mutation.
  // Degree node weights + resolution 1/(2m) makes its CPM quality expression
  // exactly standard weighted Newman–Girvan modularity at gamma = 1.
  const index = new Map(graph.ids.map((id, i) => [id, i]));
  const network = new Network({nNodes: graph.ids.length, setNodeWeightsToTotalEdgeWeights: true,
    edges: [graph.edges.map(edge => index.get(edge.source)), graph.edges.map(edge => index.get(edge.target))],
    edgeWeights: graph.edges.map(edge => edge.weight), sortedEdges: false, checkIntegrity: true});
  let best = null, quality = -Infinity, bestKey = '';
  for (const seed of [17, 29, 43]) {
    const algorithm = new LeidenAlgorithm();
    algorithm.initializeBasedOnResolutionAndNIterationsAndRandomnessAndRandom(1 / (2 * network.getTotalEdgeWeight()), 10, .01, new Random(seed));
    const clustering = new Clustering({nNodes: graph.ids.length});
    algorithm.improveClustering(network, clustering);
    const groups = new Map();
    clustering.getClusters().forEach((cluster, i) => {
      if (!groups.has(cluster)) groups.set(cluster, []);
      groups.get(cluster).push(graph.ids[i]);
    });
    const communities = makeCommunities([...groups.values()]);
    const candidateQuality = algorithm.calcQuality(network, clustering);
    const key = JSON.stringify(communities.map(community => community.members));
    if (candidateQuality > quality + 1e-12 || (Math.abs(candidateQuality - quality) <= 1e-12 && compare(key, bestKey) < 0)) {
      best = communities; quality = candidateQuality; bestKey = key;
    }
  }
  if (!best || !Number.isFinite(quality)) throw new Error('Invalid Leiden partition');
  return {communities: best, modularity: quality};
}

export function analyzeGraph(model = {}) {
  const graph = weightedGraph(model);
  const groups = connectedComponents(graph.ids, graph.edges);
  const limited = graph.ids.length > ANALYSIS_NODE_LIMIT;
  const warnings = limited ? [`Spectral and Leiden analysis is limited to ${ANALYSIS_NODE_LIMIT} agents. Showing all ${graph.ids.length} agents in a grid with connected-component groups.`] : [];
  const positions = new Map(), rawPositions = new Map(), components = [];
  const componentById = new Map();
  groups.forEach((ids, i) => ids.forEach(id => componentById.set(id, i)));
  const componentEdges = groups.map(() => []);
  graph.edges.forEach(edge => componentEdges[componentById.get(edge.source)].push(edge));
  const layouts = groups.map((ids, i) => {
    let result;
    try {
      result = limited ? {eigenvalues: [], modes: [], layout: ids.length > 1 ? 'grid' : 'isolate', points: gridPoints(ids.length)} : spectralComponent(ids, componentEdges[i]);
    } catch {
      warnings.push(`Spectral analysis could not resolve a ${ids.length}-agent component; that component uses a grid.`);
      result = {eigenvalues: [], modes: [], layout: 'grid', points: gridPoints(ids.length)};
    }
    const readable = result.layout === 'spectral' ? separateCards(result.points) : result.points.map(point => ({...point}));
    const minX = Math.min(...readable.map(point => point.x), ...result.points.map(point => point.x));
    const minY = Math.min(...readable.map(point => point.y), ...result.points.map(point => point.y));
    const width = Math.max(...readable.map(point => point.x), ...result.points.map(point => point.x)) - minX + CARD_WIDTH;
    const height = Math.max(...readable.map(point => point.y), ...result.points.map(point => point.y)) - minY + CARD_HEIGHT;
    components.push({id: `component:${JSON.stringify(ids)}`, ids, eigenvalues: result.eigenvalues, modes: result.modes, layout: result.layout});
    return {ids, readable, raw: result.points, minX, minY, width, height};
  });
  // Components share no spectral coordinate system, so pack their own canvases.
  const targetWidth = Math.max(1000, Math.sqrt(layouts.reduce((area, item) => area + (item.width + 140) * (item.height + 140), 0)) * 1.3);
  let x = 0, y = 0, rowHeight = 0;
  for (const layout of layouts) {
    if (x && x + layout.width > targetWidth) {x = 0; y += rowHeight + 140; rowHeight = 0;}
    layout.ids.forEach((id, i) => {
      positions.set(id, {x: layout.readable[i].x - layout.minX + x, y: layout.readable[i].y - layout.minY + y});
      rawPositions.set(id, {x: layout.raw[i].x - layout.minX + x, y: layout.raw[i].y - layout.minY + y});
    });
    x += layout.width + 140;
    rowHeight = Math.max(rowHeight, layout.height);
  }
  let communityResult;
  try {
    communityResult = limited ? {communities: makeCommunities(groups), modularity: null} : leidenCommunities(graph);
  } catch {
    warnings.push('Leiden analysis could not be completed; showing connected-component groups.');
    communityResult = {communities: makeCommunities(groups), modularity: null};
  }
  const {communities, modularity} = communityResult;
  const communityByAgent = new Map();
  for (const community of communities) for (const id of community.members) communityByAgent.set(id, community.id);
  return {
    signature: signatureFor(graph), positions, rawPositions, communities, communityByAgent, components,
    stats: {agents: graph.ids.length, relationships: graph.edges.length, components: groups.length, isolates: groups.filter(ids => ids.length === 1).length, communities: communities.length, modularity},
    method: {
      layout: components.some(component => component.layout === 'grid') ? 'Grid fallback (see analysis warning)' : 'Normalized Laplacian · lowest nonzero modes · collision spacing',
      community: limited || warnings.some(warning => warning.startsWith('Leiden')) ? 'Connected components (Leiden unavailable)' : 'Leiden · weighted modularity · resolution 1 · three seeded starts',
      weighting: 'Undirected weight = log(1 + retained messages in both directions); self-messages excluded',
    },
    warning: warnings.length ? warnings.join(' ') : null,
  };
}
