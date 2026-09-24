# Workstation source provenance

Selected shell, Ghostty, tmux, and mesh helpers are adapted from Anu/HOMI,
commit `80d3c86`, authored by Aadarsh Agarwal and contributors. Imported at the
owner's direction for the MIT-licensed HOMI consolidation on 2026-09-24.

Donor paths: `config/bash/fns/{tmux,dynlayout,agentlaunch,mesh}`,
`config/tmux/{tmux.conf,tile.sh,session-bar.sh,pane-yank.sh}`, and
`config/ghostty/config`. The source checkout was read only and is not a runtime
dependency. The original helper names and snapshot formats are retained where
useful. Personal service defaults, browser/research/chat applications, global
editor/Git/agent settings, and swarm distribution are not imported.

Adaptations separate package/config/state paths, select shell modules explicitly,
use native agent authentication by default, preserve SSH configuration, and add
ownership-aware configuration installation. See the repository MIT license.
