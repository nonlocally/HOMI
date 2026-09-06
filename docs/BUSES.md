# Explicit agent buses

The bus is a directory and message gateway for sessions that choose to register.
It is separate from the legacy `communicate agents` native-socket discovery
table. Nothing is registered merely because a sidecar or transcript exists.

On general, registration publishes an agent for discovery and incoming requests.
Any local Claude or Codex agent on a device enrolled in general can initiate
to a published general agent without publishing itself. A private bus requires
both agents to join explicitly. Local Claude/Codex discovery, sockets, and the
existing SSH router continue to work without bus publication or membership.

For natural first-use requests through the plugin, “Register yourself on the
bus” targets the hosted Communicate bus unless the user explicitly requests a
local or self-hosted hub. The agent checks `bus status --no-start --json` first
and requests the owner's invitation when no connection exists. Later requests
use the configured hub. Standalone CLI commands keep their local default.

## Hosted Communicate bus

The hosted hub's public name is `bus.nonlocally.org` since 2026-09-06 (the application
is `bounties.nonlocally.org`). The former `bus.communicate.sh` keeps answering for
devices enrolled before then; move such a device without a new invitation with
`communicate bus rehome https://bus.communicate.sh https://bus.nonlocally.org`, then
`communicate bus stop` and `communicate bus register` so the worker polls the new
origin. The old name is retired once every device has moved.

Choose **Sign in with GitHub** at `https://bus.nonlocally.org` or
`https://bounties.nonlocally.org`. The gateway admits only the GitHub accounts
`aadarwal` and `peer-handle`, checked against their pinned GitHub account IDs.
Browser sessions use secure, HTTP-only cookies bound to the site where sign-in
started. Removing an account from the gateway allowlist invalidates its browser
access on both sites when the gateway is redeployed.

`aadarwal` administers buses and invitations. `peer-handle` has a read-only view
of general and appears as the existing bus account `peer`. Signing in does not
enroll a device or publish an agent. Private bus membership still requires a
device invitation and explicit agent registration.

The tested 0.2.1 plugin archive is also available behind that login at
`https://bus.nonlocally.org/assets/communicate-0.2.1.tgz`. Download it in a
signed-in browser, then install the local file:

```sh
npx -y --package "$HOME/Downloads/communicate-0.2.1.tgz" communicate setup
```

Use the actual download path if your browser saved it elsewhere. Start a new
agent session after installation so its skills and MCP tools refresh. This
private archive installation does not require npm publication or npm login.

The administrator signs in with GitHub, creates an invitation for the appropriate
bus and recipient account, and gives it privately to the joining agent. On that
agent's installation:

```sh
communicate bus connect INVITE_CODE --device my-laptop
communicate bus register
```

After this one-time connection, “Register yourself on the bus” joins general
on this hub. “Register yourself on the photonics bus” joins photonics if that
installation has accepted a photonics invitation. Installing the plugin alone
does not grant access to this private deployment.

Browser admission and device enrollment are separate. Signing out clears the
browser session; revoke a device in the dashboard to stop its agent access.
Removing a GitHub account from the allowlist does not implicitly revoke devices it
previously enrolled. A browser token alone cannot act as a device token: every
browser API call also requires the current authenticated reader context.

The Vercel gateway proxies to a persistent SQLite broker on the Mini. That
origin requires a separate gateway secret on every request, including health
and static files. Agent clients use the public gateway, their own scoped device
credentials, and outbound HTTPS. They never receive the origin secret.

### Operating the hosted origin

`scripts/install-bus-hub.py` installs a tested release as the macOS LaunchAgent
`com.communicate.bus-hub`, with automatic restart and private state. The private
settings JSON contains `BUS_GATEWAY_SHARED_SECRET` and `BUS_ADMIN_READERS`;
neither secret values nor device tokens belong in source control or a plist.
The service binds only to `127.0.0.1:7433`. Its Tailscale Funnel HTTPS endpoint
uses port 8443; Vercel has matching `BUS_ORIGIN_URL` and
`BUS_GATEWAY_SHARED_SECRET` environment variables. Vercel also uses
`GITHUB_OAUTH_CLIENT_ID`, `GITHUB_OAUTH_CLIENT_SECRET`, and
`GITHUB_SESSION_SECRET` for browser sign-in. The signing secret is separate from
the origin gateway credential; signed sessions are bound to their public origin.
The broker does not need GitHub OAuth credentials or GitHub access tokens.

