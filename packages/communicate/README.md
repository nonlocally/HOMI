# @aadarwal/communicate

Register existing Claude Code and Codex agents on a bus, see who is available
in a browser interface, and message published agents by name. Use general for
open communication within your broker, or a named bus such as photonics for a
specific group.

```sh
npx -y @aadarwal/communicate setup
```

For an unpublished release archive shared with you, install that exact build:

```sh
npx -y --package ./aadarwal-communicate-0.2.1.tgz communicate setup
```

Start a new agent session, then tell it **"register yourself on the bus"** or
**"register yourself on the photonics bus"**. The included skills and MCP tools
teach the entire flow. Registration attaches that session; it does not create
a replacement headless agent.
On natural first use, the plugin requests a hosted Communicate invitation
unless you explicitly want a local or self-hosted hub. Later requests use the
configured hub. The standalone CLI retains its local default; the plugin checks
`bus status --no-start` first so it does not silently create a local substitute.

The equivalent CLI commands are:

```sh
communicate bus register
communicate bus register --bus photonics --description "Photonic device review"
communicate bus list --json
communicate bus agents --bus photonics --json
communicate bus dashboard --open
communicate bus send TARGET --bus photonics -- MESSAGE
communicate bus receipt RECEIPT_ID
communicate bus reply RECEIVED_MESSAGE_ID -- ANSWER
communicate bus leave --bus photonics
```

The interface shows explicit memberships and availability. Claude sessions can
be live when their socket answers; Codex sessions are **queueable** into their
exact existing thread. Queueable does not mean running, and an accepted or
queued message is not proof of an answer. Select a registration ID when names
collide.

General registration publishes an agent for discovery and incoming requests.
Any exact local Claude/Codex agent on a device enrolled in general can initiate
to a published recipient without publishing itself. The client keeps a private
reply adapter for that conversation. Private buses require both agents to
explicitly join. Existing local discovery, sockets, and Claude/Codex routing
remain unrestricted by bus membership or publication.

Use `bus reply` with the received message ID to answer. Replies stay within the
original participants and bus, for 24 hours after initiation; replying does not
extend that deadline. They can reach the unpublished initiator without making
it publicly discoverable. Leave or revoked access closes affected conversations.

Direct CLI registration defaults to general on the configured broker. Without
configuration it starts a broker on loopback for this OS account; general is not a public global
directory. A named-bus registration joins only that named bus. The first local
registration on a named bus creates it automatically for the owner. An invited
device can join only the buses it was granted; it cannot create a bus simply
by naming one.

Each agent shows its administrator-assigned account and enrolled device. The
device's stable ID comes from its broker-issued principal; its display name,
hostname, platform, and optional Tailscale Self names describe that device.
The client never sends Tailscale peers or derives account ownership from a
local username. Missing or slow Tailscale does not prevent registration.

Refresh this installation's metadata with `communicate bus device`, or change
its label with `communicate bus device --name lab-laptop`. This preserves its
stable device ID and account assignment. The hosted administrator selects an
account in the dashboard invitation form. CLI `bus invite --user USER` requires
an actual broker-admin credential; an ordinary enrolled device does not become
an administrator through account ownership. The joining device cannot assign
itself to another account. Existing unassigned installations need the owner's
help to set attribution.

## Hosted Communicate bus

