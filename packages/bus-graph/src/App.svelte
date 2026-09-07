<script>
  import { tick, onDestroy } from 'svelte';
  import { SvelteFlowProvider } from '@xyflow/svelte';
  import FlowCanvas from './FlowCanvas.svelte';
  import { snapshotModel, layoutNodes, canDeferRefresh, decorate, agentDetails, agentNodeId, owner, deviceName } from './model.mjs';

  let model = $state.raw(snapshotModel());
  let nodes = $state.raw([]);
  let edges = $state.raw([]);
  let selectedId = $state(null);
  let scope = '';
  let epoch = $state(0);
  let canvas = $state();
  let expanded = $state(false);
  let standalone = $state(false);
  let dragging = false;
  let pendingRefresh = null;
  let expandButton = $state();
  let fitFrame = 0;
  let disposed = false;
  const cancelFit = () => { cancelAnimationFrame(fitFrame); fitFrame = 0; };
  onDestroy(() => { disposed = true; pendingRefresh = null; cancelFit(); });
  const details = $derived(agentDetails(model, selectedId));
  const messageCount = $derived(model.connections.reduce((sum, edge) => sum + edge.message_count, 0));
  const names = $derived(new Map(model.agents.map(agent => [agent.id, agent.name])));
  const receiptStates = ['accepted', 'leased', 'delivered', 'queued', 'failed', 'expired', 'cancelled'];

  function applySelection(id) {
    const decorated = decorate(model, nodes, id);
    selectedId = decorated.selectedId;
    nodes = decorated.nodes;
    edges = decorated.edges;
  }
  function select(id) { if (id !== selectedId) applySelection(id); }
  async function toggleExpanded() {
    expanded = !expanded;
    await tick();
    // ResizeObserver updates Svelte Flow's dimensions on the next frame.
    cancelFit();
    if (!disposed) fitFrame = requestAnimationFrame(() => {
      fitFrame = requestAnimationFrame(() => { fitFrame = 0; if (!disposed) canvas?.fit(); });
    });
  }
  function handleKey(event) {
    if (expanded && event.key === 'Escape') {
      event.preventDefault();
      expanded = false;
      expandButton?.focus();
    }
  }
  function formatTime(seconds) { return seconds ? new Date(seconds * 1000).toLocaleString(undefined, {dateStyle: 'medium', timeStyle: 'short'}) : 'Unknown'; }
  function statusSummary(edge) { return receiptStates.filter(state => edge.status_counts[state]).map(state => `${edge.status_counts[state]} ${state}`).join(' · '); }
  async function resetLayout() {
    cancelFit();
    nodes = layoutNodes(model);
    applySelection(selectedId);
    await tick();
    canvas?.fit();
  }

  export function update(snapshot) {
    const nextScope = typeof snapshot?.scope === 'string' ? snapshot.scope : '';
    const next = snapshotModel(snapshot);
    standalone = snapshot?.standalone === true;
    if (standalone) expanded = false;
    const changed = nextScope !== scope;
    if (dragging && !changed && canDeferRefresh(model, next)) {
      // Keep only the latest sanitized snapshot; the host continues polling.
      pendingRefresh = {model: next, scope: nextScope};
      return;
    }
    if (dragging) {
      // Recreate the canvas to cancel its in-flight pointer operation before
      // applying a scope change, removal, or narrower permission boundary.
      dragging = false;
      epoch += 1;
    }
    pendingRefresh = null;
    applyRefresh(next, nextScope);
  }
  function applyRefresh(next, nextScope) {
    const changed = nextScope !== scope;
    if (changed) { expanded = false; cancelFit(); }
    if (changed || (!model.agents.length && next.agents.length)) epoch += 1;
    scope = nextScope;
    nodes = layoutNodes(next, changed ? [] : nodes);
    model = next;
    applySelection(changed ? null : selectedId);
  }
  function dragStart() { dragging = true; }
  function dragStop() {
    dragging = false;
    if (!pendingRefresh) return;
    const pending = pendingRefresh;
    pendingRefresh = null;
    applyRefresh(pending.model, pending.scope);
  }
  export function clear() {
    cancelFit();
    expanded = false;
    dragging = false;
    pendingRefresh = null;
    scope = '';
    selectedId = null;
    nodes = [];
    edges = [];
    model = snapshotModel();
    epoch += 1;
  }
</script>

