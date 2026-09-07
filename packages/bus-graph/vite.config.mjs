import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';
import { fileURLToPath } from 'node:url';
import { runtimeLicenses } from './licenses.mjs';

export default defineConfig({
  plugins: [svelte(), runtimeLicenses()],
  define: {'process.env.NODE_ENV': JSON.stringify('production')},
  build: {
    target: 'es2022',
    outDir: fileURLToPath(new URL('../../lib/assets/', import.meta.url)),
    emptyOutDir: false,
    sourcemap: false,
    lib: {
      entry: fileURLToPath(new URL('./src/main.mjs', import.meta.url)),
      name: 'CommunicateGraph',
      formats: ['iife'],
      fileName: () => 'bus-graph.js',
      cssFileName: 'bus-graph',
    },
  },
});
