# Quickstart

Ten minutes from an archive to two identities exchanging a correlated question and
answer, then a Claude or Codex session that knows HOMI. Every command here is the
real one; nothing is started, registered, or changed except what the step says.

## 1. Install and check

```sh
shasum -a 256 -c homi-0.3.0.tar.gz.sha256
tar -xzf homi-0.3.0.tar.gz
./homi-0.3.0/bin/homi setup --no-clients      # CLI only for now; clients come in step 5
export PATH="$HOME/.local/share/communicate/bin:$PATH"
homi doctor
```

`doctor` should show the payload as `ok`, `homi on PATH` resolving under
`~/.local/share/communicate/`, and the daemon as not running yet. Missing optional
tools (tmux, ssh, tailscale, claude, codex) are listed as unavailable, not as
errors.

## 2. Start the daemon

```sh
homi start
homi status
```

This is an unmanaged background daemon. For a managed daemon that starts with
your user session, use `homi setup --service` (step 7). Either way, stopping it never loses
mail: identities and mailboxes are files under `~/.local/state/communicate/homi/`.

## 3. Two identities, one message

```sh
homi claim researcher
homi claim reviewer
homi send reviewer 'The experiment log is in runs/2026-09-24. Please review.' --from researcher
homi inbox reviewer
```

`inbox` prints the stored mail as JSON lines without consuming or acknowledging
it. `homi agents --json` shows both identities, their undelivered mail, and that
neither is bound to a seat: an identity is an address, not a process.

## 4. A correlated question and answer

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

## 5. A session that knows HOMI

Install the plugin for the client you use, then start a fresh session:

```sh
homi setup --claude          # and/or: homi setup --codex
homi doctor                  # shows the registration and the cached plugin version
```

Restart Claude Code, or start a new Codex thread. The session can now discover
agents, register itself, send, reply, and drive seats through the plugin's skills
and MCP tools. Try, in plain language: *"Register yourself on the bus, then list
the agents you can reach."* The session runs `homi bus register` and
`homi bus agents --json` for you.

Supported clients are the Claude Code CLI and the Codex CLI. Codex queued delivery
requires `codex queue`; plugin setup has separate capability checks described in
[INSTALL.md](INSTALL.md#clients). Desktop apps are not supported clients, and a
queued message does not wake one.

## 6. Run a model as an identity, in a seat

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

## 7. Keep it running

```sh
homi setup --service
homi doctor                  # running daemon, its source, and "release parity"
```

The service is a per-user launchd agent (macOS) or systemd user unit (Linux) that
runs the installed release. Updates restart it on the new release; a failed
activation puts the previous one back.

## 8. Reach other sessions and machines

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