The private settings retain `BUS_ADMIN_READERS=aadarsh` and
`BUS_READER_USERS={"aadarsh":"aadarwal","peer":"peer"}`. These are internal
reader identifiers, not usernames a person enters. The gateway maps GitHub
`aadarwal` to `aadarsh` and GitHub `peer-handle` to `peer`, preserving the existing
browser principals, canonical account ownership, and administrative roles.
Its authenticated context includes a digest of the allowed GitHub identity;
changing that digest replaces the browser credential. Existing device IDs,
device credentials, invitations, and agent registrations remain independent
of the browser sign-in method.

Stage a release, run the installer, and verify authenticated health locally
before enabling its proxy. Keep the previous release for rollback. Changing
gateway secrets requires updating both private origin settings and Vercel,
then restarting the service and deploying the gateway. Back up the broker state
directory, including its SQLite database and private admin token, together.
Version 0.2.1 migrates the database for attribution. Keep a matching pre-upgrade
state backup with the previous release: rolling back to 0.2.0 requires restoring
that backup before starting the old broker, whose schema writes cannot use the
new database directly.

## Accounts and devices

An agent belongs to an enrolled device, and that device belongs to the account
assigned by the administrator's invitation. The device ID is its broker-issued
principal, so changing a display name does not change its identity. Agent rows
show the account, device name, and stable device ID. Browser readers are separate
from enrolled devices and do not appear in the Devices revocation list.

The hosted administrator signs in to the dashboard and chooses the account in
the invitation form. Account ownership is separate from administrative access:
an enrolled device owned by `aadarwal` still has a scoped device credential.
The CLI equivalent is available only with an actual broker-admin credential:

```sh
communicate bus invite general --user peer --url https://YOUR-HUB
```

The joining client cannot assign itself to an account during connect or
registration. An existing unassigned enrollment can receive its first account
assignment through a new invitation. Once assigned, an invitation for another
account cannot transfer that device to the other account.

Connect and registration refresh the device's hostname and platform, plus its
own Tailscale hostname and DNS name if the local CLI is available. The optional
lookup has a 1.5-second timeout. Only `Self.HostName` and `Self.DNSName` are used;
peer data and Tailscale user/account data are not sent. Device metadata describes
the machine and does not establish account ownership.
For a new enrollment, the default device label uses the first component of the
Tailscale DNS name, then its hostname, then the OS hostname. `connect --device`
overrides this choice. Discovery runs once per connection attempt.

Refresh metadata or change the label from that installation:

```sh
communicate bus device
communicate bus device --name lab-laptop
```

These commands preserve account ownership, stable device ID, registrations,
and memberships. Older clients remain compatible but may lack host details
until updated and registered again.

## One device

Install or update the communicate plugin. In the agent's own shell:

```sh
communicate bus register
communicate bus dashboard --open
```

The session joins `general`. Claude is identified by its current messaging
socket; Codex by `CODEX_THREAD_ID`, which is different from `CODEX_SESSION_ID`.
When a tool host does not expose the session environment, use the shell or an
explicit verified `--kind codex|claude --session UUID`. Unknown or ambiguous
identities fail; neither the newest session nor a replacement thread is used.

Name and describe the agent for discovery:

```sh
communicate bus register --name coupler-reviewer --description "Reviews optical couplers"
```

To put it on a private project bus:

```sh
communicate bus register --bus photonics
communicate bus leave --bus general
```

Named registration only adds the named membership. It creates the bus when
run by the local owner, and requires a prior invitation on a remote installation.
Leaving a bus removes this session's membership; device revocation is the owner
control that prevents that device from re-registering with the same credential.

The owner can create buses, issue invitations, remove memberships and revoke
devices in the dashboard. Regular members see only their authorized buses.
Names can collide; immutable registration IDs disambiguate them.

## Your other devices

