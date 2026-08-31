# @aadarwal/communicate

An agent router: message any Claude Code or Codex agent by **name**, locally or
across devices — plus the plugin that teaches agents how to use the bus.

```sh
npx -y @aadarwal/communicate setup        # install for Claude Code + Codex
```

`setup`:
- stabilizes the payload to `~/.local/share/communicate/<version>/` (symlink `current`);
- **Claude Code**: adds two keys to `~/.claude/settings.json` (timestamped backup
  first): `extraKnownMarketplaces.communicate` → the stabilized directory
  marketplace, and `enabledPlugins["communicate@communicate"]: true`. New
  sessions get 5 skills, the `/agents` command, the `communicate` CLI on PATH,
  and the MCP tools.
- **Codex**: runs `codex plugin marketplace add` + `codex plugin add
  communicate@communicate` when the `codex` CLI is present (otherwise prints
  the exact lines). New threads see the skills and MCP tools.

Other verbs: `doctor` (verify install) · `serve` (stdio MCP server: agents_list,
whereis, route, send, codex_queue, codex_ask, status) · `setup --uninstall`
(reverse; `--purge` also removes the payload) · anything else passes through to
the bundled `communicate` CLI (`agents`, `route`, `send`, `codex …`, `claude
bridge …`, `wake …`).

**Requirements:** macOS/Linux · bash · python3 · ssh (for cross-device verbs) ·
Codex CLI ≥ 0.151 for `codex queue`.

This package ships the **communicate layer only**. Durable identities,
mailboxes, store-and-forward, and federation live in the homi plane of the full
repo: https://github.com/aadarwal/communicate
