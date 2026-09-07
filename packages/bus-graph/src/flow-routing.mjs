// A drawing-only orthogonal router. The organizing tree chooses node positions;
// this module routes EVERY retained-message edge through those current positions.
// It never reads the tree, infers dependencies, or removes a feedback connection.
export const FLOW_ROUTING_LIMIT = Object.freeze({nodes: 96, edges: 1024, expansions: 18000, corridorWork: 262144});
const CLEARANCE = 10;
const LANE = 14;
const EPS = 1e-7;
let cached = {key: '', routes: new Map()};
// Scope changes and sign-out must release old endpoint IDs even when another
// layout is active and no empty Flow snapshot would otherwise reach the router.
export function clearFlowRoutingCache() {
  cached = {key: '', routes: new Map()};
}
const finite = (n, fallback) => Number.isFinite(n) ? n : fallback;
const point = (x, y) => ({x, y});
const equal = (a, b) => Math.abs(a.x - b.x) < EPS && Math.abs(a.y - b.y) < EPS;

function box(node) {
  const x = finite(node.position?.x, 0), y = finite(node.position?.y, 0);
  const width = Math.max(1, finite(node.measured?.width, finite(node.width, 250)));
  const height = Math.max(1, finite(node.measured?.height, finite(node.height, 120)));
  return {id: node.id, left: x - CLEARANCE, right: x + width + CLEARANCE,
    top: y - CLEARANCE, bottom: y + height + CLEARANCE,
    cx: x + width / 2, cy: y + height / 2};
}

// Rectangle boundaries themselves are legal: the expansion supplies clearance.
function clearSegment(a, b, boxes, omitted = new Set()) {
  if (Math.abs(a.x - b.x) > EPS && Math.abs(a.y - b.y) > EPS) return false;
  return !boxes.some(r => {
    if (omitted.has(r.id)) return false;
    if (Math.abs(a.x - b.x) < EPS) return a.x > r.left + EPS && a.x < r.right - EPS && Math.max(a.y, b.y) > r.top + EPS && Math.min(a.y, b.y) < r.bottom - EPS;
    return a.y > r.top + EPS && a.y < r.bottom - EPS && Math.max(a.x, b.x) > r.left + EPS && Math.min(a.x, b.x) < r.right - EPS;
  });
}

function compact(points) {
  const result = [];
  for (const p of points) {
    if (result.length && equal(result.at(-1), p)) continue;
    while (result.length > 1) {
      const a = result.at(-2), b = result.at(-1);
      const vertical = Math.abs(a.x - b.x) < EPS && Math.abs(b.x - p.x) < EPS;
      const horizontal = Math.abs(a.y - b.y) < EPS && Math.abs(b.y - p.y) < EPS;
      // Do not erase reversals; they can be a loop's only visible excursion.
      if (!(vertical && (b.y - a.y) * (p.y - b.y) >= 0 || horizontal && (b.x - a.x) * (p.x - b.x) >= 0)) break;
      result.pop();
    }
    result.push(p);
  }
  return result;
}

function port(r, side, offset = 0) {
  if (side === 'right') return [point(r.right - CLEARANCE, r.cy), point(r.right, r.cy), point(r.right, r.cy + offset)];
  if (side === 'left') return [point(r.left + CLEARANCE, r.cy), point(r.left, r.cy), point(r.left, r.cy + offset)];
  if (side === 'bottom') return [point(r.cx, r.bottom - CLEARANCE), point(r.cx, r.bottom), point(r.cx + offset, r.bottom)];
  return [point(r.cx, r.top + CLEARANCE), point(r.cx, r.top), point(r.cx + offset, r.top)];
}
const sourceHandle = {right: 'out', left: 'out-left', top: 'out-top', bottom: 'out-bottom'};
const targetHandle = {left: 'in', right: 'in-right', top: 'in-top', bottom: 'in-bottom'};

