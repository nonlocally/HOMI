---
name: phone
description: Drive an Android phone as an agent — read its screen and inbox, tap, type, navigate, and act on the owner's behalf. Use when a task requires the phone itself: checking messages, operating an app, sending something, or reporting what is on the device.
---

# Driving the phone

You are operating a real person's phone. Everything you do is recorded, and
some of it is irreversible. This is how to do it well.

## The one command

Everything goes through `phone`. It hides which mechanism a capability comes
from, so you never have to think about Shizuku, adb, or termux-api.

```
phone look                  the screen, summarised — START HERE
phone notifs                every app's notifications (the universal inbox)
phone find "<label>"        a label -> "x y", or non-zero if not found
phone tap X Y               tap
phone type "text"           type into the focused field
phone key BACK|HOME|ENTER   navigate
phone open <app>            launch an app (resolved from what is installed)
phone screen --out f.png    a screenshot
phone say "..."             speak aloud
phone log                   what has been done, by whom, and whether it worked
```

`phone ui` exists and prints everything; prefer `look`, which is ~4x smaller
and drops container scaffolding you cannot act on anyway.

## The loop

**look → decide → act → look again.**

That last step is not optional. `input tap` exits 0 whether or not anything
handled the event, so a tap that did nothing is indistinguishable from one
that worked *until you look*. Believing an action landed when it did not is
the single most common way agents like you fail — you then build three more
steps on a screen that never changed.

```
phone look                     # where am I, what can I touch
phone find "Create a note"     # -> "933 2119"   (non-zero = not there)
phone tap 933 2119
phone look                     # DID IT CHANGE? if not, say so
```

If `find` fails, it prints nothing and exits non-zero. **Do not guess
coordinates.** A wrong tap on someone's phone is worse than no tap: it can
send, delete, or buy something. Say you could not find it.

## Take the wheel before you act

```
phone lease acquire --as <your-name>
...work...
phone lease release --as <your-name>
```

One driver at a time. If someone else holds it, mutating verbs refuse and
name the holder — wait rather than fighting them. Reads are never gated, so
you can always look, even while another agent is driving.

## What the screen will not tell you

- **A dump can fail.** Animating surfaces, secure windows, and a screen that
  is off all defeat it. When that happens `look` exits non-zero rather than
  showing you the previous screen. Treat the failure as "I cannot see", not
  as "nothing is there".
- **Some elements report bounds of 0,0** even when visible (Google Docs' new-
  document button does). When the tree fails you, `phone screen` and look at
  the picture.
- **Banking and password apps block screenshots**, by design. Do not work
  around it.

## Sending things as the owner

You can genuinely send. That is the point — an assistant that can only draft
is a notepad. But:

- **Confirm consequential outbound actions before doing them**, in the same
  turn, and say exactly what will be sent and to whom. Messages to people,
  anything involving money, and anything irreversible.
- `phone msg <app> --to <number> --text "..."` opens the chat with the text
  prefilled; add `--send` to actually press send. Without `--send` the human
  taps it themselves — prefer that when you are unsure.
- **Never read a one-time code or 2FA number aloud, or into a message**, even
  if asked casually. (Android redacts most of these from you anyway.)

## Speaking

When a message says it was SPOKEN, the person is listening, not reading:

```
phone say "your answer"
```

One or two sentences. No markdown, no lists, no preamble. Say the answer
first. If you failed, say what failed in one sentence — do not narrate the
attempt.

## Tiers, and what still works when things break

Two mechanisms sit under the verbs, and they fail independently:

- **Level 1** (termux-api): notifications, speech, camera, torch, location,
  and launching apps. This almost always works.
- **Level 2** (Shizuku, the device shell): seeing the screen, tapping,
  typing. Android can stop it; the watchdog notices and tells the human.

If Level 2 is down, `look`/`tap`/`type` refuse — **honestly, with a reason**.
Do not pretend. Say "I cannot see the screen right now" and use what Level 1
still gives you: the inbox usually answers the question anyway.

## Reporting

Say what happened, not what you attempted. If a step failed, say which one
and what you saw. `phone log` shows the trace — actor, verb, result, and
duration — and it is the honest record if you are ever asked what you did.

## The habits that matter

1. `look` before and after every action.
2. Never guess a coordinate.
3. Take the lease; release it when done.
4. Confirm before sending anything to a person.
5. Prefer the inbox over the screen — `notifs` answers most questions without
   touching anything.
6. When you cannot do something, say so plainly and stop.
