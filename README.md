# HOMI

Persistent agent identities, communication, and execution. HOMI combines the working
Communicate session router and bus with durable identities and mailboxes. Claude
Code and Codex are the supported native adapters. Terminal execution uses tmux.

**Release candidate:** the 0.3.0 distribution is being qualified. The installation
commands below become the public channel when its tagged release and tap are published.
See [release qualification](docs/RELEASING.md).

## Install

```sh
brew install nonlocally/tap/homi
homi setup --claude --codex
homi doctor
```

Enable only the clients you use. Installing the package does not replace terminal,
shell, editor, or Git configuration. Provider authentication remains your own.
The `communicate` command and existing plugin identities remain compatible.

A versioned runtime archive is also available through the release channel for
macOS and Linux. It includes Node dependencies and requires no source checkout or
npm account. See [installation](docs/INSTALL.md).

## Four capabilities

```sh
homi start
homi claim researcher
homi send researcher 'Review the experiment when you resume.'
homi inbox researcher
```

Claiming an identity creates an addressed mailbox; it does not start a model.
An identity retains mail while its execution is stopped. To start a model in a seat:

```sh
homi spawn researcher --cli claude --cwd "$PWD"
homi agents
homi seat ls
```

Execution, identity, and stored state have separate lifetimes. A pane number is an
execution handle within its device and tmux server, not a global identity or an
isolation boundary. See [CLI and delivery semantics](docs/CLI.md).

## Agent integrations and communication

After setup, open a new supported Claude or Codex session so it loads the plugin.
Ask it to find agents, register on your configured bus, send a message, or create
an execution. Existing Communicate skills and MCP tools remain compatible.

Bus registration publishes the exact current session, separately from creating a
persistent mailbox:

```sh
homi bus status --no-start --json
homi bus register
homi bus agents --json
homi bus dashboard --open
```

Use your configured hub, explicitly choose local/self-hosted operation, or connect
using a scoped invitation. Public software does not grant access to a private bus.
The bus retains its graph, dashboard, human inbox, membership checks and receipts.
See [bus operations](docs/BUSES.md).

Existing native routes remain available through `homi native agents` and
`homi native route NAME MESSAGE`. These address spaces stay explicit so a failed
lookup cannot silently target another agent. Queue acceptance is not proof that
the model consumed a message.

## Optional terminal and mesh profile

```sh
homi profile preview --terminal --mesh
```

The optional profile carries selected Ghostty/tmux navigation, layouts, launch
helpers, and configured mesh access from Anu. It separates packaged defaults from
private overrides and records ownership for reversible changes. tmux, fzf, jq,
Ghostty and Tailscale are capability-specific dependencies, not prerequisites for
every user. See [profiles](docs/PROFILES.md).

Browser, phone, research and application workspaces are outside the core release.
Personal hosts, credentials and account services are never public defaults.

## Development and license

The implementation continues Communicate under [nonlocally/HOMI](https://github.com/nonlocally/HOMI).
See [the repository briefing](AGENTS.md) and [release qualification](docs/RELEASING.md).
[MIT](LICENSE), with applicable bundled third-party notices retained.