<svelte:window onkeydown={handleKey} />
<section class="cg-shell" class:cg-expanded={expanded} class:cg-standalone={standalone} aria-label="Agent connections graph">
  <header class="cg-toolbar">
    <div class="cg-counts"><strong>{model.agents.length}</strong> agents <span>·</span><strong>{model.groups.length}</strong> {model.groups.length === 1 ? 'device' : 'devices'}<span>·</span><strong>{model.connections.length}</strong> directed links</div>
    <div class="cg-actions"><span class="cg-visual-label">Visual layout</span><button type="button" onclick={() => canvas?.fit()} disabled={!model.agents.length}>Fit</button><button type="button" onclick={resetLayout} disabled={!model.agents.length}>Reset layout</button>{#if !standalone}<button type="button" bind:this={expandButton} onclick={toggleExpanded} aria-expanded={expanded} aria-label={expanded ? 'Collapse graph canvas' : 'Expand graph canvas'}>{expanded ? 'Collapse' : 'Expand'}</button>{/if}</div>
  </header>
  <div class="cg-workspace" class:cg-with-inspector={details !== null}>
    <div class="cg-canvas" aria-label="Draggable graph of agents grouped by device">
      {#key epoch}
        <SvelteFlowProvider><FlowCanvas bind:this={canvas} bind:nodes bind:edges onselect={select} ondragstart={dragStart} ondragstop={dragStop} /></SvelteFlowProvider>
      {/key}
      {#if !model.agents.length}
        <div class="cg-empty"><strong>No agents in this view</strong><span>Choose another bus or adjust the filters.</span></div>
      {:else if !model.observed}
        <div class="cg-canvas-note" role="status">Agent locations are available. This broker does not provide message connections yet.</div>
      {:else if !model.connections.length}
        <div class="cg-canvas-note" role="status">No retained messages connect the agents in this view. Shared bus membership alone does not draw a link.</div>
      {/if}
    </div>
    {#if details}
      <aside class="cg-inspector nowheel nopan" aria-label="Selected agent details">
        <div class="cg-inspector-header"><span>Selected agent</span><button class="cg-close" type="button" onclick={() => select(null)} aria-label="Close agent details">×</button></div>
        <h3>{details.agent.name}</h3>
        <p class="cg-detail-kind">{details.agent.kind} <span>·</span> {details.agent.status}</p>
        {#if details.agent.description}<p class="cg-description">{details.agent.description}</p>{/if}
        <dl class="cg-detail-list">
          <div><dt>Owner</dt><dd>{owner(details.agent)}</dd></div>
          <div><dt>Device</dt><dd>{deviceName(details.agent)}</dd></div>
          {#each Object.entries(details.agent.device_metadata) as [key, value]}
            <div><dt>{key.replaceAll('_', ' ')}</dt><dd>{value}</dd></div>
          {/each}
          <div><dt>Last seen</dt><dd>{formatTime(details.agent.last_seen)}</dd></div>
          <div><dt>Buses</dt><dd>{details.agent.buses.join(', ') || '—'}</dd></div>
          <div><dt>Agent ID</dt><dd class="cg-mono">{details.agent.id}</dd></div>
          <div><dt>Device ID</dt><dd class="cg-mono">{details.agent.device_id || 'Unknown'}</dd></div>
        </dl>
        <div class="cg-metrics"><div><strong>{details.sent}</strong><span>sent</span></div><div><strong>{details.received}</strong><span>received</span></div><div><strong>{details.peerCount}</strong><span>peers</span></div></div>
        <p class="cg-metric-note">Retained messages between visible agents.</p>
        {#each [{label: 'Outgoing', edges: details.outgoing, target: true}, {label: 'Incoming', edges: details.incoming, target: false}] as direction}
          <section class="cg-connections" aria-label={direction.label + ' connections'}>
            <h4>{direction.label}<span>{direction.edges.length}</span></h4>
            {#each direction.edges as edge (edge.id)}
              <div class="cg-connection"><button type="button" onclick={() => select(agentNodeId(direction.target ? edge.target : edge.source))}><span>{names.get(direction.target ? edge.target : edge.source)}</span><strong>{edge.message_count}</strong></button><small>{statusSummary(edge)}</small><small>Last sent {formatTime(edge.last_sent_at)}</small></div>
            {:else}<p class="cg-no-connections">No retained messages</p>{/each}
          </section>
        {/each}
      </aside>
    {/if}
  </div>
  <footer class="cg-footer"><span>Drag agents to arrange · Drag background to pan · Scroll or pinch to zoom</span><span>{messageCount} retained messages · Positions change this view only</span></footer>
</section>
