# Bus graph

A locally bundled Svelte Flow view of the existing authorized bus snapshot. The
host dashboard owns authentication, fetching, filters, and permissions; this
widget makes no requests and stores nothing in cookies or browser storage.

The initial layout groups agents by owner and enrolled device, with a balanced
three-column grid for larger devices. Nodes can be moved freely, and the viewport
supports pan, zoom, fit, and a minimap. The standalone graph page fills its host
without a surrounding box. The embedded view retains Expand; Escape collapses
it and returns focus to its toolbar button. Drag the background or scroll with
two fingers to pan. Pinch, or hold Ctrl/Command while scrolling, to zoom.
Selecting an agent opens an overlay without resizing or moving the canvas. It shows its identity,
device, current status, and incoming/outgoing counts, while highlighting its
neighbors. These are visual operations only; they do not issue agent commands,
change bus membership, or create message edges.

## Build

From this directory:

```sh
npm ci --ignore-scripts
npm test
npm run build
```

Node 22.12+ (or Node 20.19+) is required by Vite. The exact runtime and build
versions are locked in `package-lock.json`. Build emits checked-in
`lib/assets/bus-graph.js` (IIFE, includes Svelte and Svelte Flow) and
`lib/assets/bus-graph.css` from source. The build also emits
`lib/assets/bus-graph.LICENSES.txt` with license notices for every bundled runtime
dependency; it must be distributed alongside the assets. There are no CDN imports, fonts, or separate
runtime chunks. The dashboard does not require Node to run.

Upstream: [Svelte Flow](https://svelteflow.dev/learn),
[@xyflow/svelte](https://github.com/xyflow/xyflow), licensed MIT. The standard
Svelte Flow attribution is retained.

## Host API

Load the stylesheet and script from the broker's authenticated static routes.
Mount into an empty element inside the dashboard's graph view:

```js
const graph = window.CommunicateGraph.mount(element);
graph.update({scope: `${serverId}:${selectedBus}`, agents, buses, standalone: true});
graph.clear();
graph.destroy();
```

- `agents` is the currently visible, filtered roster. Each agent has `id`, `name`,
  `kind`, `user`, `device`, `device_id`, `device_metadata`, `status`, `last_seen`,
  `description`, and permitted `buses` names.
- `buses` contains only selected/permitted snapshot buses. Each has `name`,
  `agents`, and optional `graph: {basis: "retained_bus_messages", edges,
  retention_seconds}`. Each directed edge has `source`, `target`,
  `message_count`, `last_sent_at`, and `status_counts`.
- An edge is shown only when both endpoint agents are currently visible and
  occur in that edge's source bus roster. Counts aggregate across selected buses
  for the same directed pair. Membership does not imply traffic.
- Broker receipt retention is based on terminal receipt updates, not a strict
  “messages sent in the last seven days” window. Labels say *retained messages*.
  Nonterminal messages may be older. Message contents are never needed.
- Stable `scope` preserves node positions, selection, and viewport across data
  refresh. Use a different scope for another broker/bus selection. Removed
  agents lose positions and inspector state immediately; no out-of-view cache
  is kept. Device reassignment also resets that agent's position.
- `standalone: true` fills the mount element's explicitly sized height, removes
  the border and Expand control, and uses compact controls. Omit it for the
  embedded dashboard view. The inspector overlays the right edge, or the bottom
  on narrow screens, without changing the canvas dimensions.
- During a node drag, routine refreshes wait for pointer release and only the
  latest sanitized snapshot is kept. Any scope change, removed agent or message
  link, narrower bus membership, device reassignment, or `clear()` applies
  immediately and cancels the drag; a pending snapshot cannot restore it.
- A changed `scope` resets the layout, selection, and viewport. Call `clear()`
  whenever authorization is lost or the session is being replaced; it removes
  nodes, edges, details, and in-memory visual state synchronously.
- `destroy()` is idempotent and removes the component; later update calls are
  ignored. Recreate it with `mount()` when needed.

The pure-model tests cover edge authorization boundaries, message aggregation,
large same-device layouts, state pruning, and selection neighbors. Dashboard
integration tests cover actual Svelte Flow interaction and backend authorization.
