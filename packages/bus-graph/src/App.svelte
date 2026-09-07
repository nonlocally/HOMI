<script>
  import { tick, onDestroy } from 'svelte';
  import { SvelteFlowProvider } from '@xyflow/svelte';
  import FlowCanvas from './FlowCanvas.svelte';
  import { snapshotModel, layoutNodes, spectralNodes, graphPresentation, communityColor, communityNodeId, canDeferRefresh, agentDetails, agentNodeId, owner, deviceName, deviceKey } from './model.mjs';
  import { analyzeGraph, graphSignature } from './analysis.mjs';

  let model = $state.raw(snapshotModel());
  let analysis = $state.raw(analyzeGraph(snapshotModel()));
  let nodes = $state.raw([]);
  let edges = $state.raw([]);
  let baseNodes = [];
  let selectedId = $state(null);
  let layoutMode = $state('spectral');
  let panel = $state(null);
  let focusedCommunity = $state(null);
  let showCommunities = $state(true);
  let stale = $state(false);
  let collapsed = $state.raw(new Set());
  let pinned = $state.raw(new Set());
  let aggregatePositions = new Map();
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
  const activeCommunity = $derived(analysis.communities.find(group => group.id === focusedCommunity) || null);
  const receiptStates = ['accepted', 'leased', 'delivered', 'queued', 'failed', 'expired', 'cancelled'];

  function capturePositions() {
    const current = new Map(nodes.filter(node => node.type === 'agent').map(node => [node.id, node.position]));
    baseNodes = baseNodes.map(node => current.has(node.id) ? {...node, position: {...current.get(node.id)}} : node);
    for (const node of nodes) if (node.type === 'community') aggregatePositions.set(node.id, {...node.position});
  }
  function present() {
    const view = graphPresentation(model, baseNodes, analysis, {collapsed, pinned, aggregatePositions, showCommunities, selectedId, focusedCommunity});
    selectedId = view.selectedId;
    nodes = view.nodes;
    edges = view.edges;
  }
  function select(id) {
    const group = nodes.find(node => node.id === id && node.type === 'community')?.data.community;
    if (group) { if (focusedCommunity !== group.id || panel !== 'communities') focusGroup(group.id); return; }
    if (id === selectedId && !focusedCommunity) return;
    capturePositions();
    selectedId = id;
    focusedCommunity = null;
    if (id) panel = null;
    present();
  }
  function focusGroup(id) {
    capturePositions();
    focusedCommunity = focusedCommunity === id ? null : id;
    selectedId = null;
    panel = 'communities';
    present();
  }
  function togglePanel(value) {
    capturePositions();
    panel = panel === value ? null : value;
    selectedId = null;
    focusedCommunity = null;
    present();
  }
  function toggleCommunityLayer() {
    capturePositions();
    showCommunities = !showCommunities;
    if (!showCommunities) { collapsed = new Set(); aggregatePositions.clear(); focusedCommunity = null; }
    present();
  }
  function toggleGroup(group) {
    capturePositions();
    const next = new Set(collapsed);
    if (next.has(group.id)) {
      const position = aggregatePositions.get(communityNodeId(group.id));
      const members = baseNodes.filter(node => node.type === 'agent' && group.members.includes(node.data.agent.id));
      if (position && members.length) {
        const dx = position.x - members.reduce((sum, node) => sum + node.position.x, 0) / members.length;
        const dy = position.y - members.reduce((sum, node) => sum + node.position.y, 0) / members.length;
        baseNodes = baseNodes.map(node => node.type === 'agent' && group.members.includes(node.data.agent.id) ? {...node, position: {x: node.position.x + dx, y: node.position.y + dy}} : node);
      }
      next.delete(group.id);
      aggregatePositions.delete(communityNodeId(group.id));
    } else { next.add(group.id); selectedId = null; }
    collapsed = next;
    present();
  }
  function openMember(id) {
    const group = analysis.communities.find(value => value.members.includes(id));
    if (group && collapsed.has(group.id)) toggleGroup(group);
    select(agentNodeId(id));
  }
  function togglePin(id) {
    capturePositions();
    const next = new Set(pinned);
    next.has(id) ? next.delete(id) : next.add(id);
    pinned = next;
    present();
  }
  function pruneGroups() {
    const permitted = new Set(analysis.communities.map(group => group.id));
    collapsed = new Set([...collapsed].filter(id => permitted.has(id)));
    aggregatePositions = new Map([...aggregatePositions].filter(([id]) => analysis.communities.some(group => communityNodeId(group.id) === id)));
    if (!permitted.has(focusedCommunity)) focusedCommunity = null;
  }
  function place(previous = []) { return layoutMode === 'spectral' ? spectralNodes(model, analysis, previous) : layoutNodes(model, previous); }
  async function arrange(recompute = true) {
    cancelFit();
    capturePositions();
    if (recompute) { analysis = analyzeGraph(model); stale = false; pruneGroups(); }
    baseNodes = place(baseNodes.filter(node => node.type === 'agent' && pinned.has(node.data.agent.id)));
    aggregatePositions.clear();
    present();
    await tick();
    canvas?.fit();
  }
  function chooseLayout(event) { layoutMode = event.currentTarget.value; arrange(false); }
  async function toggleExpanded() {
    expanded = !expanded;
    await tick();
    cancelFit();
    if (!disposed) fitFrame = requestAnimationFrame(() => {
      fitFrame = requestAnimationFrame(() => { fitFrame = 0; if (!disposed) canvas?.fit(); });
    });
  }
  function handleKey(event) {
    if (event.key === 'Escape' && (panel || details)) { select(null); panel = null; }
    else if (expanded && event.key === 'Escape') { event.preventDefault(); expanded = false; expandButton?.focus(); }
  }
  function formatTime(seconds) { return seconds ? new Date(seconds * 1000).toLocaleString(undefined, {dateStyle: 'medium', timeStyle: 'short'}) : 'Unknown'; }
  function formatNumber(value) { return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(4) : 'Not defined'; }
  function formatEigenvalue(value) {
    if (!Number.isFinite(value)) return 'Not available';
    return value !== 0 && Math.abs(value) < 1e-4 ? value.toExponential(3) : Number(value.toPrecision(6)).toString();
  }
  function statusSummary(edge) { return receiptStates.filter(state => edge.status_counts[state]).map(state => `${edge.status_counts[state]} ${state}`).join(' · '); }

  export function update(snapshot) {
    const nextScope = typeof snapshot?.scope === 'string' ? snapshot.scope : '';
    const next = snapshotModel(snapshot);
    standalone = snapshot?.standalone === true;
    if (standalone) expanded = false;
    const changed = nextScope !== scope;
    if (dragging && !changed && canDeferRefresh(model, next)) { pendingRefresh = {model: next, scope: nextScope}; return; }
    if (dragging) { dragging = false; epoch += 1; }
    pendingRefresh = null;
    applyRefresh(next, nextScope);
  }
  function applyRefresh(next, nextScope) {
    const changed = nextScope !== scope;
    const narrower = !canDeferRefresh(model, next);
    const identityChanged = model.agents.length !== next.agents.length || next.agents.some(agent => !model.agents.some(old => old.id === agent.id));
    capturePositions();
    if (changed) {
      expanded = false; cancelFit(); selectedId = null; focusedCommunity = null;
      panel = null; collapsed = new Set(); pinned = new Set(); aggregatePositions.clear();
    }
    if (changed || (!model.agents.length && next.agents.length)) epoch += 1;
    // Pins and positions never cross a reassigned device identity.
    pinned = new Set([...pinned].filter(id => next.agents.some(agent => agent.id === id && model.agents.some(old => old.id === id && deviceKey(old) === deviceKey(agent)))));
    scope = nextScope;
    model = next;
    if (changed || narrower || identityChanged) {
      analysis = analyzeGraph(model); stale = false; pruneGroups();
      // The previous aggregate position may have encoded removed membership.
      if (narrower) { aggregatePositions.clear(); collapsed = new Set(); focusedCommunity = null; }
    } else stale = graphSignature(model) !== analysis.signature;
    baseNodes = place(changed ? [] : baseNodes);
    present();
  }
  function dragStart() { dragging = true; }
  function dragStop() {
    dragging = false;
    capturePositions();
    if (!pendingRefresh) return;
    const pending = pendingRefresh;
    pendingRefresh = null;
    applyRefresh(pending.model, pending.scope);
  }
  export function clear() {
    cancelFit(); expanded = false; dragging = false; pendingRefresh = null;
    scope = ''; selectedId = null; focusedCommunity = null; panel = null;
    collapsed = new Set(); pinned = new Set(); aggregatePositions.clear();
    baseNodes = []; nodes = []; edges = []; model = snapshotModel();
    analysis = analyzeGraph(model); stale = false; epoch += 1;
  }
