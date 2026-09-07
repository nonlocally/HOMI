<script>
  import { Handle, Position } from '@xyflow/svelte';
  import { communityColor } from './model.mjs';
  let { data, selected } = $props();
</script>
<div class="cg-agent cg-community-node has-community" class:is-selected={selected} class:is-focused={data.focused} class:is-dimmed={data.dimmed} class:is-neighbor={data.neighbor} style:--cg-community-color={communityColor(data.community.colorIndex)}>
  <Handle type="target" position={Position.Left} id="in" isConnectable={false} />
  <div class="cg-node-top"><span class="cg-kind">Community</span><span class="cg-node-status">{data.community.members.length} agents</span></div>
  <div class="cg-node-name">{data.community.label}</div>
  <div class="cg-community-internal" data-internal-messages={data.internalMessages}>{data.internalMessages} internal messages</div>
  <div class="cg-node-device">{data.sent} sent · {data.received} received outside</div>
  <Handle type="source" position={Position.Right} id="out" isConnectable={false} />
  <Handle class="cg-direction-handle" type="target" position={Position.Right} id="in-right" isConnectable={false} />
  <Handle class="cg-direction-handle" type="source" position={Position.Left} id="out-left" isConnectable={false} />
  <Handle class="cg-direction-handle" type="target" position={Position.Top} id="in-top" isConnectable={false} />
  <Handle class="cg-direction-handle" type="source" position={Position.Top} id="out-top" isConnectable={false} />
  <Handle class="cg-direction-handle" type="target" position={Position.Bottom} id="in-bottom" isConnectable={false} />
  <Handle class="cg-direction-handle" type="source" position={Position.Bottom} id="out-bottom" isConnectable={false} />
</div>
