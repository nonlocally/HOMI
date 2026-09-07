<script>
  import { tick, onDestroy } from 'svelte';
  import { SvelteFlowProvider } from '@xyflow/svelte';
  import FlowCanvas from './FlowCanvas.svelte';
  import { snapshotModel, layoutNodes, spectralNodes, flowNodes, placeConductor, graphPresentation, communityColor, communityNodeId, canDeferRefresh, agentDetails, agentNodeId, owner, deviceName, deviceKey } from './model.mjs';
  import { analyzeGraph, graphSignature } from './analysis.mjs';
  import { inferFlow, layoutFlow, validateParentOverride, parentOverrideOptions } from './flow.mjs';
  import { clearFlowRoutingCache } from './flow-routing.mjs';

  let { onchat, oninbox, onopenwebui } = $props();
  let chatConfig = $state.raw({enabled: false, buses: [], unread: 0, openwebui: []});
  let chatBus = $state('');

  let model = $state.raw(snapshotModel());
  let analysis = $state.raw(analyzeGraph(snapshotModel()));
  let nodes = $state.raw([]);
  let edges = $state.raw([]);
  let baseNodes = [];
  let selectedId = $state(null);
  let layoutMode = $state('flow');
  let flowAnalysis = $state.raw(inferFlow(snapshotModel()));
  let flowDirection = $state('RIGHT');
  let flowRootId = $state(null);
  let parentOverrides = $state.raw(new Map());
  let parentError = $state('');
  let routingWarning = $state('');
  let layoutInterrupted = $state(false);
  let flowPending = $state(false);
  let layoutGeneration = 0;
  let panel = $state(null);
  let focusedCommunity = $state(null);
  let showCommunities = $state(true);
  let stale = $state(false);
  let collapsed = $state.raw(new Set());
  let pinned = $state.raw(new Set());
  let conductorId = $state(null);
  let conductorHome = null;
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
  onDestroy(() => { disposed = true; invalidateLayout(); pendingRefresh = null; cancelFit(); clearFlowRoutingCache(); });
  const details = $derived(agentDetails(model, selectedId));
  const chatBuses = $derived(chatConfig.enabled && details ? details.agent.buses.filter(bus => chatConfig.buses.includes(bus)) : []);
  const chosenChatBus = $derived(chatBuses.includes(chatBus) ? chatBus : chatBuses[0] || '');
  const canOpenWebUI = $derived(details && chatConfig.openwebui.some(entry => entry.bus === chosenChatBus && entry.agent === details.agent.id));
  const webuiURL = $derived(details && chosenChatBus ? 'https://mit.nonlocally.org/?models=' + encodeURIComponent('communicate_bus.' + chosenChatBus + '--' + details.agent.id) : '');
  const messageCount = $derived(model.connections.reduce((sum, edge) => sum + edge.message_count, 0));
  const names = $derived(new Map(model.agents.map(agent => [agent.id, agent.name])));
  const activeCommunity = $derived(analysis.communities.find(group => group.id === focusedCommunity) || null);
  const placementRoot = $derived(layoutMode === 'flow' ? flowAnalysis.rootId : conductorId);
  const parentChoices = $derived.by(() => {
    if (!details || layoutMode !== 'flow') return [];
    const id = details.agent.id;
    const options = parentOverrideOptions(model, flowAnalysis, id);
    return model.agents.filter(agent => options.has(agent.id)).sort((a, b) => a.name.localeCompare(b.name) || a.id.localeCompare(b.id)).map(agent => ({...agent, ...options.get(agent.id)}));
  });
  const automaticParent = $derived.by(() => {
    if (!details || layoutMode !== 'flow') return null;
    const overrides = new Map(parentOverrides);
    overrides.delete(details.agent.id);
    return inferFlow(model, {conductorId: flowRootId, parentOverrides: overrides}).parentByAgent.get(details.agent.id) || null;
  });
  const receiptStates = ['accepted', 'leased', 'delivered', 'queued', 'failed', 'expired', 'cancelled'];

  function capturePositions() {
    const current = new Map(nodes.filter(node => node.type === 'agent').map(node => [node.id, node.position]));
    baseNodes = baseNodes.map(node => current.has(node.id) ? {...node, position: {...current.get(node.id)}} : node);
    for (const node of nodes) if (node.type === 'community') aggregatePositions.set(node.id, {...node.position});
  }
  function present() {
    const view = graphPresentation(model, baseNodes, analysis, {collapsed, pinned, aggregatePositions, showCommunities, selectedId, focusedCommunity, conductorId: layoutMode === 'flow' ? null : conductorId, flowRootId: layoutMode === 'flow' ? flowAnalysis.rootId : null, flowDirection: layoutMode === 'flow' ? flowDirection : null});
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
    parentError = '';
    focusedCommunity = null;
    if (id) panel = null;
    present();
  }
  function focusGroup(id) {
    capturePositions();
    parentError = '';
    focusedCommunity = focusedCommunity === id ? null : id;
    selectedId = null;
    panel = 'communities';
    present();
  }
  function togglePanel(value) {
    capturePositions();
    parentError = '';
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
      const members = baseNodes.filter(node => node.type === 'agent' && group.members.includes(node.data.agent.id) && node.data.agent.id !== placementRoot);
      if (position && members.length) {
        const dx = position.x - members.reduce((sum, node) => sum + node.position.x, 0) / members.length;
        const dy = position.y - members.reduce((sum, node) => sum + node.position.y, 0) / members.length;
        baseNodes = baseNodes.map(node => node.type === 'agent' && group.members.includes(node.data.agent.id) && node.data.agent.id !== placementRoot ? {...node, position: {x: node.position.x + dx, y: node.position.y + dy}} : node);
      }
      next.delete(group.id);
      aggregatePositions.delete(communityNodeId(group.id));
    } else { next.add(group.id); selectedId = null; }
    collapsed = next;
    present();
  }
  function openMember(id) {
    const group = analysis.communities.find(value => value.members.includes(id));
    if (group && collapsed.has(group.id) && id !== placementRoot) toggleGroup(group);
    select(agentNodeId(id));
  }
  function togglePin(id) {
    capturePositions();
    const next = new Set(pinned);
    next.has(id) ? next.delete(id) : next.add(id);
    pinned = next;
    if (flowPending) arrange(false);
    else present();
  }
  function restoreConductor() {
    if (conductorHome && !pinned.has(conductorId)) {
      baseNodes = baseNodes.map(node => node.type === 'agent' && node.data.agent.id === conductorId && node.data.group === conductorHome.group ? {...node, position: {...conductorHome.position}} : node);
    }
    conductorId = null;
    conductorHome = null;
  }
  function rememberConductorHome() {
    const node = baseNodes.find(node => node.type === 'agent' && node.data.agent.id === conductorId);
    conductorHome = node ? {position: {...node.position}, group: node.data.group} : null;
  }
  async function chooseConductor(id) {
    capturePositions();
    const clear = id === conductorId;
    restoreConductor();
    if (!clear && model.agents.some(agent => agent.id === id)) {
      conductorId = id;
      rememberConductorHome();
      baseNodes = placeConductor(baseNodes, conductorId, pinned, model.connections);
    }
    present();
    await tick();
    canvas?.fit();
  }
  function pruneGroups() {
    const permitted = new Set(analysis.communities.map(group => group.id));
    collapsed = new Set([...collapsed].filter(id => permitted.has(id)));
    aggregatePositions = new Map([...aggregatePositions].filter(([id]) => analysis.communities.some(group => communityNodeId(group.id) === id)));
    if (!permitted.has(focusedCommunity)) focusedCommunity = null;
  }
  function place(previous = []) {
    return layoutMode === 'flow' ? flowNodes(model, flowAnalysis, previous) : layoutMode === 'spectral' ? spectralNodes(model, analysis, previous) : layoutNodes(model, previous);
  }
  function invalidateLayout() { layoutGeneration += 1; flowPending = false; }
  function inferCurrentFlow() {
    flowAnalysis = inferFlow(model, {conductorId: flowRootId, parentOverrides});
    parentOverrides = new Map(flowAnalysis.acceptedOverrides);
  }
  async function computeFlow(previous = [], fit = true) {
    const generation = ++layoutGeneration;
    const expectedScope = scope;
    const isCurrent = () => !disposed && generation === layoutGeneration && layoutMode === 'flow' && scope === expectedScope && !dragging;
    flowPending = true;
    layoutInterrupted = false;
    try {
      // Metadata-only polls may proceed while ELK works. Changed traffic is
      // recalculated before commit; narrower scopes invalidate this generation.
      let result;
      do {
        const input = model;
        const signature = graphSignature(input);
        result = await layoutFlow(input, {conductorId: flowRootId, parentOverrides, direction: flowDirection, isCurrent});
        if (!isCurrent()) return;
        if (signature === graphSignature(model)) break;
      } while (true);
      flowAnalysis = result;
      parentOverrides = new Map(result.acceptedOverrides);
      baseNodes = flowNodes(model, result, previous);
      aggregatePositions.clear();
      flowPending = false;
      stale = graphSignature(model) !== analysis.signature;
      present();
      await tick();
      if (fit && !disposed && generation === layoutGeneration && layoutMode === 'flow') canvas?.fit();
    } catch {
      if (disposed || generation !== layoutGeneration) return;
      flowPending = false;
      // Retain the currently permitted drawing, never an old request snapshot.
      flowAnalysis = {...flowAnalysis, warning: 'Flow layout could not be calculated. Existing positions are retained; try Recompute.'};
      present();
    }
  }
  async function arrange(recompute = true) {
    cancelFit();
    invalidateLayout();
    layoutInterrupted = false;
    capturePositions();
    if (recompute) { analysis = analyzeGraph(model); stale = false; pruneGroups(); }
    const previous = baseNodes.filter(node => node.type === 'agent' && pinned.has(node.data.agent.id));
    if (layoutMode === 'flow') {
      inferCurrentFlow();
      present();
      await computeFlow(previous);
      return;
    }
    baseNodes = place(previous);
    rememberConductorHome();
    baseNodes = placeConductor(baseNodes, conductorId, pinned, model.connections);
    aggregatePositions.clear();
    present();
    const generation = layoutGeneration;
    await tick();
    if (!disposed && generation === layoutGeneration) canvas?.fit();
  }
  function chooseLayout(event) { layoutMode = event.currentTarget.value; parentError = ''; arrange(false); }
  function chooseDirection(event) { flowDirection = event.currentTarget.value; parentError = ''; arrange(false); }
  function chooseFlowRoot(id) {
    flowRootId = id === flowRootId ? null : id;
    parentError = '';
    arrange(false);
  }
  function chooseParent(event, id) {
    const parent = event.currentTarget.value;
    const next = new Map(parentOverrides);
    if (parent) {
      const verdict = validateParentOverride(model, flowAnalysis, id, parent);
      if (!verdict.valid) {
        event.currentTarget.value = parentOverrides.get(id) || '';
        parentError = verdict.reason;
        return;
      }
      next.set(id, parent);
    } else next.delete(id);
    parentOverrides = next;
    parentError = '';
    arrange(false);
  }
  async function toggleExpanded() {
    expanded = !expanded;
    await tick();
    cancelFit();
    if (!disposed) fitFrame = requestAnimationFrame(() => {
      fitFrame = requestAnimationFrame(() => { fitFrame = 0; if (!disposed) canvas?.fit(); });
    });
  }
  function handleKey(event) {
    // A host dialog (including chat) owns Escape while it is open.
    if (document.querySelector('dialog[open]')) return;
    if (event.key === 'Escape' && (panel || details)) { select(null); panel = null; }
    else if (expanded && event.key === 'Escape') { event.preventDefault(); expanded = false; expandButton?.focus(); }
  }
  function formatTime(seconds) { return seconds ? new Date(seconds * 1000).toLocaleString(undefined, {dateStyle: 'medium', timeStyle: 'short'}) : 'Unknown'; }
  function formatNumber(value) { return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(4) : 'Not defined'; }
  function formatEigenvalue(value) {
    if (!Number.isFinite(value)) return 'Not available';
    return value !== 0 && Math.abs(value) < 1e-4 ? value.toExponential(3) : Number(value.toPrecision(6)).toString();
  }
  function routingStatus(warning) { routingWarning = warning; }
  function statusSummary(edge) { return receiptStates.filter(state => edge.status_counts[state]).map(state => `${edge.status_counts[state]} ${state}`).join(' · '); }

  export function update(snapshot) {
    chatConfig = {enabled: snapshot?.chat?.enabled === true, buses: Array.isArray(snapshot?.chat?.buses) ? snapshot.chat.buses.filter(bus => typeof bus === 'string') : [], unread: Number.isSafeInteger(snapshot?.chat?.unread) ? Math.max(0, snapshot.chat.unread) : 0, openwebui: Array.isArray(snapshot?.chat?.openwebui) ? snapshot.chat.openwebui.filter(entry => entry && typeof entry.bus === 'string' && typeof entry.agent === 'string') : []};
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
    const requiresLayout = changed || narrower || identityChanged;
    if (changed || narrower) clearFlowRoutingCache();
    if (requiresLayout) { invalidateLayout(); parentError = ''; layoutInterrupted = false; }
    if (changed) {
      expanded = false; cancelFit(); selectedId = null; focusedCommunity = null;
      panel = null; collapsed = new Set(); pinned = new Set(); conductorId = null; conductorHome = null; flowRootId = null; parentOverrides = new Map(); parentError = ''; aggregatePositions.clear();
    }
    if (changed || (!model.agents.length && next.agents.length)) epoch += 1;
    // Pins and positions never cross a reassigned device identity.
    pinned = new Set([...pinned].filter(id => next.agents.some(agent => agent.id === id && model.agents.some(old => old.id === id && deviceKey(old) === deviceKey(agent)))));
    if (conductorId && !next.agents.some(agent => agent.id === conductorId && model.agents.some(old => old.id === conductorId && deviceKey(old) === deviceKey(agent)))) { conductorId = null; conductorHome = null; }
    const retained = new Set(next.agents.filter(agent => model.agents.some(old => old.id === agent.id && deviceKey(old) === deviceKey(agent))).map(agent => agent.id));
    if (flowRootId && !retained.has(flowRootId)) flowRootId = null;
    parentOverrides = new Map([...parentOverrides].filter(([child, parent]) => retained.has(child) && retained.has(parent)));
    scope = nextScope;
    model = next;
    if (changed || narrower || identityChanged) {
      analysis = analyzeGraph(model); stale = false; pruneGroups();
      // The previous aggregate position may have encoded removed membership.
      if (narrower) { aggregatePositions.clear(); collapsed = new Set(); focusedCommunity = null; }
    } else stale = graphSignature(model) !== analysis.signature;
    const previous = changed ? [] : baseNodes;
    if (requiresLayout) inferCurrentFlow();
    baseNodes = place(previous);
    present();
    if (requiresLayout && layoutMode === 'flow' && model.agents.length) computeFlow(previous, changed || !previous.length);
  }
  function dragStart() { dragging = true; if (flowPending) layoutInterrupted = true; invalidateLayout(); }
  function dragStop() {
    dragging = false;
    capturePositions();
    if (!pendingRefresh) return;
    const pending = pendingRefresh;
    pendingRefresh = null;
    applyRefresh(pending.model, pending.scope);
  }
  export function clear() {
    chatConfig = {enabled: false, buses: [], unread: 0, openwebui: []}; chatBus = '';
    clearFlowRoutingCache();
    cancelFit(); invalidateLayout(); expanded = false; dragging = false; pendingRefresh = null;
    scope = ''; selectedId = null; focusedCommunity = null; panel = null; conductorId = null; conductorHome = null; flowRootId = null; parentOverrides = new Map(); parentError = ''; flowAnalysis = inferFlow(snapshotModel());
    collapsed = new Set(); pinned = new Set(); aggregatePositions.clear();
    baseNodes = []; nodes = []; edges = []; model = snapshotModel(); routingWarning = ''; layoutInterrupted = false;
    analysis = analyzeGraph(model); stale = false; epoch += 1;
  }
