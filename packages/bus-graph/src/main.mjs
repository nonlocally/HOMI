import { mount as mountSvelte, unmount, flushSync } from 'svelte';
import App from './App.svelte';
import '@xyflow/svelte/dist/style.css';
import './style.css';

/** Mount into an empty, sized element. All data comes from the host dashboard. */
export function mount(target) {
  if (!(target instanceof HTMLElement)) throw new TypeError('CommunicateGraph.mount needs an HTML element');
  const app = mountSvelte(App, {target});
  flushSync();
  let destroyed = false;
  return Object.freeze({
    update(snapshot) { if (!destroyed) flushSync(() => app.update(snapshot)); },
    clear() { if (!destroyed) flushSync(() => app.clear()); },
    destroy() {
      if (destroyed) return;
      flushSync(() => app.clear());
      destroyed = true;
      unmount(app);
    },
  });
}