function terminals(source, target, direction, reciprocal) {
  let from, to;
  if (source.id === target.id) [from, to] = direction === 'DOWN' ? ['bottom', 'left'] : ['right', 'top'];
  else {
    const delta = direction === 'DOWN' ? target.cy - source.cy : target.cx - source.cx;
    if (Math.abs(delta) < EPS) [from, to] = direction === 'DOWN' ? ['bottom', 'bottom'] : ['right', 'right'];
    else if (delta > 0) [from, to] = direction === 'DOWN' ? ['bottom', 'top'] : ['right', 'left'];
    else [from, to] = direction === 'DOWN' ? ['top', 'bottom'] : ['left', 'right'];
  }
  const offset = reciprocal ? (source.id < target.id ? -LANE : LANE) : 0;
  return {start: port(source, from, offset), end: port(target, to, offset), sourceHandle: sourceHandle[from], targetHandle: targetHandle[to]};
}

function length(points) {
  return points.slice(1).reduce((total, p, i) => total + Math.abs(p.x - points[i].x) + Math.abs(p.y - points[i].y), 0);
}

function candidates(start, goal, boxes, direction) {
  const left = Math.min(start.x, goal.x, ...boxes.map(r => r.left)) - 26;
  const right = Math.max(start.x, goal.x, ...boxes.map(r => r.right)) + 26;
  const top = Math.min(start.y, goal.y, ...boxes.map(r => r.top)) - 26;
  const bottom = Math.max(start.y, goal.y, ...boxes.map(r => r.bottom)) + 26;
  const xs = [(start.x + goal.x) / 2, start.x, goal.x, left, right];
  const ys = [(start.y + goal.y) / 2, start.y, goal.y, top, bottom];
  const horizontal = xs.map(x => compact([start, point(x, start.y), point(x, goal.y), goal]));
  const vertical = ys.map(y => compact([start, point(start.x, y), point(goal.x, y), goal]));
  const possible = direction === 'DOWN' ? [...vertical, ...horizontal] : [...horizontal, ...vertical];
  return possible.filter(path => path.slice(1).every((p, i) => clearSegment(path[i], p, boxes)))
    .sort((a, b) => length(a) + a.length * 18 - length(b) - b.length * 18)[0] || null;
}

class Heap {
  entries = [];
  push(value) {
    const a = this.entries; let i = a.length; a.push(value);
    while (i) { const p = (i - 1) >> 1; if (a[p].f < value.f || a[p].f === value.f && a[p].id <= value.id) break; a[i] = a[p]; i = p; }
    a[i] = value;
  }
  pop() {
    const a = this.entries, result = a[0], value = a.pop();
    if (a.length) {
      let i = 0;
      while (i * 2 + 1 < a.length) {
        let child = i * 2 + 1;
        if (child + 1 < a.length && (a[child + 1].f < a[child].f || a[child + 1].f === a[child].f && a[child + 1].id < a[child].id)) child++;
        if (value.f < a[child].f || value.f === a[child].f && value.id <= a[child].id) break;
        a[i] = a[child]; i = child;
      }
      a[i] = value;
    }
    return result;
  }
}

