// This model only consumes an already-authorized dashboard snapshot. It neither
// guesses links from bus membership nor keeps agents that leave that snapshot.
export const NODE_WIDTH = 250;
const own = (value, key) => Object.prototype.hasOwnProperty.call(value, key);
const text = (value, fallback = '') => typeof value === 'string' && value ? value : fallback;
const integer = value => Number.isSafeInteger(value) && value >= 0 ? value : 0;
export const timestamp = value => Number.isFinite(value) && value > 0 ? value : 0;
export const owner = agent => text(agent.user, 'Unassigned');
export const deviceName = agent => text(agent.device, text(agent.device_metadata?.hostname, 'Unknown device'));
export const deviceKey = agent => JSON.stringify([owner(agent), text(agent.device_id, text(agent.principal, deviceName(agent)))]);
export const agentNodeId = id => `agent:${id}`;

function cleanAgent(input) {
  const metadata = {};
  for (const key of ['hostname', 'platform', 'tailscale_hostname', 'tailscale_name', 'tailscale_dns_name']) {
    if (typeof input.device_metadata?.[key] === 'string') metadata[key] = input.device_metadata[key];
  }
  return {
    id: input.id, name: text(input.name, 'Unnamed agent'), kind: text(input.kind, 'agent'),
    user: text(input.user), device: text(input.device), device_id: text(input.device_id, text(input.principal)),
    device_metadata: metadata, status: text(input.status, 'unknown'),
    last_seen: timestamp(input.last_seen), description: text(input.description),
    buses: Array.isArray(input.buses) ? [...new Set(input.buses.filter(v => typeof v === 'string'))] : [],
  };
}

export function snapshotModel(snapshot = {}) {
  const agents = new Map();
  for (const input of Array.isArray(snapshot.agents) ? snapshot.agents : []) {
    if (input && typeof input.id === 'string' && input.id && !agents.has(input.id)) agents.set(input.id, cleanAgent(input));
  }
  const connections = new Map();
  let observed = false;
  const retention = [];
  for (const bus of Array.isArray(snapshot.buses) ? snapshot.buses : []) {
    if (!bus || typeof bus.name !== 'string' || !Array.isArray(bus.agents)) continue;
    const members = new Set(bus.agents.filter(a => a && typeof a.id === 'string').map(a => a.id));
    if (bus.graph?.basis !== 'retained_bus_messages' || !Array.isArray(bus.graph.edges)) continue;
    observed = true;
    if (Number.isFinite(bus.graph.retention_seconds) && bus.graph.retention_seconds > 0) retention.push(bus.graph.retention_seconds);
    for (const edge of bus.graph.edges) {
      if (!edge || !agents.has(edge.source) || !agents.has(edge.target) || !members.has(edge.source) || !members.has(edge.target)) continue;
      const count = integer(edge.message_count);
      if (!count) continue;
      const key = JSON.stringify([edge.source, edge.target]);
      const connection = connections.get(key) || {id: key, source: edge.source, target: edge.target, message_count: 0, last_sent_at: 0, buses: [], status_counts: {}};
      connection.message_count += count;
      connection.last_sent_at = Math.max(connection.last_sent_at, timestamp(edge.last_sent_at));
      if (!connection.buses.includes(bus.name)) connection.buses.push(bus.name);
      for (const [status, value] of Object.entries(edge.status_counts || {})) {
        if (['accepted', 'leased', 'delivered', 'queued', 'failed', 'expired', 'cancelled'].includes(status)) {
          connection.status_counts[status] = (own(connection.status_counts, status) ? connection.status_counts[status] : 0) + integer(value);
        }
      }
      connections.set(key, connection);
    }
  }
  const groups = new Map();
  for (const agent of agents.values()) {
    const key = deviceKey(agent);
    if (!groups.has(key)) groups.set(key, {key, owner: owner(agent), name: deviceName(agent), agents: []});
    groups.get(key).agents.push(agent);
  }
  const sortedGroups = [...groups.values()].sort((a, b) => a.owner.localeCompare(b.owner) || a.name.localeCompare(b.name) || a.key.localeCompare(b.key));
  for (const group of sortedGroups) group.agents.sort((a, b) => a.name.localeCompare(b.name) || a.id.localeCompare(b.id));
  return {agents: [...agents.values()], groups: sortedGroups, connections: [...connections.values()], observed, retention_seconds: Math.min(...retention, 604800)};
}

