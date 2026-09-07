# From the bus graph to swarm controls

The first graph is a view of the bus. Moving a node changes its position on the
canvas. It does not move a conversation to another device, change membership,
or send an instruction. Group by account and device, retain immutable agent IDs,
and distinguish shared-bus reachability from observed message activity.

The [snapshot](../lib/bus_broker.py) exposes agents, their permitted bus
memberships, account/device attribution, and endpoint status. The graph adds
directed, aggregate counts of retained broker messages between currently visible
agents on each permitted bus. It exposes no conversation content. Sharing a bus
does not create an activity edge. Any later task feed must preserve this scope
and define its own content-access policy.

## Gestures with explicit meaning

| Gesture | What it means | What must exist before execution |
| --- | --- | --- |
| Drag nodes; select **Arrange by device** | Arrange this page's layout in memory. | Nothing beyond viewing permission. No network or agent action. |
| Select several agents; choose **Assign task** | Draft a separate instruction for each selected immutable agent ID, with one shared objective and optional individual roles. | An explicitly paired sender on the same private bus, an execution grant, and a preview of every recipient and message. |
| Draw A → B; choose **Request handoff** | Ask A to prepare a summary or named artifact for B. This starts as a request to A; it does not copy A's transcript. | Both agents' bus membership; explicit permitted content; a receipt for the request. Mark the handoff complete only after an explicit response or artifact event. |
| Draw a box; name it **Review team** | Save a reusable selection of agent IDs for later tasks. | This is a canvas collection. Creating a private bus or adding agents remains a separate membership action. |
| Connect task A's result to task B | Draft a dependency: B receives the approved result after A reports completion. | A new task record and explicit completion event. Existing delivery receipts alone cannot trigger this correctly. |

Dragging an agent between device groups must never imply session migration.
Drawing an edge must initially create a draft, with a distinct appearance from
observed activity. Deleting a visual edge removes the draft or layout annotation;
it cannot retract a delivered message.

## The next useful slice: assign one task to selected agents

First add selection, a task composer, and a reviewable dispatch plan. The plan
contains the broker ID, bus, sender agent ID, recipient IDs, exact messages,
and current endpoint statuses. It can initially export CLI instructions for an
already authorized controller session, using the existing `bus send` operation.
This makes the interaction useful before browser command authority exists.

For direct **Send** from the canvas, add a separate, revocable pairing between
the signed-in human and one enrolled controller device/session. The device owner
explicitly authorizes that pairing for a bus and permitted operations. Keep its
device credential on the device; the browser does not receive it. The device's
existing outbound worker can fetch approved dispatch jobs and send using its
owned controller agent. This requires new pairing/job endpoints and worker code;
it is not available in the current snapshot API.

Group membership continues to grant viewing only. Neither an OpenWebUI admin
role nor matching account labels creates command authority. Even a bus browser
administrator cannot currently impersonate an enrolled agent: `_op_send` requires
the sender to be owned by the authenticated device. Preserve that check.

At **Send**, validate the pairing, bus admission, sender ownership, and each
recipient again. Store a bounded dispatch record keyed by a client operation ID
and deduplicate each recipient's send, so double-clicks or retries do not create
duplicate tasks. Record the initiating human separately from the actual sender
agent. Report partial dispatch explicitly; do not pretend several sends are an
atomic transaction. Show new offline or inaccessible recipients before sending
the affected task.

The results panel should use the actual [delivery contract](../lib/bus.py):

- **Accepted:** the broker stored the message.
- **Delivered:** the Claude socket accepted it; the native inbound gate can still
  hold it.
- **Queued:** it entered the existing Codex thread's queue; the thread may not be
  running, and consumption is unconfirmed.
- **Failed / expired / cancelled:** delivery did not complete under the current
  broker state. Cancellation cannot undo a message already fetched by a device.
- **Answered / completed:** future explicit task events, not deductions from
  `delivered` or `queued`.

Keep these receipts private to the authorized controller and participants;
ordinary group viewers do not gain receipt or task-content access. The current
conversation reply window is 24 hours from initiation. Longer task tracking
needs a separate lifecycle instead of silently extending that window.

Test the first direct-control slice with one existing Claude session and one
existing Codex thread: exact recipients, forbidden viewer, membership removed
after preview, duplicate submission, offline endpoint, partial delivery, and an
explicit reply. This validates the real transport without promising that a
message forces arbitrary execution.

## Controls to leave out for now

The bus has no uniform pause, resume, interrupt, kill, model-switch, filesystem
transfer, or process-migration operation. `bus stop` stops a device's bus worker
and owned broker; it is not a swarm pause. Revoking a device removes bus access;
it does not stop its processes. The separate [wake scheduler](../lib/wake.sh)
sends legacy routed prompts, and is not a hosted-bus task scheduler. Expose none
of these as equivalent graph controls without a specific new backend contract.

See [BUSES.md](BUSES.md) for device enrollment, private membership, exact session
identity, and the separation between browser viewing and agent messaging.
