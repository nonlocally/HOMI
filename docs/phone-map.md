# The phone map

What can actually be done on this phone, established by trying it.

This file exists because of a specific failure. Asked to play a song, the
phone's controller searched Apple Music, dumped the whole UI tree to read
artist names off it, and tapped a coordinate — while the device had a media
API sitting right there. It did that because its instructions taught it the
screen and never mentioned anything above it.

So: entries here are things somebody attempted and watched the result of.
Nothing in this file is written from memory of how Android works.

## How to read an entry

Every entry names the **route** that worked and the **date and build** it was
verified on. A fact about this phone in August 2026 is not a fact about
phones — an app update can move a task from one tier to another, in either
direction, without telling anyone.

Routes, highest first:

- **state** — `content query`, `cmd <service>`, `dumpsys`, `settings`.
  Structured, milliseconds, works with the screen off, verifiable.
- **intent** — `am start -a <ACTION> ... -p <pkg>`. The app does the work.
- **screen** — `look` / `find` / `tap` / `type`. Last resort, and the only
  route that cannot check its own work.

**Failed attempts belong here too**, and are often the more useful half —
they are what nobody else will discover before wasting an hour on it.

If an entry conflicts with what the device says now, the device is right:

```
phone --device aadarshs-pixel-10 capabilities
```

---

## Device

**aadarshs-pixel-10** — Pixel 10, Android 16 (sdk 36), security patch
2026-05-05. Shell tier via Shizuku; `phone` reaches it either on-device or
from a controller over a forwarded socket.

### Which `phone` you are running matters
**Verified:** 2026-08-23.

There is more than one `lib/phone` on this machine and they do not have the
same verbs. `ranger`'s `launch.sh` puts the **main checkout** on `PATH`
(`communicate/lib`), and that build is older — it has no `capabilities`, no
`media --current`, no `contacts`, no `cal`. Running the first command this
charter tells you to run gets you:

    phone: unknown verb 'capabilities'

The current build is the one in the worktree
(`.claude/worktrees/ranger/lib/phone`). If a verb the map documents does not
exist, check which binary you resolved before concluding the map is stale:

    which phone && phone --help | head -3

---

## Music

### Read what is playing
**Route:** state. **Verified:** 2026-08-23, Android 16 (sdk 36).

    phone --device aadarshs-pixel-10 media --current
    → com.apple.android.music  paused  Criminal, Akon, Vishal Dadlani &
      Shruti Pathak, Ra-One (Original Motion Picture Soundtrack)  0:45

App, transport state, position and track, from `dumpsys media_session`. No
screenshot, no UI tree. Works with the display off.

Note `--current` is not "the first active session": Spotify holds an active
session that is permanently `state=ERROR` ("Please login to use Spotify"),
and dispatching transport keys at it does nothing.

### Play / pause / skip
**Route:** state. **Verified:** 2026-08-23, Android 16 (sdk 36).

    phone --device aadarshs-pixel-10 media toggle
    → playing … 0:45   (then paused … 0:47 on the second call)

`cmd media_session dispatch` addresses the real session. The older
`input keyevent KEYCODE_MEDIA_*` injects a key at whatever holds audio focus
and exits 0 whether or not anything consumed it.

### Play a *specific* song
**Route:** intent for some apps, screen for Apple Music.
**Verified:** 2026-08-23, Android 16 (sdk 36).

Six packages declare `android.media.action.MEDIA_PLAY_FROM_SEARCH` here —
Apple Music, Tidal, YouTube Music, YouTube, Play Movies, Spotify. Ask, don't
assume: `cmd package query-activities -a <ACTION>`.

**Apple Music declares it and ignores it.** It accepts the intent, comes to
the foreground, and starts nothing; `media --current` still reads PAUSED
afterwards. Declaring an intent is not honouring it. For Apple Music,
search-and-play still needs the screen.

**YouTube Music treats it as "open search results", and never plays.** It
accepts the intent, receives the query correctly, and navigates to its own
search-results page — and stops there. Confirmed on the raw UI dump:

    resource-id=.../search_edit_text   text="tum hi ho arijit singh"