</script>

<svelte:window onkeydown={handleKey} />
<section class="cg-shell" class:cg-expanded={expanded} class:cg-standalone={standalone} aria-label="Agent connections graph">
  <header class="cg-toolbar">
    <div class="cg-counts"><strong>{model.agents.length}</strong> agents <span>·</span><strong>{model.groups.length}</strong> {model.groups.length === 1 ? 'device' : 'devices'}<span>·</span><strong>{model.connections.length}</strong> directed links{#if layoutMode === 'flow' && flowAnalysis.rootId}<span>·</span><span class="cg-conductor-summary" title={names.get(flowAnalysis.rootId)}><span>{flowRootId ? 'Flow root' : 'Suggested root'}: {names.get(flowAnalysis.rootId)}</span>{#if flowRootId}<button type="button" onclick={() => chooseFlowRoot(flowRootId)} aria-label="Use suggested flow root">×</button>{/if}</span>{:else if conductorId}<span>·</span><span class="cg-conductor-summary" title={names.get(conductorId)}><span>Conductor: {names.get(conductorId)}</span><button type="button" onclick={() => chooseConductor(conductorId)} aria-label="Clear conductor placement">×</button></span>{/if}</div>
    <div class="cg-actions">
      {#if chatConfig.enabled && oninbox}<button type="button" onclick={oninbox} aria-label={'Inbox' + (chatConfig.unread ? ', ' + chatConfig.unread + ' unread replies' : '')}>Inbox{#if chatConfig.unread}<span class="cg-unread">{chatConfig.unread}</span>{/if}</button>{/if}
      <select aria-label="Graph layout" value={layoutMode} onchange={chooseLayout}><option value="flow">Flow</option><option value="spectral">Spectral</option><option value="devices">Devices</option></select>
      {#if layoutMode === 'flow'}<select aria-label="Flow direction" value={flowDirection} onchange={chooseDirection}><option value="RIGHT">Left → right</option><option value="DOWN">Top → bottom</option></select>{/if}
      <button type="button" onclick={() => togglePanel('communities')} aria-expanded={panel === 'communities'}>Communities</button>
      <button type="button" onclick={() => togglePanel('analysis')} aria-expanded={panel === 'analysis'}>Analysis</button>
      <button type="button" onclick={() => canvas?.fit()} disabled={!model.agents.length}>Fit</button>
      <button type="button" onclick={() => arrange()} disabled={!model.agents.length} title="Recalculate layout and communities; pinned agents stay in place">Recompute</button>
      {#if !standalone}<button type="button" bind:this={expandButton} onclick={toggleExpanded} aria-expanded={expanded} aria-label={expanded ? 'Collapse graph canvas' : 'Expand graph canvas'}>{expanded ? 'Collapse' : 'Expand'}</button>{/if}
    </div>
  </header>
  <div class="cg-workspace" class:cg-with-inspector={details !== null}>
    <div class="cg-canvas" aria-label="Draggable graph of agent relationships" data-layout={layoutMode} data-flow-pending={flowPending ? "true" : "false"}>
      {#key epoch}
        <SvelteFlowProvider><FlowCanvas bind:this={canvas} bind:nodes bind:edges onselect={select} ondragstart={dragStart} ondragstop={dragStop} flowLayout={layoutMode === 'flow'} direction={flowDirection} onroutingstatus={routingStatus} /></SvelteFlowProvider>
      {/key}
      {#if !model.agents.length}
        <div class="cg-empty"><strong>No agents in this view</strong><span>Choose another bus or adjust the filters.</span></div>
      {:else if flowPending}
        <div class="cg-canvas-note" role="status">Arranging communication flow…</div>
      {:else if layoutMode === 'flow' && layoutInterrupted}
        <div class="cg-canvas-note" role="status">Layout interrupted by dragging · Recompute to arrange the graph.</div>
      {:else if layoutMode === 'flow' && flowAnalysis.warning}
        <div class="cg-canvas-note" role="status">{flowAnalysis.warning}</div>
      {:else if layoutMode === 'flow' && routingWarning}
        <div class="cg-canvas-note" role="status">{routingWarning}</div>
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
        <p class="cg-panel-description">Groups reflect retained message relationships, not assigned teams. Collapsing and moving a group changes this view only.{#if placementRoot} The {layoutMode === 'flow' ? 'Flow root' : 'chosen conductor'} stays visible outside its collapsed group; community membership is unchanged.{/if}</p>
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
          <div><dt>Layout</dt><dd>{layoutMode === 'flow' ? flowAnalysis.method || 'Connectivity branches · layered flow' : layoutMode === 'devices' ? 'Device groups · grid' : analysis.method.layout}</dd></div>
          <div><dt>Community method</dt><dd>{analysis.method.community}</dd></div>
          <div><dt>Relationship weight</dt><dd>{analysis.method.weighting}</dd></div>
        </dl>
        {#if layoutMode === 'flow'}
          <p class="cg-panel-description">Flow suggests a starting agent from connectivity, separates branches around it, and places each branch in successive levels. Strong connections influence the branch arrangement; every retained message direction and count stays visible. Return and cross-branch messages need not follow the levels.</p>
          <p class="cg-panel-description">This is a suggested communication hierarchy, not an assigned role or a task dependency. Select an agent to choose the root or correct its layout parent. Dragging and pinning change this view only.</p>
          <dl class="cg-detail-list"><div><dt>Root</dt><dd>{names.get(flowAnalysis.rootId) || 'None'} ({flowRootId ? 'chosen' : 'suggested'})</dd></div><div><dt>Direction</dt><dd>{flowDirection === 'RIGHT' ? 'Left → right' : 'Top → bottom'}</dd></div><div><dt>Branches</dt><dd>{flowAnalysis.branches.length}</dd></div><div><dt>Corrections</dt><dd>{parentOverrides.size}</dd></div></dl>
          <h4 class="cg-analysis-heading">Spectral reference</h4>
        {/if}
        <p class="cg-equation">L = I − D⁻¹ᐟ² W D⁻¹ᐟ²</p>
        <p class="cg-panel-description">The two lowest nonzero modes set the starting coordinates. Weighted graph distances refine the spacing, with an anchor to that spectral arrangement and room for each card. Component packing is visual. Dragging changes the drawing, not the calculation.{#if layoutMode !== 'flow' && conductorId} The chosen conductor is placed apart for this view; its mathematical coordinates and community are unchanged.{/if}</p>
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
        {#if chatBuses.length}
          <div class="cg-chat-actions">
            {#if chatBuses.length > 1}<label class="cg-parent-picker"><span>Chat on bus</span><select aria-label="Chat on bus" value={chosenChatBus} onchange={event => chatBus = event.currentTarget.value}>{#each chatBuses as bus}<option value={bus}>{bus}</option>{/each}</select></label>{/if}
            <div class="cg-detail-actions">
              {#if onchat}<button type="button" class="cg-chat-primary" onclick={() => onchat(details.agent, chosenChatBus)}>Chat</button>{/if}
              {#if canOpenWebUI}{#if onopenwebui}<button type="button" onclick={() => onopenwebui(details.agent, chosenChatBus)}>Open in Nonlocally ↗</button>{:else}<a href={webuiURL} target="_blank" rel="noopener noreferrer">Open in Nonlocally ↗</a>{/if}{/if}
            </div>
            <p class="cg-metric-note">Message this exact session on {chosenChatBus}.</p>
          </div>
        {/if}
        <div class="cg-detail-actions"><button type="button" aria-pressed={pinned.has(details.agent.id)} onclick={() => togglePin(details.agent.id)}>{pinned.has(details.agent.id) ? 'Unpin agent' : 'Pin agent'}</button><small>Keep position on Recompute</small></div>
        {#if layoutMode === 'flow'}
          <div class="cg-detail-actions cg-conductor-action"><button type="button" aria-pressed={flowRootId === details.agent.id} onclick={() => chooseFlowRoot(details.agent.id)}>{flowRootId === details.agent.id ? 'Use suggested flow root' : 'Use as flow root'}</button><small>{flowAnalysis.rootId === details.agent.id && !flowRootId ? 'Suggested from visible connections. ' : ''}Visual organization only; this assigns no authority.</small></div>
          <label class="cg-parent-picker"><span>Layout parent</span><select aria-label="Layout parent" value={parentOverrides.get(details.agent.id) || ''} onchange={event => chooseParent(event, details.agent.id)} disabled={flowAnalysis.roots.includes(details.agent.id)}><option value="">Automatic{automaticParent ? ': ' + names.get(automaticParent) : ' (root)'}</option>{#each parentChoices as peer (peer.id)}<option value={peer.id} disabled={!peer.valid}>{peer.name} · {peer.id.slice(0, 8)}{!peer.valid ? ' — would create a cycle' : ''}</option>{/each}</select><small>{flowAnalysis.roots.includes(details.agent.id) ? 'A root has no layout parent.' : 'Choose a connected agent to correct this branch. Automatic resets the correction.'}</small></label>
          {#if parentError}<p class="cg-analysis-stale" role="alert">{parentError}</p>{/if}
        {:else}
        <div class="cg-detail-actions cg-conductor-action"><button type="button" aria-pressed={conductorId === details.agent.id} onclick={() => chooseConductor(details.agent.id)}>{conductorId === details.agent.id ? 'Clear conductor placement' : 'Set apart as conductor'}</button><small>Visual placement only.{pinned.has(details.agent.id) ? ' Pinned position takes precedence.' : ''}</small></div>
        {/if}
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