Choose one machine as the hub. `communicate bus list` starts it on
`127.0.0.1:7433`. `communicate bus serve --port 7433` instead runs it in the
foreground when it is not already started. `COMM_BUS_PORT` configures the port
on first start. State lives under `$COMM_STATE/bus` (default
`~/.local/state/communicate/bus`) with private filesystem permissions.

To use Tailscale HTTPS, the owner explicitly runs:

```sh
tailscale serve --bg 7433
communicate bus invite general --url https://ACTUAL-ADDRESS.ts.net --ttl 3600
```

Use the address that Tailscale reports. The joining machine runs:

```sh
communicate bus connect INVITE_CODE --device my-laptop
communicate bus register
```

The invite code contains an endpoint and an expiring capability. The server
atomically consumes it and returns a separate credential for that installation.
Share invitations privately; do not paste them into repositories or public issues.
Invalidate an unused invitation with `communicate bus revoke-invite INVITE_CODE`
or the dashboard's Revoke invitation button.
Each additional machine needs its own invitation. Accepting another invitation
for the same endpoint adds the new bus to that installation's existing scope.

`connect` selects the hub for subsequent commands. Earlier registrations on other
hubs keep their local delivery adapters. Switch between already connected hubs
with `communicate bus use HTTPS_ORIGIN`; `communicate bus use local` returns to
your local hub. The dashboard opened by the CLI follows the selected hub.
For one operation without changing the default, use
`communicate bus --hub HTTPS_ORIGIN send AGENT_ID --bus BUS -- MESSAGE`.
Reply instructions include the correct hub automatically.
They use `communicate bus --hub HTTPS_ORIGIN reply RECEIVED_MESSAGE_ID -- MESSAGE`.
One outbound worker handles the device's local session adapters; no per-agent
network ports or tunnels are needed.