The query lands in the search box; the results list (Songs / Videos /
Artists / Albums chips) renders. Nothing is selected, nothing is queued,
nothing plays. `dumpsys media_session`'s "Audio playback (lastly played comes
first)" never listed YouTube Music at all, across every attempt.

Works cold and warm. On a cold start `am start` launches the activity; when
the app is already top-most Android says

    Warning: Activity not started, intent has been delivered to
    currently running top-most instance

and YouTube Music *does* handle that `onNewIntent` — firing a third query at
a warm app updated `search_edit_text` to the new words. So the intent is
reliably delivered and reliably understood. It is just never *played*.

| app | takes intent | receives the query | acts on it | starts playing |
|---|---|---|---|---|
| Apple Music | yes | — | no — lands on its own UI | no |
| YouTube Music | yes | **yes** | opens search results for it | **no** |

So the honest summary is that **no app on this phone plays a named song from
an intent.** Both declare the action; neither honours the "PLAY" in its name.
Search-and-play still needs the screen, in both apps.

### The decoy that nearly made this entry wrong
**Verified:** 2026-08-23, Android 16 (sdk 36). Worth reading before you trust
any media-session read after an intent.

Firing the intent and then reading the session back *looks* like it worked:

    metadata: description=Criminal, Vishal Dadlani, Akon & Shruti Pathak
    queueTitle=Up next, size=25

A plausible track, a real queue — and it is **stale state**, not a result.
YouTube Music restores its previous queue when it starts, so the session
shows a real song that has nothing to do with what you asked for. The tells:

- `state=PAUSED, position=0`, and the `updated=` stamp never moves.
- It survives a **different** query. Fire `"tum hi ho arijit singh"` and the
  metadata still reads `Criminal`.
- It survives `am force-stop` — it is restored on the next cold start.

The control that settles it is **changing the query and checking that the
observation changes with it**. Reading the session once, after one query that
happened to match what was already loaded, produces a confident wrong answer.
That is the same shape as the failure this whole file was written about, one
tier up: an observation that is real, and simply not evidence of what you
think it is.

### Finish what the intent started — you can't, from shell
**Route:** none. **Verified:** 2026-08-23, Android 16 (sdk 36).

Since the intent gets you a search page, the obvious repair is to compose the
tiers: intent to get there, then `cmd media_session dispatch play` to start
something. **This does not work, and it is worse than not working — it starts
a different app.**

`cmd media_session dispatch` has no way to name a session. It hands the key
to the *media button session*, and that is the app which last held audio, not
the app in the foreground. With YouTube Music foregrounded and active, the
dump still read:

    Media button session is com.apple.android.music/...

and dispatching `play` started **Apple Music** — position advanced
47193 → 47303 — while YouTube Music stayed at `PAUSED, position=0` with an
unchanged `updated=` stamp. Watched, not inferred. Paused again after.

The consequence for a caller: **a media dispatch is not addressed to the app
you were just talking to.** Read `Media button session` before dispatching,
or you will silently drive a different app than the one you meant. There is
no shell-side way to point a transport key at a chosen session.

---

## Asking the device things — and knowing what the answer meant

Three traps live in the tools themselves, not in Android. Each turns a loud
failure into a quiet wrong answer, which is the exact thing this file exists
to prevent.

### `phone sh` throws the device's stderr away
**Verified:** 2026-08-23, Android 16 (sdk 36).

`phone sh 'content query --uri content://com.google.android.keep/notes'`
prints **nothing at all** and exits 0. That looks like "readable and empty".
It is not. Redirect *on the device* — inside the quotes — and the same
command says:

    $ phone sh 'content query --uri content://com.google.android.keep/notes 2>&1'
    java.lang.SecurityException: Permission Denial: opening provider
      KeepProviderImpl from (null) (pid=..., uid=2000) that is not exported
      from UID 10314

Same for a completely invented authority: silent without the redirect, a
stack trace with it. **Put `2>&1` inside every `phone sh` you intend to read
a result from.** A `2>&1` on the outside catches the controller's stderr, not
the phone's, and does nothing for this.

