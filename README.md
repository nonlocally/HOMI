# HOMI

**Let Claude Code and Codex work together.**

HOMI connects your coding agents so they can divide a task, ask each other
questions, review each other's work, and bring the findings back to you.
Start in the agent you already use and describe what you want done.

Use it to get a second opinion on a change, investigate different parts of a
problem in parallel, or compare approaches before choosing one. Collaborators
can run on your computer or on other devices you explicitly connect.

## Install

```sh
brew install nonlocally/tap/homi
homi setup
homi doctor
```

The setup guide lets you choose Claude Code, Codex, or both. It shows the plan
before making changes and offers to install missing clients and tools, including
tmux for running collaborators. Existing provider clients are kept.

Terminal configuration is optional. On macOS, you can also choose Ghostty and
its configured font. Keep using zsh or Bash: setup does not change your interactive
shell. Signing in is a separate choice, handled by each provider's own login flow.

Prefer a release archive, need a server installation, or want explicit setup
flags? See the [installation guide](docs/INSTALL.md). HOMI runs on macOS and
Linux. The archive needs Node.js 20+ and Bash to start setup; Homebrew supplies
the core runtime dependencies.

## Give your agents a task

After setup and provider sign-in, open a fresh Claude Code CLI or Codex CLI
session in your project. Restart Claude Code or start a new Codex thread if
it was already running, so it loads the HOMI plugin.

Then ask for work in plain language. For example:

### Investigate a failure

> Create a Claude collaborator called investigator. Ask it to trace why the
> checkout test fails intermittently while you inspect the recent changes.
> Compare your findings and bring me the most likely cause, with evidence.

### Compare options before building

> Ask one collaborator to compare SQLite and PostgreSQL for this application's
> deployment and workload. Ask another to inspect the code for migration costs.
> Have them challenge each other's assumptions, then bring me a recommendation
> with the tradeoffs and unresolved questions.

### Get an independent review

> Ask the Codex reviewer to examine this change for correctness and missing
> tests. Discuss any disagreements with it, then give me the findings that still
> need attention before I merge.

Choose collaborators whose clients you have installed and authenticated. You
can use one provider or combine Claude Code and Codex. HOMI supplies the
communication and execution tools; the models do the investigation and review.

Your agent handles finding or creating collaborators, sending the work,
checking progress, and collecting replies. You do not need to learn identity
or messaging commands to use it. The [quickstart](docs/QUICKSTART.md) walks
through setup and your first collaboration.

## What you can do

- **Divide work.** Give collaborators separate questions or parts of a project,
  then ask your coordinating agent to combine the results.
- **Get another perspective.** Send an existing agent a question, request a
  review, or have two agents compare their conclusions.
- **Keep named collaborators.** HOMI retains agent identities and messages
  independently of a running model session. Stopping an execution does not
  erase them.
- **See what is happening.** Inspect an agent's terminal or ask to open the
  communication dashboard to see registered agents and conversations.
- **Use another machine.** Connect a device you control or join a shared bus
  through its owner's invitation, then work with the agents you can reach.

A stored message or queued request is not a completed task. Your agent should
bring back an actual reply or explain what prevented it. Saved identities and
messages do not preserve a model process's memory or automatically resume every
client; see [delivery behavior](docs/CLI.md) when diagnosing a stalled task.

## Make the terminal comfortable

The optional terminal profile adds navigation, tiling and familiar launch
shortcuts: `cx` / `cxx` for Claude Code and `cdx` / `cdxx` for Codex.
The `cxx` and `cdxx` shortcuts retain their full-auto permission settings.
They work from zsh or Bash; Bash runs their implementation internally.

You can choose the profile during setup or preview it later:

```sh
homi profile preview --terminal --mesh
```

The mesh profile adds device discovery and SSH helpers. Snapshots, account
helpers and contained execution are separate optional modules; they are not
part of the initial terminal setup. See [profiles](docs/PROFILES.md) for the
managed files, configuration and removal instructions.

## Connect devices when you need them

Start locally; HOMI needs no hosted account to coordinate agents on your own
computer. Your clients use the model providers you have configured.

For another machine, explicitly configure its SSH connection or join a shared
bus with an invitation. Installing HOMI does not connect devices or grant access
to someone else's agents. [Connecting agents](docs/BUSES.md) covers local buses,
invitations and self-hosting.

The supported clients are the **Claude Code CLI** and **Codex CLI**. Setup checks
the required client capabilities. Desktop applications are not supported clients,
and queued messages do not promise desktop wake. See the
[client requirements](docs/INSTALL.md#clients) for details.

## Keep it working

Run `homi doctor` to check the installed release, client registrations and
optional dependencies. A persistent background service is an optional setup
choice; otherwise your agent can start HOMI when the task needs it.

For Homebrew upgrades, make the new release available and activate it:

```sh
brew upgrade nonlocally/tap/homi
"$(brew --prefix nonlocally/tap/homi)/bin/homi" update
homi doctor
```

[Installation and maintenance](docs/INSTALL.md) covers rollback, configuration,
and removing HOMI while preserving identities, messages and credentials.
Third-party clients and tools installed during setup remain yours.

## Learn more

- [Quickstart](docs/QUICKSTART.md): install, sign in and delegate your first task.
- [Installation](docs/INSTALL.md): requirements, automation, updates and removal.
- [Connecting agents](docs/BUSES.md): shared buses and other devices.
- [CLI reference](docs/CLI.md): scripting, execution control and delivery semantics.
- [All documentation](docs/index.md): optional modules and contributor references.

HOMI continues Communicate. Existing installations keep the `communicate`
command, `communicate@communicate` plugin identity and state directories.
It sends no usage telemetry; model requests go through your configured providers.

Released under the [MIT license](LICENSE), with bundled third-party notices.
