import {readFileSync, readdirSync} from 'node:fs';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';

// This pinned release declares ISC but omits a separate notice. Keep the
// provenance and standard license text checked in; upgrades must be reviewed.
const declaredNotices = new Map([
  ['java-random@0.4.0', new URL('./licenses/java-random-0.4.0.txt', import.meta.url)],
]);

// Inspect the emitted runtime, not package.json's build-only dependencies. The
// bundle's MIT/BSD notices ship with it even when minification removes comments.
export function runtimeLicenses() {
  return {
    name: 'bus-graph-runtime-licenses',
    generateBundle(_options, bundle) {
      const packages = new Map();
      for (const output of Object.values(bundle)) {
        if (output.type !== 'chunk') continue;
        output.code = '/*! Runtime license notices: bus-graph.LICENSES.txt */\n' + output.code;
        for (const moduleId of Object.keys(output.modules)) {
          const match = moduleId.match(/^(.*\/node_modules\/((?:@[^/]+\/)?[^/]+))\//);
          if (match) packages.set(match[2], match[1]);
        }
      }
      const sections = ['Third-party software included in bus-graph.js and bus-graph.css.\nGenerated from the modules present in the runtime bundle.'];
      for (const [name, directory] of [...packages].sort(([a], [b]) => a.localeCompare(b))) {
        const manifest = JSON.parse(readFileSync(join(directory, 'package.json'), 'utf8'));
        const files = readdirSync(directory).filter(file => /^licen[cs]e(?:\..*)?$/i.test(file)).sort();
        const fallback = declaredNotices.get(`${name}@${manifest.version}`);
        if (!files.length && !fallback) this.error(`Missing runtime license for ${name}`);
        const notices = files.length ? files.map(file => readFileSync(join(directory, file), 'utf8').trim()).join('\n\n')
          : readFileSync(fileURLToPath(fallback), 'utf8').trim();
        sections.push(`${name} ${manifest.version}\n${'='.repeat(name.length + manifest.version.length + 1)}\n${notices.replaceAll('\r\n', '\n')}`);
      }
      this.emitFile({type: 'asset', fileName: 'bus-graph.LICENSES.txt', source: sections.join('\n\n\n') + '\n'});
    },
  };
}
