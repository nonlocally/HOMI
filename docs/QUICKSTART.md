# Quickstart

Install HOMI once, open your chosen coding agent, then describe the work you
want it to coordinate. The agent handles identities, communication and execution
through its loaded HOMI tools. Download the release archive and its checksum
from [installation](INSTALL.md#get-the-archive).

## 1. Install and check

```sh
shasum -a 256 -c homi-0.3.0.tar.gz.sha256
tar -xzf homi-0.3.0.tar.gz
./homi-0.3.0/bin/homi setup                  # guided choices in an interactive terminal
export PATH="$HOME/.local/share/communicate/bin:$PATH"
homi doctor
```

Start with Node.js 20+ and Bash. Choose the clients and optional terminal tools
you want; setup offers missing selected dependencies and shows its plan before
applying it. Ghostty and provider login are separate choices. The managed service
is optional; your agent can start the daemon when needed.
Selecting either client includes tmux for agent seats, even if you decline the
terminal profile. This dependency does not change your interactive shell.

The terminal profile keeps `cx`/`cxx` and `cdx`/`cdxx` as commands usable from
zsh or Bash. Bash is an internal dependency; your interactive shell, Ghostty's
shell choice, tmux's default shell, and provider settings are preserved. Bulk
launching and session restore are not loaded by the initial setup.

For a CLI-only walkthrough with prerequisites already present, use
`setup --no-clients --no-service` instead. For an explicit dependency plan, use
`setup --install-missing --claude --terminal --dry-run`, then replace `--dry-run`
with `--yes` to apply it. Substitute `--codex` if that is your client.

`doctor` should show the payload as `ok` and `homi on PATH` resolving under
`~/.local/share/communicate/`. The daemon is stopped unless you selected the
service. Unselected optional tools are listed as unavailable, not as errors.

## 2. Sign in and open your agent

If you selected provider login during setup, complete that provider's own flow.
Otherwise, sign in through Claude Code or Codex normally. Login is separate from
installation; `--yes` alone never starts it. You can explicitly request
`homi setup --guided --claude --login-claude` in a terminal, or use the corresponding
`--codex --login-codex` flags.

Open a fresh Claude Code CLI or Codex CLI session in your project. If it was
running before setup, restart Claude Code or start a new Codex thread so it loads
the installed plugin. With the optional terminal profile, `cx` and `cdx` are
convenient launch commands; `cxx` and `cdxx` retain their full-auto behavior.

To add a client later, preview with `homi setup --install-missing --claude --dry-run`,
then replace `--dry-run` with `--yes`; substitute `--codex` as needed. An existing
client only needs `homi setup --claude` or `--codex` for registration.

Supported clients are the Claude Code CLI and the Codex CLI. Desktop apps are not
supported clients, and a queued message does not wake one. Client capabilities
are checked during setup; see [INSTALL.md](INSTALL.md#clients).

## 3. Ask for the work

In your agent conversation, try:

> Create a Claude agent called researcher, ask it to investigate the flaky test
> in this project, and bring me its answer.

Use the provider you have installed and authenticated. The agent checks the daemon
and execution prerequisites, creates the identity and running agent when needed,
sends the task, and waits for a correlated reply. Normal use does not require you
to type `claim`, `spawn`, `send`, or `ask` commands yourself.

For existing peers, ask: *"Register this session on the configured bus and show me
who is reachable."* Then name the intended peer and the task you want to send.
The agent uses the exact session and supplied reply identity; it should distinguish
stored mail from an actual answer. If readiness or authorization is missing, it
should explain that condition rather than claim success.

## 4. Optional: keep the daemon running

Choose the per-user service during setup, or run `homi setup --service` later.
It uses launchd on macOS or the systemd user manager on Linux. `homi doctor`
shows the running source and release parity. Updates restart an owned service
on the new release; a failed activation restores the previous definition.

## Advanced CLI reference

These commands explain the operations your agent normally performs. Use them
when integrating HOMI into scripts or debugging a route; they are not required
steps in the everyday agent workflow.

### Start the daemon

```sh
homi start
homi status
```

This is an unmanaged background daemon. For a managed daemon that starts with
your user session, use `homi setup --service`. Either way, stopping it never loses
mail: identities and mailboxes are files under `~/.local/state/communicate/homi/`.

### Two identities, one message

```sh
homi claim researcher
homi claim reviewer
homi send reviewer 'The experiment log is in runs/2026-09-24. Please review.' --from researcher
homi inbox reviewer
```

`inbox` prints the stored mail as JSON lines without consuming or acknowledging
it. `homi agents --json` shows both identities, their undelivered mail, and that
neither is bound to a seat: an identity is an address, not a process.

### A correlated question and answer

Open a second terminal. Start this wait before sending the question:

```sh
homi wait reviewer --timeout 300 --json
```

`wait` watches for mail arriving after it starts. It skips the earlier review
message even though that message remains in the inbox. If you send the question
before starting `wait`, use `homi inbox reviewer` to find it instead.

In the first terminal, ask and block for the answer:

```sh
homi ask reviewer 'Which run had the regression?' --from researcher --timeout 300
```

The second terminal prints the question together with its reply token. Answer
with that token:

```sh
homi reply TOKEN 'Run 3. The seed changed.' --from reviewer
```

The `ask` in the first terminal returns `Run 3. The seed changed.` That answer is
correlated to your question by the token; a storage or delivery receipt alone
does not establish that answer. [CLI.md](CLI.md) defines the three levels: stored, submitted,
replied.

### Run a model as an identity, in a seat

With tmux installed and a client authenticated:

```sh
homi spawn researcher --cli claude --cwd "$PWD" --json
homi agents                  # researcher is now bound to a running seat
homi seat ls
homi seat read SEAT --lines 40
```

With Claude, `"adopted": true` confirms native mail delivery to the named session.
A Codex seat or an unadopted session can read its durable inbox through the plugin;
typing mail into a seat requires a separate, explicit relay permission.

A seat is a tmux pane HOMI drives: read its screen, send it input, interrupt it,
kill it. Screen text is observation, not an agent reply, and a pane is not a
container. For isolation, see [CONTAINED-EXECUTION.md](CONTAINED-EXECUTION.md).

Once the Claude session reports adoption, an explicit question waits for its
correlated answer:

```sh
homi ask researcher 'Which test is flaky, and why?' --timeout 120
```

The agent replies with the supplied token through `homi reply TOKEN ...`.
Unadopted sessions retain mail in their inbox; a delivery receipt is not a reply.

### Reach other sessions and machines

Locally, a bus needs no account:

```sh
homi bus status --no-start --json
homi bus register
homi bus agents --json
homi bus dashboard --open
```

To join a bus someone else runs, redeem the invitation they give you privately,
then register the session you want to expose:

```sh
homi bus connect INVITE_CODE --device my-laptop
homi bus register --bus project
```

For durable mail between your own machines, link their daemons over SSH:

```sh
homi daemon pair user@other-host
```

[BUSES.md](BUSES.md) covers invitations, self-hosting, and browser access.

## Where to go next

- Optional terminal and mesh helpers: `homi profile preview --terminal --mesh`
  shows exactly what would change before you install anything
  ([PROFILES.md](PROFILES.md)).
- Scheduled workspace snapshots with an archive volume:
  [SNAPSHOTS.md](SNAPSHOTS.md).
- Optional account selection and rotation, using services you configure:
  [PROFILES.md](PROFILES.md#user-configuration-and-optional-accounts).
- Updating, rolling back, and removing: [INSTALL.md](INSTALL.md).
