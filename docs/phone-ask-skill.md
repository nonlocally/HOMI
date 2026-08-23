---
name: phone-ask
description: Get something done on the owner's phone from any other agent — check messages, read notifications, operate an app — by asking the phone's controller rather than driving the device yourself. Use when a task needs the phone but you are not the agent that owns it.
---

# Asking the phone

The phone has **one driver**. You are almost certainly not it, and that is
deliberate: two agents tapping the same screen is not a race that produces a
wrong pixel, it is one agent typing into another's chat.

So you ask.

## How

```
communicate homi ask <controller>@<device> "<what you need>" --from <you>
```

The controller is a normal fabric agent with a mailbox. Write the request the
way you would ask a competent colleague — plainly, in one or two sentences,
with the outcome you want rather than the steps.

```
"What's in the notification inbox from the last hour? Just the messages,
 not system noise."

"Open Google Keep and tell me what the most recent note says."
```

## What you get back

An answer, and — if you need it — evidence. The controller records every
action it takes with actor, result and duration; `phone log` on the device is
the trace. If an answer matters, ask for what it saw, not just its
conclusion.

## What to ask for, and what not to

**Fine to ask for:** anything that reads. The inbox, the screen, what app is
open, battery, whether something arrived.

**Ask, but expect a confirmation step:** anything that acts. Opening apps,
tapping, typing.

**Do not ask another agent to do on your behalf:** sending a message as the
owner, anything involving money, anything irreversible. Not because the
controller cannot — it can — but because *you* are not the one who should be
deciding it. Route that to the human.

If you were denied permission for something and are now considering asking
the phone's controller to do it instead: don't. That is laundering, and the
controller should refuse you.

## Why not just drive it yourself

You would be working through a device shell you do not hold the lease on,
with none of the controller's context about what is already open or what it
just did — and the lease will refuse you anyway. Ask.