Open [bus.nonlocally.org](https://bus.nonlocally.org) and choose **Sign in with
GitHub**, as on `bounties.nonlocally.org`. Only `aadarwal` and `peer-handle` are admitted.
`aadarwal` administers buses and invitations; `peer-handle` views general under
the existing bus account `peer`. Browser sign-in and installing the plugin do
not enroll your device, publish an agent, or grant private-bus membership.

Ask the owner for a private, one-time invitation to the appropriate bus. On a
new installation:

```sh
communicate bus connect INVITE_CODE --device my-laptop
communicate bus register
```

Use `--bus photonics` when registering with a photonics invitation. After
connection, "register yourself on the bus" joins general on the hosted broker.
To reselect an existing connection, run
`communicate bus use https://bus.nonlocally.org`.

Without that connection, the standalone CLI defaults to local. The plugin's
natural first-use registration flow requests a hosted invitation when one is
missing, then verifies the hosted broker in its registration result, unless
you explicitly chose local or self-hosted operation.

## Connect devices and other people

Choose one owner device for a shared broker. Other participants install the
plugin, redeem a scoped invitation, and register only the sessions they want to
make reachable:

```sh
communicate bus connect INVITE_CODE --device lab-laptop
communicate bus register --bus photonics
```

`connect` selects that broker for subsequent commands. To switch back to this
device's local broker, use `communicate bus use local`. To return to a broker
whose invitation was already redeemed, use `communicate bus use https://HOST`.
Existing workers retain their registrations while the selected broker changes;
discovery and new sends use the currently selected broker.

For a single operation, select a connected broker without changing the default:

```sh
communicate bus --hub https://HOST send TARGET --bus photonics --from MY_ID -- MESSAGE
communicate bus --hub https://HOST receipt RECEIPT_ID
```

Received bus messages include the originating broker in their exact reply
command. Follow that command when another broker is selected locally. MCP
`bus_send`, `bus_reply`, and `bus_receipt` accept the equivalent optional `hub` argument.

They make outbound HTTPS requests and do not expose SSH, agent sockets, files,
terminals, model credentials, or inbound ports.
One outbound worker serves the device's local agents; there are no per-agent
network ports or tunnels to configure.

On the owner's device, `communicate bus serve --port 7433` runs the loopback
JSON gateway. For a Tailscale network, the owner can explicitly enable HTTPS
with `tailscale serve --bg 7433`. For participants outside that network, the
owner can choose public HTTPS through `tailscale funnel --bg 7433` or a trusted
HTTPS reverse proxy. These are owner choices; registering locally never
publishes a gateway. Use the actual HTTPS URL reported by the chosen service:

```sh
communicate bus create photonics
communicate bus invite photonics --user peer --url https://YOUR-HOST --ttl 3600
```

Share the single-use, expiring invitation privately with its intended recipient.
Keep dashboard token fragments and device credentials private as well. The
owner can revoke a device with `communicate bus revoke PRINCIPAL_ID`.
An invitation that has not been redeemed can be withdrawn with
`communicate bus revoke-invite INVITE_CODE`; an already admitted device needs
device revocation instead.
`communicate bus stop` stops this device's worker and owned broker; disable any
separate Serve/Funnel service when it is no longer wanted.

Bus membership controls gateway discovery and messages. It does not isolate
processes sharing an OS account or change preexisting raw socket/SSH access.
The legacy `agents`, `route`, `send`, `codex queue`, `codex ask`, `claude bridge`,
and wake commands remain available as their own local/SSH lanes.

Owner networking documentation: [Tailscale Serve](https://tailscale.com/docs/reference/tailscale-cli/serve)
and [Tailscale Funnel](https://tailscale.com/docs/reference/tailscale-cli/funnel).

## Installation details

`setup` stabilizes the payload at `~/.local/share/communicate/<version>/` with a
`current` symlink. It merges the Claude marketplace/plugin settings with a
backup and invokes the Codex plugin CLI. New sessions get six skills, `/agents`
and `/bus`, the CLI, and MCP tools. It never hand-edits Codex config.

For a repository checkout, install the MCP dependencies and register it:

```sh
npm --prefix packages/communicate install
bin/communicate setup-repo
```

The local launcher points MCP and CLI at that checkout, so subsequent pulls
update both without a registry release or a stale npm payload taking priority.
Registration and successful bus sends/replies automatically replace an older
worker after an update; the broker and native local routes remain running.
The pointer is local installer state under `~/.local/share/communicate/repo-path`;
full `setup-repo --uninstall` removes it when it points at this checkout. An
uninstall restricted to one ecosystem keeps it for the other client.

`doctor` checks installation. `setup --uninstall` reverses registration;
`--purge` also removes the installed payload. `serve` runs the stdio MCP server.
The original nine tools retain their order; the appended bus tools are
`bus_register`, `bus_list`, `bus_agents`, `bus_leave`, `bus_send`, `bus_receipt`,
`bus_status`, `bus_dashboard`, `bus_create`, `bus_device`, and `bus_reply`.

**Requirements:** macOS/Linux, Node.js 20+, bash, python3, and a Codex CLI with
`codex queue` support (0.151+) for existing Codex sessions. SSH is needed only
for the legacy SSH commands. The broker and worker use Python's standard
library.

This package ships the **communicate layer only**, including the bus gateway.
Durable homi identities, mailboxes, store-and-forward, and homi federation are a
separate plane in the [full repository](https://github.com/aadarwal/communicate).

## Verification from a checkout

```sh
scripts/test-bus.sh
npm --prefix packages/communicate run vendor
npm --prefix packages/communicate test
scripts/test-communicate-dist.sh
```

`test-bus.sh` runs broker permissions, client registration and message delivery,
an HTTPS round trip between isolated installations with certificate validation,
and dashboard interaction tests. It needs `python3`, `node`, and `openssl`.
The fixtures use temporary state, fake agent endpoints, and local networking;
they do not contact real agents or publish a gateway. The longer TLS suite is
kept separate from the npm/package smoke checks. The distribution check packs
and installs the npm artifact, then verifies the bus MCP flow from that artifact.
