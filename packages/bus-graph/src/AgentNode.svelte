<script>
  import { Handle, Position } from '@xyflow/svelte';
  import { deviceName, owner } from './model.mjs';
  let { data, selected } = $props();
</script>

<div class="cg-agent" class:is-selected={selected} class:is-dimmed={data.dimmed} class:is-neighbor={data.neighbor}>
  <Handle type="target" position={Position.Left} id="in" isConnectable={false} />
  <div class="cg-node-top"><span class="cg-kind">{data.agent.kind}</span><span class="cg-node-status"><i class:available={data.agent.status === 'live' || data.agent.status === 'reachable'} class:queued={data.agent.status === 'queuable' || data.agent.status === 'queueable'}></i>{data.agent.status}</span></div>
  <div class="cg-node-name" title={data.agent.name}>{data.agent.name}</div>
  <div class="cg-node-owner">{owner(data.agent)}</div>
  <div class="cg-node-device" title={deviceName(data.agent)}>{deviceName(data.agent)}</div>
  <Handle type="source" position={Position.Right} id="out" isConnectable={false} />
</div>