function makeGrid(boxes) {
  const unique = values => [...new Set(values)].sort((a, b) => a - b);
  const xs = unique(boxes.flatMap(r => [r.left, r.right, r.cx - LANE, r.cx, r.cx + LANE]));
  const ys = unique(boxes.flatMap(r => [r.top, r.bottom, r.cy - LANE, r.cy, r.cy + LANE]));
  xs.unshift(xs[0] - 26); xs.push(xs.at(-1) + 26);
  ys.unshift(ys[0] - 26); ys.push(ys.at(-1) + 26);
  const nx = xs.length, ny = ys.length;
  const xi = new Map(xs.map((v, i) => [v, i])), yi = new Map(ys.map((v, i) => [v, i]));
  const horizontal = new Uint8Array((nx - 1) * ny), vertical = new Uint8Array(nx * (ny - 1));
  for (const r of boxes) {
    const l = xi.get(r.left), right = xi.get(r.right), t = yi.get(r.top), b = yi.get(r.bottom);
    for (let y = t + 1; y < b; y++) horizontal.fill(1, y * (nx - 1) + l, y * (nx - 1) + right);
    for (let y = t; y < b; y++) vertical.fill(1, y * nx + l + 1, y * nx + right);
  }
  // Reused typed buffers bound allocation across all edges in this one layout.
  const distance = new Float64Array(nx * ny * 3), parent = new Int32Array(nx * ny * 3), stamp = new Uint32Array(nx * ny * 3);
  let run = 0;
  return (start, goal) => {
    run++;
    const sx = xi.get(start.x), sy = yi.get(start.y), gx = xi.get(goal.x), gy = yi.get(goal.y);
    if ([sx, sy, gx, gy].some(v => v === undefined)) return null;
    const first = (sy * nx + sx) * 3, heap = new Heap();
    stamp[first] = run; distance[first] = 0; parent[first] = -1;
    heap.push({id: first, f: Math.abs(start.x - goal.x) + Math.abs(start.y - goal.y), d: 0});
    let visits = 0;
    while (heap.entries.length && visits++ < FLOW_ROUTING_LIMIT.expansions) {
      const current = heap.pop(), state = current.id;
      if (current.d !== distance[state]) continue;
      const cell = Math.floor(state / 3), axis = state % 3, y = Math.floor(cell / nx), x = cell % nx;
      if (x === gx && y === gy) {
        const result = [];
        for (let at = state; at !== -1; at = parent[at]) { const cell = Math.floor(at / 3); result.push(point(xs[cell % nx], ys[Math.floor(cell / nx)])); }
        return compact(result.reverse());
      }
      const next = [];
      if (x && !horizontal[y * (nx - 1) + x - 1]) next.push([x - 1, y, 1]);
      if (x + 1 < nx && !horizontal[y * (nx - 1) + x]) next.push([x + 1, y, 1]);
      if (y && !vertical[(y - 1) * nx + x]) next.push([x, y - 1, 2]);
      if (y + 1 < ny && !vertical[y * nx + x]) next.push([x, y + 1, 2]);
      for (const [xx, yy, aa] of next) {
        const id = (yy * nx + xx) * 3 + aa;
        const d = current.d + Math.abs(xs[xx] - xs[x]) + Math.abs(ys[yy] - ys[y]) + (axis && aa !== axis ? 18 : 0);
        if (stamp[id] === run && distance[id] <= d) continue;
        stamp[id] = run; distance[id] = d; parent[id] = state;
        heap.push({id, d, f: d + Math.abs(xs[xx] - goal.x) + Math.abs(ys[yy] - goal.y)});
      }
    }
    return null;
  };
}

function routeData(points, reciprocal, source, target, warning) {
  points = compact(points);
  let longest = -1, labelX = points[0].x, labelY = points[0].y, horizontal = true;
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1], b = points[i], size = Math.abs(b.x - a.x) + Math.abs(b.y - a.y);
    if (size > longest) { longest = size; labelX = (a.x + b.x) / 2; labelY = (a.y + b.y) / 2; horizontal = Math.abs(b.y - a.y) < EPS; }
  }
  // Counts stay distinct even if constrained corridors make return paths meet.
  if (reciprocal) { const offset = source < target ? -11 : 11; if (horizontal) labelY += offset; else labelX += offset; }
  return {points, path: points.map((p, i) => `${i ? 'L' : 'M'}${p.x},${p.y}`).join(' '), labelX, labelY, warning: warning || null};
}

// Count labels belong to actual message edges. Give their rectangles room too:
// different shortcuts can share the same long corridor (and its midpoint).
// A bounded greedy pass tries other points on the same routed segments, without
// changing the route or hiding a count. Above the routing budget, keep the
// simple midpoint placement rather than doing quadratic work while dragging.
function placeLabels(routes, boxes, widths) {
  const occupied = [];
  const intersects = (a, b) => a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;
  for (const [id, route] of routes) {
    if (!route.path) continue;
    const width = widths.get(id), height = 20;
    const rectangle = (x, y) => ({left: x - width / 2, right: x + width / 2, top: y - height / 2, bottom: y + height / 2});
    const candidates = [{x: route.labelX, y: route.labelY}];
    const segments = route.points.slice(1).map((b, i) => ({a: route.points[i], b, size: Math.abs(b.x - route.points[i].x) + Math.abs(b.y - route.points[i].y)})).sort((a, b) => b.size - a.size);
    for (const {a, b, size} of segments) {
      const horizontal = Math.abs(a.y - b.y) < EPS;
      if (size < (horizontal ? width : height) + 8) continue;
      for (const fraction of [.5, .35, .65, .2, .8, .1, .9]) {
        const x = a.x + (b.x - a.x) * fraction, y = a.y + (b.y - a.y) * fraction;
        for (const offset of [0, -12, 12, -24, 24]) candidates.push(horizontal ? {x, y: y + offset} : {x: x + offset, y});
      }
    }
    const clear = candidates.find(p => {
      const rect = rectangle(p.x, p.y);
      return !boxes.some(box => intersects(rect, box)) && !occupied.some(other => intersects(rect, other));
    });
    if (clear) { route.labelX = clear.x; route.labelY = clear.y; }
    occupied.push(rectangle(route.labelX, route.labelY));
  }
}

