<script>
  import { BaseEdge } from '@xyflow/svelte';
  let { id, data, label, labelStyle = '', markerStart, markerEnd, style, interactionWidth } = $props();
  const route = $derived(data.flowRoute);
  // Graph presentation offsets Bezier labels; orthogonal routes already supply
  // distinct reciprocal label positions, so that old CSS offset must not stack.
  const textStyle = $derived(labelStyle.replace(/;translate:[^;]*/g, '') + ';background:#121211;border-radius:3px;padding:3px 6px;pointer-events:none');
</script>

<BaseEdge {id} path={route.path} labelX={route.labelX} labelY={route.labelY} {label} labelStyle={textStyle} {markerStart} {markerEnd} {style} {interactionWidth} />