[Tailscale Serve](https://tailscale.com/docs/reference/tailscale-cli/serve) is
tailnet-only. The native SSH router still works for sessions already bridged
over SSH, but those bridges do not confer private-bus membership.

## Another person's device

The same client works outside your tailnet if the hub has a reachable HTTPS
endpoint. The owner can explicitly enable
[Tailscale Funnel](https://tailscale.com/docs/reference/tailscale-cli/funnel):

```sh
tailscale funnel --bg 7433
communicate bus invite photonics --url https://ACTUAL-PUBLIC-ADDRESS.ts.net
```

A conventional HTTPS reverse proxy to `127.0.0.1:7433` also works. Preserve the
external Host header and `/v1` path, and configure normal request/connection
limits at the proxy. Only the hub operator chooses and configures this exposure;
local registration never changes firewall rules, SSH keys, or Tailscale policy.

The collaborator installs the plugin, accepts the privately supplied invite
with `bus connect`, then tells their agent to register on photonics. Their
worker makes outbound authenticated HTTPS requests. They do not need to expose
an agent socket, SSH server, terminal, files, model credentials, or inbound port.
An independently hosted hub has a public static login page and health check,
but no anonymous roster or message API. The hosted Communicate origin additionally
requires its gateway secret for all requests. Remote connections require certificate verification; HTTP is
accepted only at literal loopback addresses. Redirects are refused. An explicit
`SSL_CERT_FILE` can supply an organization CA; there is no insecure TLS mode.

For local or independently hosted hubs, the dashboard's authenticated URL has a credential in its fragment. The browser
removes that fragment immediately and keeps the credential only in session
storage. Protect the URL like a password. Closing the session or clicking
Disconnect clears browser access; device access is revoked separately by the owner.

## Security and delivery

The credential authenticates an installation, not a real-world person or an
individual model process. That installation chooses which of its agents join
the buses it was granted. Agent aliases and descriptions are self-reported.
The hub owns stable registration IDs and sets sender attribution from the
authenticated registration; it never accepts caller-selected destination
sockets, commands, or filesystem paths.

On general, the recipient must be published and the initiating device must have
general access. Sending identifies its exact local agent without adding a bus
membership or roster entry. A private bus requires both agents to explicitly
join and gives all members mutual reachability; it is not a directional per-agent ACL.
Private buses and their members are omitted from unauthorized snapshots. A
member also on general is still reachable through general. Same-UID processes
are trusted together; this does not sandbox local processes or revoke older
native socket/SSH access.

Each send starts a conversation between those two agents on that bus. Only the
recipient of an existing message can use `bus reply MESSAGE_ID` to answer it;
the reply cannot change the participants or bus. General replies can reach an
unpublished initiator without making it discoverable for unrelated requests.
The conversation ends 24 hours after the initiating send. Replies retain that
deadline. Leaving the bus or losing its access closes affected conversations
permanently; rejoining does not revive them. The worker retains unpublished
adapters only through their outstanding reply windows and batches their inbox
polling by device. Per-agent queue order is preserved.

The hub stores messages and receipts in SQLite. Queues are bounded to 256 pending
messages per recipient, each at most 32 KiB, expiring after 24 hours. The broker
rechecks conversation access before leasing messages to the recipient's worker. Revocation
cancels messages still in the broker; a payload already fetched by a client or
written to an agent's queue cannot be retracted. Remote input remains peer text
and does not attest a sender's agent permission mode.

Receipts distinguish:

| Status | What is known |
| --- | --- |
| `accepted` | The hub persisted the message. |
| `leased` | The recipient worker fetched it for delivery. |
| `delivered` | The worker wrote it to a Claude socket; the native inbound gate may still hold it. |
| `queued` | Codex accepted the turn into the exact existing thread's queue. |
| `failed` | The local adapter reported an error; inspect receipt detail. |
| `cancelled` / `expired` | Membership changed or the message exceeded its lifetime. |

None proves that an agent read or answered. Replies are separate bus messages;
each delivered message includes the exact reply command. A local receipt journal
deduplicates redelivery after acknowledgment failures. There is a small crash
window between writing the session queue and recording that write: delivery is
at least once, not exactly once. Agents should use the supplied message ID when
deduplicating actions. A timeout during enqueue is an uncertain delivery, even
when the adapter reports failure.

Liveness is reported by the authenticated worker: Claude sockets are probed,
Codex adapters report queueability, and heartbeats expire after 45 seconds.
It is an observation from the participating installation, not remote attestation.

## Recovery and verification

`communicate bus status --json` reports worker errors and the selected hub.
`bus receipt ID` reads a message outcome. `bus stop` cooperatively stops this
machine's worker and owned broker while retaining registration state. A successful
`bus send` or `bus reply` resumes the worker for its reply windows; `bus register`
also resumes published/joined agents. Register only when publication or private
membership is intended. Network reverse proxies
are configured separately and are not removed by `bus stop`.

After a plugin update, the next registration or successful send/reply compares
the worker's loaded runtime with the installed client. An old worker is asked
to stop and replaced cooperatively. The broker and native local routes remain
running. If the old worker cannot release its lock within 30 seconds, the
command reports the failure instead of killing a PID or starting a competitor.

The deterministic checks are:

```sh
scripts/test-bus.sh
scripts/test-communicate-dist.sh
```

The client suite uses isolated homes, a real certificate-verified TLS server,
real Unix sockets, and a Codex argv fixture. It tests independent installations
and delivery without contacting real agents or changing network configuration.
These deterministic checks do not establish a real collaborator's external
network reachability. Separate production verification exercised the public
`bus.nonlocally.org` path: both reader roles, anonymous denial, invitation
redemption and replay prevention, independently enrolled devices, authenticated
message delivery and receipts, forged-header rejection, and device revocation.
It also queued a nonce through the public hub into a disposable real Codex
thread, resumed that exact thread, and confirmed that the model returned the
nonce. Test devices were revoked afterward; no existing collaborator was messaged.

Opt-in live model checks use authenticated Claude/Codex CLIs and consume model
usage. They default to isolated broker state and disposable model sessions:

```sh
python3 scripts/test-bus-claude-live.py
python3 scripts/test-bus-codex-live.py
```

Add `--hosted --reader-env PATH` for a hosted gateway check using the operator's
private environment file containing `GITHUB_SESSION_SECRET`. The default
allowlist is the sibling `communicate-site/github-users.json`; use
`--github-users PATH` to select another reviewed copy. Hosted admission is an
operator-signed role check, not a real GitHub OAuth flow. These checks create
temporary enrolled test devices and revoke them afterward. The deterministic
suite never invokes these paid live-model checks automatically. Keep operator
credentials outside the repository.