</script>

<svelte:window onkeydown={handleKey} />
<section class="cg-shell" class:cg-expanded={expanded} class:cg-standalone={standalone} aria-label="Agent connections graph">
  <header class="cg-toolbar">
    <div class="cg-counts"><strong>{model.agents.length}</strong> agents <span>·</span><strong>{model.groups.length}</strong> {model.groups.length === 1 ? 'device' : 'devices'}<span>·</span><strong>{model.connections.length}</strong> directed links</div>
    <div class="cg-actions">
      <select aria-label="Graph layout" value={layoutMode} onchange={chooseLayout}><option value="spectral">Spectral</option><option value="devices">Devices</option></select>
      <button type="button" onclick={() => togglePanel('communities')} aria-expanded={panel === 'communities'}>Communities</button>
      <button type="button" onclick={() => togglePanel('analysis')} aria-expanded={panel === 'analysis'}>Analysis</button>
      <button type="button" onclick={() => canvas?.fit()} disabled={!model.agents.length}>Fit</button>
      <button type="button" onclick={() => arrange()} disabled={!model.agents.length} title="Recalculate layout and communities; pinned agents stay in place">Recompute</button>
      {#if !standalone}<button type="button" bind:this={expandButton} onclick={toggleExpanded} aria-expanded={expanded} aria-label={expanded ? 'Collapse graph canvas' : 'Expand graph canvas'}>{expanded ? 'Collapse' : 'Expand'}</button>{/if}
    </div>
  </header>
  <div class="cg-workspace" class:cg-with-inspector={details !== null}>
    <div class="cg-canvas" aria-label="Draggable graph of agent relationships" data-layout={layoutMode}>
      {#key epoch}
        <SvelteFlowProvider><FlowCanvas bind:this={canvas} bind:nodes bind:edges onselect={select} ondragstart={dragStart} ondragstop={dragStop} /></SvelteFlowProvider>
      {/key}
      {#if !model.agents.length}
        <div class="cg-empty"><strong>No agents in this view</strong><span>Choose another bus or adjust the filters.</span></div>
      {:else if stale}
        <div class="cg-canvas-note cg-stale-note" role="status">Traffic changed · Recompute to update layout and groups.</div>
      {:else if analysis.warning}
        <div class="cg-canvas-note" role="status">{analysis.warning}</div>
      {:else if !model.observed}
        <div class="cg-canvas-note" role="status">Agent locations are available. This broker does not provide message connections yet.</div>
      {:else if !model.connections.length}
        <div class="cg-canvas-note" role="status">No retained messages connect the agents in this view. Shared bus membership alone does not draw a link.</div>
      {/if}
    </div>
    {#if panel === 'communities'}
      <aside class="cg-inspector cg-community-panel nowheel nopan" aria-label="Communities">
        <div class="cg-inspector-header"><span>Communities</span><button class="cg-close" type="button" onclick={() => togglePanel('communities')} aria-label="Close communities">×</button></div>
        <h3>Patterns in communication</h3>
        <p class="cg-panel-description">Groups reflect retained message relationships, not assigned teams. Collapsing and moving a group changes this view only.</p>
        <label class="cg-layer-toggle"><input type="checkbox" checked={showCommunities} onchange={toggleCommunityLayer} /> Show community layer</label>
        {#if stale}<p class="cg-analysis-stale" role="status">New traffic is available. Recompute to refresh these groups.</p>{/if}
        <div class="cg-community-list">
          {#each analysis.communities as group (group.id)}
            <section class="cg-community-row" class:is-focused={focusedCommunity === group.id} data-community={group.id}>
              <div class="cg-community-row-heading"><button type="button" class="cg-community-focus" aria-label={'Focus ' + group.label} aria-pressed={focusedCommunity === group.id} onclick={() => focusGroup(group.id)}><i style:background={communityColor(group.colorIndex)}></i><span>{group.label}</span><small>{group.members.length}</small></button><button type="button" disabled={!showCommunities || group.members.length < 2} aria-label={(collapsed.has(group.id) ? 'Expand ' : 'Collapse ') + group.label} onclick={() => toggleGroup(group)}>{collapsed.has(group.id) ? 'Expand' : 'Collapse'}</button></div>
              {#if focusedCommunity === group.id}<ul class="cg-community-members">{#each group.members as id (id)}<li><button type="button" onclick={() => openMember(id)}>{names.get(id)}</button></li>{/each}</ul>{/if}
            </section>
          {:else}<p class="cg-no-connections">No agents in this view.</p>{/each}
        </div>
        {#if activeCommunity}<button class="cg-clear-focus" type="button" onclick={() => focusGroup(activeCommunity.id)}>Clear focus</button>{/if}
      </aside>
    {:else if panel === 'analysis'}
      <aside class="cg-inspector cg-analysis-panel nowheel nopan" aria-label="Graph analysis">
        <div class="cg-inspector-header"><span>Graph analysis</span><button class="cg-close" type="button" onclick={() => togglePanel('analysis')} aria-label="Close analysis">×</button></div>
        <h3>A mathematical view</h3>
        <p class="cg-panel-description">Calculated from retained messages between the agents permitted in this view. Message direction remains visible in the arrows.</p>
        {#if stale}<p class="cg-analysis-stale" role="status">Traffic changed. Values below describe the last computation; Recompute updates them.</p>{/if}
        {#if analysis.warning}<p class="cg-analysis-stale">{analysis.warning}</p>{/if}
        <dl class="cg-analysis-methods">
          <div><dt>Layout</dt><dd>{analysis.method.layout}</dd></div>
          <div><dt>Community method</dt><dd>{analysis.method.community}</dd></div>
          <div><dt>Relationship weight</dt><dd>{analysis.method.weighting}</dd></div>
        </dl>
        <p class="cg-equation">L = I − D⁻¹ᐟ² W D⁻¹ᐟ²</p>
        <p class="cg-panel-description">The two lowest nonzero modes set each connected component's coordinates. Spacing separates overlapping cards; component packing is visual. Dragging changes the drawing, not the calculation.</p>
        <dl class="cg-detail-list cg-analysis-stats">
          <div><dt>Agents</dt><dd>{analysis.stats.agents}</dd></div>
          <div><dt>Relationships</dt><dd>{analysis.stats.relationships}</dd></div>
          <div><dt>Components</dt><dd>{analysis.stats.components}</dd></div>
          <div><dt>Isolates</dt><dd>{analysis.stats.isolates}</dd></div>
          <div><dt>Communities</dt><dd>{analysis.stats.communities}</dd></div>
          <div><dt>Modularity</dt><dd data-analysis="modularity">{formatNumber(analysis.stats.modularity)}</dd></div>
        </dl>
        <p class="cg-panel-description">Weighted modularity measures separation under this grouping and weighting. It does not measure agent quality or prove a team structure.</p>
        <h4 class="cg-analysis-heading">Nonzero eigenvalues</h4>
        {#each analysis.components as component, index}
          <details class="cg-component"><summary>Component {index + 1} · {component.ids.length} {component.ids.length === 1 ? 'agent' : 'agents'}</summary><p class="cg-mono" data-analysis="eigenvalues">{component.eigenvalues.length ? component.eigenvalues.map(formatEigenvalue).join(', ') : 'No nonzero modes'}</p><ul class="cg-component-members">{#each component.ids as id}<li>{names.get(id)}</li>{/each}</ul></details>
        {/each}
      </aside>
    {/if}
    {#if details}
      <aside class="cg-inspector nowheel nopan" aria-label="Selected agent details">
        <div class="cg-inspector-header"><span>Selected agent</span><button class="cg-close" type="button" onclick={() => select(null)} aria-label="Close agent details">×</button></div>
        <h3>{details.agent.name}</h3>
        <div class="cg-detail-actions"><button type="button" aria-pressed={pinned.has(details.agent.id)} onclick={() => togglePin(details.agent.id)}>{pinned.has(details.agent.id) ? 'Unpin agent' : 'Pin agent'}</button><small>Keep position on Recompute</small></div>
        <p class="cg-detail-kind">{details.agent.kind} <span>·</span> {details.agent.status}</p>
        {#if details.agent.description}<p class="cg-description">{details.agent.description}</p>{/if}
        <dl class="cg-detail-list">
          {#if showCommunities && analysis.communityByAgent.has(details.agent.id)}<div><dt>Community</dt><dd><button class="cg-text-button" type="button" onclick={() => focusGroup(analysis.communityByAgent.get(details.agent.id))}>{analysis.communities.find(group => group.id === analysis.communityByAgent.get(details.agent.id))?.label}</button></dd></div>{/if}
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
              <div class="cg-connection"><button type="button" onclick={() => openMember(direction.target ? edge.target : edge.source)}><span>{names.get(direction.target ? edge.target : edge.source)}</span><strong>{edge.message_count}</strong></button><small>{statusSummary(edge)}</small><small>Last sent {formatTime(edge.last_sent_at)}</small></div>
            {:else}<p class="cg-no-connections">No retained messages</p>{/each}
          </section>
        {/each}
      </aside>
    {/if}
  </div>
  <footer class="cg-footer"><span>Drag agents to arrange · Drag background to pan · Scroll or pinch to zoom</span><span>{messageCount} retained messages · Positions and groups change this view only</span></footer>
</section>