export function routeFlowEdges(nodes, edges, {direction = 'RIGHT'} = {}) {
  direction = direction === 'DOWN' ? 'DOWN' : 'RIGHT';
  const boxes = nodes.filter(n => n.type !== 'device').map(box).sort((a, b) => a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
  const compare = (a, b) => a < b ? -1 : a > b ? 1 : 0;
  const topology = edges.map(e => [e.id, e.source, e.target, Math.max(24, Math.min(144, String(e.data?.messageCount ?? e.label ?? '').length * 6 + 12))])
    .sort((a, b) => compare(a[0], b[0]) || compare(a[1], b[1]) || compare(a[2], b[2]));
  const key = JSON.stringify([direction, boxes, topology]);
  if (key !== cached.key) {
    const byId = new Map(boxes.map(r => [r.id, r]));
    const pairs = new Set(edges.map(e => JSON.stringify([e.source, e.target])));
    const routes = new Map();
    const bounded = boxes.length > FLOW_ROUTING_LIMIT.nodes || edges.length > FLOW_ROUTING_LIMIT.edges;
    // The search limit alone is insufficient for a dense fallback: checking
    // every dogleg against every card costs O(edges × nodes) on each drag frame.
    const inspectCorridors = boxes.length * topology.length <= FLOW_ROUTING_LIMIT.corridorWork;
    let grid = null;
    for (const [id, from, to] of topology) {
      const source = byId.get(from), target = byId.get(to);
      if (!source || !target) { routes.set(id, {warning: 'A message endpoint is unavailable for Flow routing.'}); continue; }
      const reciprocal = from !== to && pairs.has(JSON.stringify([to, from]));
      const terminals_ = terminals(source, target, direction, reciprocal);
      const {start, end} = terminals_;
      const stubsClear = inspectCorridors && start.slice(1).every((p, i) => clearSegment(start[i], p, boxes, new Set([from]))) && end.slice(1).every((p, i) => clearSegment(end[i], p, boxes, new Set([to])));
      let middle = stubsClear && candidates(start.at(-1), end.at(-1), boxes, direction);
      if (!middle && stubsClear && !bounded) { grid ||= makeGrid(boxes); middle = grid(start.at(-1), end.at(-1)); }
      let warning = bounded ? `Flow uses bounded corridor routing above ${FLOW_ROUTING_LIMIT.nodes} cards or ${FLOW_ROUTING_LIMIT.edges} connections.` : null;
      if (!middle) {
        // Keep the connection/count visible when overlapping cards or the search
        // budget make a clear route impossible. This fallback is explicitly flagged.
        middle = direction === 'DOWN' ? [start.at(-1), point(start.at(-1).x, end.at(-1).y), end.at(-1)] : [start.at(-1), point(end.at(-1).x, start.at(-1).y), end.at(-1)];
        warning = 'Some Flow connections use a fallback route that may cross cards; spread overlapping cards or use fewer visible connections.';
      }
      routes.set(id, {...terminals_, ...routeData([...start, ...middle, ...[...end].reverse()], reciprocal, from, to, warning)});
    }
    if (!bounded) placeLabels(routes, boxes, new Map(topology.map(([id, , , width]) => [id, width])));
    cached = {key, routes};
  }
  return edges.map(edge => {
    const route = cached.routes.get(edge.id);
    if (!route?.path) return {...edge, data: {...edge.data, flowRoute: route}};
    const {sourceHandle, targetHandle, start, end, ...flowRoute} = route;
    return {...edge, type: 'flow', sourceHandle, targetHandle, data: {...edge.data, flowRoute}};
  });
}
