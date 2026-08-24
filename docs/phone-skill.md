---
name: phone
description: Drive an Android phone as an agent — read its state, act through intents, and use the screen only when nothing above it will do. Use when a task requires the phone itself: answering a question about it, operating an app, sending something, or reporting what is on the device.
---

# Driving the phone

You are operating a real person's phone. Everything is recorded and some of
it is irreversible. Most tasks do **not** need the screen; reaching for it
first is the most common way agents fail here.

## Ask the device first

```
phone capabilities
```

Which providers are readable, which apps declare which intents, what is here
right now — the device answering for itself. **More current than any
document, including this one.** Tables of "which app supports what" rot; ask.

You are probably not on the phone. One flag, and everything below is
identical: `phone --device aadarshs-pixel-10 capabilities`

## Three routes. Climb down, never up.

| route | how | cost | checks itself? |
|---|---|---|---|
| **1. state** | `content query`, `cmd <svc>`, `dumpsys`, `settings` | ~0.5s, bytes | yes |
| **2. intent** | `am start -a <ACTION>` — the app does the work | ~1.5s | only if you look |
| **3. screen** | `look` / `find` / `tap` / `type` | ~4s, 20-40x output | **no** |

Try the highest route that could work; descend only when it genuinely fails.
The screen is the **residual** route — where you end up, not where you start.

## Route 1 — state

Structured in, structured out. Works with the display off, and provable.

```
phone notifs           every app's notifications — answers most questions
phone media            what is playing, and transport control
phone cal --days 7     calendar          phone contacts <name>   contacts
phone battery          battery           phone foreground        app in front
phone sh 'dumpsys <service>'             anything else the platform knows
```

`notifs` is the most useful verb here. "What happened today", "did X reply",
"what is my battery" are all state questions — never visual ones.

## Route 2 — intent

Hand the app the data and let it do the work: no coordinates, nothing to
mis-tap, works with the screen off.

```
phone open <package>              launch an app (verifies it came forward)
phone msg <app> --to ... --text "..."
phone play "<song>"               tries the intent, says if it fell back
phone sh 'am start -a <ACTION> --es <key> <value> -p <pkg>'
```

**Declaring an intent is not honouring it.** An app can list an action, accept
the intent, come to the foreground, and do nothing — `am start` exits 0
either way. This is the normal case, not a rare corner.

Ask who handles an action, and pass `-t` whenever the action carries data —
without it, resolution matches almost nothing and a real handler looks absent:

```
phone sh 'cmd package query-activities -a <ACTION> -t <mime>'
```

## Route 3 — screen, the residual route

The only route that cannot check its own work, and the least reliable: dumps
fail on animating surfaces, secure windows, and a dark screen. **Expect
`look` to fail outright a fair fraction of the time** — that means "I cannot
see", never "nothing is there".

```
phone look             the screen, summarised — prefer over `phone ui`
phone find "<label>"   a label -> "x y", non-zero if not found
phone tap X Y
phone type "text"
phone key BACK|HOME|ENTER
phone screen --out f.png          a picture, when the tree fails you
```

**look → decide → act → look again.** Not optional: `input tap` exits 0
whether or not anything handled the event, so a tap that did nothing looks
exactly like one that worked — until you look.

If `find` fails it exits non-zero. **Do not guess coordinates.** A wrong tap
can send, delete, or buy something. Say you could not find it.

## Verify by observation

An exit code is not evidence — `input tap`, `am start` and `content query` all
exit 0 on failure, and silence can mean "empty", "denied", or "your typo".

- Read the state back (`phone foreground`, `phone media`, `phone look`).
- **Vary the input and check the observation varies with it.** A plausible
  answer that does not change when you change the question is stale state,
  not a result. This is the trap that survives every other check.
- Report what you saw, not what you attempted.

## Large reads corrupt silently

Beyond a few KB, output can come back **the right length and the wrong
bytes**, with no error — a corrupted `dumpsys` read looks exactly like a
fact. Use `--checked` for anything bulky, or filter on the device to keep the
answer small:

```
phone sh --checked 'dumpsys <something big>'
```

## Take the wheel

```
phone lease acquire --as <your-name>
...work...
phone lease release --as <your-name>
```

One driver at a time; mutating verbs refuse and name the holder. Reads are
never gated. Leave the phone as you found it.

## Acting as the owner

- **Confirm consequential outbound actions first**, in the same turn, saying
  exactly what will be sent and to whom: messages to people, anything
  involving money, anything irreversible.
- `phone msg` drafts; `--send` actually sends. Prefer letting the human press
  send when unsure.
- **Never read a one-time code or 2FA number aloud or into a message.**
- Never touch Developer options, wireless debugging, Shizuku, the screen lock
  or accounts — that severs the control channel, and only a human holding the
  phone can restore it.

## Speaking

A turn marked `[spoken]` means the person is **listening, not reading** —
answer first, one or two sentences, no markdown. It does **not** mean call
`phone say`: the page speaks your reply itself, and saying it again puts two
voices in the room. `phone say` is for when no page is listening.

## Reporting

`phone log` is the honest record — actor, verb, result, duration, origin. If
the phone is unreachable it prints the controller's half, says so, and exits
non-zero: that is *half the story*, not "nothing else happened".

## If you remember four things

Ask the device, do not assume. Highest route that could work. Verify by
reading state back, never by an exit code. Never guess a coordinate.

Dated specifics — which app honours which intent, what is readable today —
live in `docs/phone-map.md`, established by trying them. This file is the
method; that one is the territory.