### `content query` exits 0 no matter what happened
**Verified:** 2026-08-23, Android 16 (sdk 36).

The exit code carries no information — it is 0 for rows, for permission
denials, and for authorities that do not exist. You must read the *shape* of
the output. There are five, and they mean different things:

| output | meaning |
|---|---|
| `Row: 0 ...` | readable, and there is data |
| `No result found.` (stdout) | the query ran and matched nothing |
| `SecurityException: ... requires <PERM>` | denied — and it names the permission you'd need |
| `SecurityException: ... not exported from UID <n>` | **no permission can fix this**; shell can never open it |
| `IllegalStateException: Could not find provider` | no such authority — usually your typo |
| *nothing* | you forgot the on-device `2>&1`; see above |

Two of those deserve care:

- **`No result found.` is ambiguous.** It is what an empty table returns —
  and also what a *wrong path on a real authority* returns.
  `content://com.google.android.gms.phenotype/x` says "No result found" and
  `/x` is not a table. So "empty" is only trustworthy when you know the path
  is right.
- **"not exported" is a wall, not a lock.** "requires PERM" means someone
  with that permission could read it. "not exported from UID" means the app
  never opened the door to anybody; there is no flag, no grant, no Shizuku
  tier that gets you in. Stop there and write it down.

### `query-activities` under-reports unless you give it a MIME type
**Verified:** 2026-08-23, Android 16 (sdk 36).

The map tells you to ask the device who handles an action. Ask it wrong and
it lies by omission:

    cmd package query-activities -a android.intent.action.SEND
      → 4 handlers
    cmd package query-activities -a android.intent.action.SEND -t text/plain
      → 17 handlers, including com.google.android.keep

Intent resolution matches action **and data**. An action that normally
carries data resolves against almost nothing when you omit `-t`, so a handler
that exists looks absent. Keep really does declare `SEND`/`text/plain`
(`ShareReceiverActivity`, `exported=true`) — the four-handler answer would
have had you conclude otherwise.

`MEDIA_PLAY_FROM_SEARCH` takes no data, so the six-package answer above is
unaffected. Any action that carries a payload is not.

---

## Notes and documents

### Read the notes in Google Keep
**Route:** none. **Verified:** 2026-08-23, Android 16 (sdk 36).

Keep's authority `com.google.android.keep` exists and resolves —
`KeepProviderImpl` is right there in `dumpsys package providers` — and it is
**not exported**:

    SecurityException: ... not exported from UID 10314

That is the wall, not the lock: no permission grant reaches it. **There is no
state-tier route to Keep note content on this phone**, and no amount of
Shizuku changes that. Don't spend time on it. (Ten Keep authorities are
registered; the other nine are file/clipboard/startup plumbing, not notes.)

### Write a note
**Route:** intent, *declared but untested*. **Established:** 2026-08-23.

Keep declares `android.intent.action.SEND` for `text/plain` via
`com.google.android.keep.activities.ShareReceiverActivity`, `exported=true`,
so `am start -a ...SEND -t text/plain --es android.intent.extra.TEXT "..."
-p com.google.android.keep` should create a note.

**Not fired.** Creating a note is a write, and — given Keep is not readable
from shell — it is a write this agent could not verify *or clean up* without
the screen. Declaring is not honouring (see YouTube Music above), so treat
this as a plausible route, not a working one, until somebody watches it.

`android.intent.action.CREATE_NOTE` resolves to nothing here, with or
without a MIME type.

### Google Docs
**Route:** none for content. **Verified:** 2026-08-23.

`com.google.android.apps.docs.editors.docs` registers no readable authority
of that name (`Could not find provider`). Docs content lives behind the
account, not on a shell-reachable provider.

---

## Personal data

### Contacts, calendar, SMS, call log
**Route:** state. **Verified:** 2026-08-23, Android 16 (sdk 36).

    phone --device aadarshs-pixel-10 contacts peer
    phone --device aadarshs-pixel-10 cal --days 14

`com.android.shell` declares `READ_CONTACTS` / `READ_CALENDAR` / `READ_SMS` /
`READ_CALL_LOG` and the platform pre-grants them — confirmed on-device as
`granted=true, flags=[SYSTEM_FIXED|GRANTED_BY_DEFAULT]`, appops `allow`.

