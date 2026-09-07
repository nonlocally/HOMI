import {readFileSync, readdirSync} from 'node:fs';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';

// This pinned release declares ISC but omits a separate notice. Keep the
// provenance and standard license text checked in; upgrades must be reviewed.
const declaredNotices = new Map([
  ['java-random@0.4.0', new URL('./licenses/java-random-0.4.0.txt', import.meta.url)],
]);

// npm's published gitHead identifies the source corresponding to this exact
// artifact. Keep its own copyright notices as well as the supplied agreement;
// the minifier may remove those comments from the emitted JavaScript.
const sourceProvenance = new Map([
  ['elkjs@0.11.0', {
    source: 'https://github.com/kieler/elkjs/tree/37e798513db6e88b2e205b8239ed4e387d5f24d0',
    upstream: 'https://github.com/eclipse-elk/elk',
    archive: 'https://registry.npmjs.org/elkjs/-/elkjs-0.11.0.tgz',
    file: 'lib/elk.bundled.js',
  }],
]);

// Inspect the emitted runtime, not package.json's build-only dependencies. The
// bundle's runtime notices ship with it even when minification removes comments.
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
        const provenance = sourceProvenance.get(`${name}@${manifest.version}`);
        let sourceNotice = '';
        if (provenance) {
          const source = readFileSync(join(directory, provenance.file), 'utf8');
          const copyrightNotices = [...new Set((source.match(/\/\*[\s\S]*?\*\//g) || [])
            .filter(block => /copyright|SPDX-License-Identifier/i.test(block)))];
          if (!copyrightNotices.length) this.error(`Missing upstream source notices for ${name}`);
          sourceNotice = `Source code for ${name} is available under ${manifest.license}:\n${provenance.source}\nUpstream ELK Java source: ${provenance.upstream}\nPublished package: ${provenance.archive}\nNo upstream source changes; bundled and minified for this application.\n\n${copyrightNotices.join('\n\n')}\n\n`;
        }
        sections.push(`${name} ${manifest.version}\n${'='.repeat(name.length + manifest.version.length + 1)}\n${(sourceNotice + notices).replaceAll('\r\n', '\n').replace(/[ \t]+$/gm, '')}`);
      }
      this.emitFile({type: 'asset', fileName: 'bus-graph.LICENSES.txt', source: sections.join('\n\n\n') + '\n'});
    },
  };
}
