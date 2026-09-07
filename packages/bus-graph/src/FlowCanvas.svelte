<script>
  import { SvelteFlow, Controls, MiniMap, Background, BackgroundVariant, useSvelteFlow } from '@xyflow/svelte';
  import AgentNode from './AgentNode.svelte';
  import DeviceNode from './DeviceNode.svelte';
  import CommunityNode from './CommunityNode.svelte';
  let { nodes = $bindable([]), edges = $bindable([]), onselect, ondragstart, ondragstop } = $props();
  const nodeTypes = {agent: AgentNode, device: DeviceNode, community: CommunityNode};
  const flow = useSvelteFlow();
  export function fit() { return flow.fitView({padding: 0.2, maxZoom: 1, duration: 180}); }
</script>
<SvelteFlow bind:nodes bind:edges {nodeTypes} fitView fitViewOptions={{padding: 0.2, maxZoom: 1}} colorMode="dark" minZoom={0.18} maxZoom={1.8} nodesConnectable={false} edgesReconnectable={false} edgesFocusable={false} deleteKey={null} multiSelectionKey={null} selectionKey={null} selectNodesOnDrag={false} preventScrolling={true} panOnScroll={false} zoomOnScroll={true} zoomOnPinch={true} zoomActivationKey={['Control', 'Meta']} panOnDrag={[0, 1, 2]} onnodedragstart={ondragstart} onnodedragstop={ondragstop} onnodeclick={({node}) => onselect(node.id)} onselectionchange={({nodes: selection}) => { const node = selection.find(node => node.type === 'agent' || node.type === 'community'); if (node) onselect(node.id); }} onpaneclick={() => onselect(null)}>
  <Background variant={BackgroundVariant.Dots} gap={24} size={0.8} color="#383835" bgColor="#090909" />
  <Controls showLock={false} fitViewOptions={{padding: 0.2, maxZoom: 1, duration: 180}} position="bottom-left" />
  <MiniMap position="bottom-right" width={136} height={92} bgColor="#111110" maskColor="rgba(0,0,0,.52)" maskStrokeColor="#77776e" maskStrokeWidth={1} nodeColor={node => node.type === 'device' ? 'transparent' : node.selected ? '#d0cfc2' : '#606058'} nodeStrokeColor="transparent" nodeBorderRadius={3} pannable zoomable ariaLabel="Agent graph overview. Drag to pan the canvas." />
</SvelteFlow>
