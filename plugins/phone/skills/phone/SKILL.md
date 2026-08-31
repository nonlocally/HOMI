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
phone reply --list                what can be replied to right now
phone reply "<key>" "<text>"      reply to a notification — see the rules below
phone sh 'am start -a <ACTION> --es <key> <value> -p <pkg>'
```

Replying to a notification goes through a bound listener, not the screen — so
**never tap your way into a chat to answer someone.** `--list` first; if the
notification is not repliable, say so rather than falling to the screen.

**Declaring an intent is not honouring it.** An app can list an action, accept
the intent, come to the foreground, and do nothing — `am start` exits 0
either way. This is the normal case, not a rare corner.

Ask who handles an action, and pass `-t` whenever the action carries data —
without it, resolution matches almost nothing and a real handler looks absent:

```
phone sh 'cmd package query-activities -a <ACTION> -t <mime>'
```

## Route 3 — screen, the residual route

The only route that cannot check its own work. Dumps still fail on animating
surfaces, secure windows, and a dark screen — when one does, that means **"I
cannot see", never "nothing is there"**. Say which.

```
phone look             the screen, summarised — prefer over `phone ui`
phone find "<label>"   a label -> "x y", non-zero if not found
phone tap X Y          or: phone tap --label "<label>"
phone type "text"
phone key BACK|HOME|ENTER
phone scroll down      a screenful; also up/left/right, --n N
phone swipe X1 Y1 X2 Y2
phone scan "<label>"   scroll until it appears — the loop runs ON THE DEVICE
phone screen --out f.png          a picture, when the tree fails you
```

**look → decide → act → look again.** Not optional: `input tap` exits 0
whether or not anything handled the event, so a tap that did nothing looks
exactly like one that worked — until you look.

If `find` fails it exits non-zero. **Do not guess coordinates.** A wrong tap
can send, delete, or buy something. Say you could not find it.

### Spend turns, not bytes

A dump costs ~3.1s and it runs **on the phone**; shipping the 25-60 KB tree
back costs ~0.5s. So filtering the XML on the device to send less saves almost
nothing, and the only thing worth optimising is **how many dumps you take** —
which usually means how many times you stop to think.

```
phone tap --label "Add to order" --look     act and verify, one turn
phone scan "Special instructions"           3 screens, one turn, 10.9s
```

`tap --label` resolves and taps in one call, so a coordinate never has to
travel out to you and back. `scan` puts the whole scroll-and-look loop on the
device and returns a tap point; it stops early when the list stops moving, so
"not found" means the list ended, not that the budget did.

### A coordinate is not a promise

Apps float bars over their own content, and the centre of the row you named
can be **inside** the thing on top of it. Measured on DoorDash: the centre of a
menu row sat inside the floating cart bar, and tapping it opened the cart; the
centre of an option row sat inside "Add to order", and tapping it would have
bought the dish. Every exit code was 0.

`find`, `look` and `tap` now choose a point that a tap should actually reach,
and print `?under <thing>` when they could not find a clear one. That marker
is **advice, not a refusal** — on Compose screens the tree order does not
always match what is drawn on top, so a veto would strand you on real buttons.
When you see it, scroll the target into open space and look again.

### Ask which one you meant

`find` matches substrings, and screens are full of text that matches what you
meant without being it — a search box holding your own query, a saved-order
card quoting an option name.

```
phone find "<label>" --all              every candidate, with what it is
phone find "<label>" --after "<header>" only below that header
phone find "<label>" --exact
```

Reach for `--all` the moment a screen has more than one plausible target. It
costs the same dump you were taking anyway.

### Typing goes nowhere quietly

`input text` exits 0 with no field focused, so `type` used to report
delivering characters it had thrown away. It now checks what is accepting text
first and refuses with a reason. It catches a screen with nothing focused; it
can miss a field whose window just closed, because the input-method state goes
stale. **After typing something that matters, read it back.**

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

One driver at a time **per display**; mutating verbs refuse and name the
holder. Reads are never gated. Leave the phone as you found it.

## Work on your own screen

If another agent has the phone, you do not have to wait — take a display.

```
d=$(phone display create)          # a real, trusted, invisible second screen
phone --display $d open calculator
phone --display $d look / tap / screen
phone display rm $d                # closes what was on it
```

`--display` is global, like `--device`. `phone display ls` shows every screen
and what is on it.

Two rules, and **the `screens` skill is the rest of it** — read it before you
run several agents on this phone:

- **The person's screen is display 0.** Anything you launch there is on the
  phone in their hand.
- **One app, one display.** `open` refuses a package that is already
  elsewhere, because `am start --display` MOVES a task rather than copying
  it. `phone display move <app> --to <n>` when you mean it.

Displays multiply screens, not apps, and not the microphone, the speaker, the
notification shade or the person — those stay singular whatever `--display`
says.

## Acting as the owner

- **Confirm consequential outbound actions first**, in the same turn, saying
  exactly what will be sent and to whom: messages to people, anything
  involving money, anything irreversible.
- `phone msg` drafts; `--send` actually sends. `phone reply` sends immediately
  — there is no draft step. Prefer letting the human press send when unsure.
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
live in `map.md` beside this file, established by trying them. This file is the method; that one is the
territory.

For one app there is already a per-app skill: **`doordash`**, which is what
mastering an app looks like — its deep links, its screen grammar, the two
overlays that steal taps, and where it refuses to do what was asked.