export function layoutNodes(model, previous = []) {
  const positions = new Map(previous.filter(n => n.type === 'agent').map(n => [n.id, n]));
  const deviceColumns = Math.min(2, Math.max(1, model.groups.length));
  const groupColumns = group => Math.min(3, Math.max(1, Math.ceil(Math.sqrt(group.agents.length))));
  const widths = Array.from({length: deviceColumns}, (_, column) => Math.max(...model.groups.filter((_, index) => index % deviceColumns === column).map(group => groupColumns(group) * 350 - 100)));
  const nodes = [];
  let rowY = 0;
  for (let start = 0; start < model.groups.length; start += deviceColumns) {
    const row = model.groups.slice(start, start + deviceColumns);
    const rowHeight = Math.max(...row.map(group => Math.ceil(group.agents.length / groupColumns(group)))) * 170 + 140;
    let groupX = 0;
    for (let column = 0; column < row.length; column++) {
      const group = row[column];
      const columns = groupColumns(group);
      const x = groupX;
      nodes.push({id: `device:${group.key}`, type: 'device', position: {x, y: rowY}, data: {owner: group.owner, name: group.name, count: group.agents.length}, width: columns * 350 - 100, height: 50, draggable: false, selectable: false, connectable: false, focusable: false, deletable: false});
      group.agents.forEach((agent, index) => {
        const id = agentNodeId(agent.id);
        const old = positions.get(id);
        // A renamed device remains the same group, a moved/reassigned device does not.
        const position = old?.data?.group === group.key ? old.position : {x: x + (index % columns) * 350, y: rowY + 75 + Math.floor(index / columns) * 170};
        nodes.push({id, type: 'agent', position: {...position}, width: NODE_WIDTH, height: 120, data: {agent, group: group.key, dimmed: false, neighbor: false}, selected: Boolean(old?.selected), deletable: false, connectable: false, ariaLabel: `${agent.name}, ${agent.kind}, ${owner(agent)}, ${deviceName(agent)}, ${agent.status}`});
      });
      groupX += widths[column] + 130;
    }
    rowY += rowHeight;
  }
  return nodes;
}

export function decorate(model, nodes, selectedId) {
  const selected = model.agents.some(agent => agentNodeId(agent.id) === selectedId) ? selectedId : null;
  const neighbors = new Set();
  for (const edge of model.connections) {
    if (agentNodeId(edge.source) === selected) neighbors.add(agentNodeId(edge.target));
    if (agentNodeId(edge.target) === selected) neighbors.add(agentNodeId(edge.source));
  }
  return {
    selectedId: selected,
    nodes: nodes.map(node => node.type === 'agent' ? {...node, selected: node.id === selected, data: {...node.data, dimmed: Boolean(selected && node.id !== selected && !neighbors.has(node.id)), neighbor: neighbors.has(node.id)}} : node),
    edges: model.connections.map(edge => {
      const connected = selected && (agentNodeId(edge.source) === selected || agentNodeId(edge.target) === selected);
      const color = connected ? '#c7c6bc' : '#696962';
      return {
        id: `message:${edge.id}`, source: agentNodeId(edge.source), target: agentNodeId(edge.target), type: 'bezier',
        sourceHandle: 'out', targetHandle: 'in', animated: false, selectable: false, focusable: false, deletable: false, reconnectable: false,
        markerEnd: {type: 'arrowclosed', color, width: 14, height: 14},
        label: selected && !connected ? undefined : String(edge.message_count), labelStyle: `fill:${connected ? '#edede7' : '#a1a199'};font-size:10px;font-family:inherit`,
        labelBgStyle: 'fill:#121211;fill-opacity:0.95', labelBgPadding: [6, 3], labelBgBorderRadius: 3,
        style: `stroke:${color};stroke-width:${connected ? 2 : 1};opacity:${selected && !connected ? .08 : connected ? .95 : .35}`,
        ariaLabel: `${edge.message_count} retained messages from ${edge.source} to ${edge.target}`,
      };
    }),
  };
}

export function agentDetails(model, nodeId) {
  const agent = model.agents.find(value => agentNodeId(value.id) === nodeId);
  if (!agent) return null;
  const incoming = model.connections.filter(edge => edge.target === agent.id);
  const outgoing = model.connections.filter(edge => edge.source === agent.id);
  const peers = new Set([...incoming.map(edge => edge.source), ...outgoing.map(edge => edge.target)]);
  peers.delete(agent.id);
  return {agent, incoming, outgoing, peerCount: peers.size, received: incoming.reduce((sum, edge) => sum + edge.message_count, 0), sent: outgoing.reduce((sum, edge) => sum + edge.message_count, 0)};
}