SMS and the call log currently return "No result found", which means
**readable and empty**, not blocked — this SIM is out of service (AT&T,
emergency-only, data over wifi calling). A denial raises a SecurityException
instead; that difference was checked against a deliberately bogus provider.

Calendar reads `instances/when/<from>/<to>`, which expands recurrences, not
the `events` table, which does not.

---

## Messages

### Reply to a message without touching the screen
**Route:** none yet. **Established:** 2026-08-23.

There are 26 live direct-reply (`RemoteInput`) actions in the notification
dump at any given moment, so the capability is right there. But shell
**cannot** fire one: a `PendingIntent` is a live Binder token held in
`system_server`, and `dumpsys` prints only a description of it. There is no
path from that text back to a fireable object, by design.

Firing one requires a process holding the live `Notification.Action` — i.e. a
bound `NotificationListenerService`. Termux:API already *has* listener access
on this device but exposes list-only; reply is unimplemented upstream (three
open feature requests).

Under investigation. Nothing here is settled.

---

## What the tiers actually cost

**Measured:** 2026-08-23, Android 16 (sdk 36), controller over the forwarded
socket. Wall clock from the controller, so it includes `phone`'s own startup
— which is the number you actually pay.

| call | time | output |
|---|---|---|
| `sh 'settings get ...'` | 0.48s | 8 B |
| `media --current` | 0.58s | 131 B |
| `foreground` | 0.78s | 64 B |
| `notifs` | 1.71s | 1.5 KB |
| `battery` | 2.06s | 28 B |
| `ui` | 3.69s | 2.5 KB |
| `look` | 3.87s | 625 B |

The screen tier costs **roughly 5× the wall clock and 20–40× the output** of
a state read, for an answer that cannot be verified. That ratio is the whole
argument for climbing down rather than up.

### Batch your device shell calls
Each `phone sh` pays a fixed ~0.31s of setup. Five `settings get` calls run
separately took **2.25s**; the same five inside one `phone sh 'for k in ...'`
took **1.00s**. Solving for it: ~0.31s fixed per invocation, ~0.14s of actual
work each.

So when you are probing — sweeping providers, reading a dozen settings —
**put the loop on the device, not in the controller.** It is not a micro
-optimisation; at eighteen probes it is the difference between seven seconds
and one.

### The screen tier can fail outright — and does
**Verified:** 2026-08-23, Android 16 (sdk 36), YouTube Music.

`look` and `ui` parse `uiautomator dump`'s XML, and **YouTube Music's tree is
not well-formed**:

    phone: could not parse the UI tree
      (not well-formed (invalid token): line 1, column 49103)

Seen on a 72 KB dump, and again at column 16328 on a different YouTube Music
screen. Some attribute in the tree carries a byte the XML parser rejects. The
failure is intermittent between screens of the *same* app, so a passing `look`
is not evidence the next one will pass.

This matters more than a normal bug, because the screen is the tier you drop
to when the other two have already failed. **The last resort is not
guaranteed to be available.**

Two things that still work when it happens:

- `ui` and `look` fail independently — on one screen `ui` rendered fine while
  `look` did not. Try both before giving up.
- The raw dump is still there, and `grep` does not care about well-formedness:

      phone sh 'uiautomator dump /sdcard/d.xml >/dev/null 2>&1;
                grep -o "text=\"[^\"]*\"" /sdcard/d.xml'

  That is how the YouTube Music search box was read after both verbs failed.
  Cruder than `find`, but it does not need the tree to parse.

---

## Not tested, on purpose

Some things are excluded by the charter rather than unexplored:

- **Anything that reaches a person or spends money** — sending, calling,
  posting, purchasing. A capability that can only be proven by contacting
  somebody stays untested, and that is a complete entry.
- **Anything that could cost us the control channel** — Developer options,
  Wireless debugging, Shizuku, screen lock, Tailscale, accounts. Switching
  off wireless debugging severs the link and only a human holding the phone
  can restore it.
