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

**YouTube Music honours the SEARCH half and not the PLAY half.** Fired
`-a MEDIA_PLAY_FROM_SEARCH --es query "criminal ra one" -p
com.google.android.apps.youtube.music`: it came to the foreground, resolved
the query *correctly*, and built a real queue —

    metadata: description=Criminal, Vishal Dadlani, Akon & Shruti Pathak
    queueTitle=Up next, size=25
    state=PlaybackState {state=PAUSED(2), position=0, speed=1.0}

— and then sat there. `position=0` and the session's `updated=` stamp did not
move across 10s. `dumpsys media_session`'s "Audio playback (lastly played
comes first)" listed only Apple Music, so YouTube Music produced no audio at
all. Fired a second time at an already-warm, already-foreground YouTube
Music: identical, and `updated=` did not even change, so the second intent
moved nothing.

So the right summary is not "Apple Music ignores it, YouTube Music works".
Both fall short, differently, and the difference matters to a caller:

| app | takes intent | resolves the query | starts playing |
|---|---|---|---|
| Apple Music | yes | no — lands on its own UI | no |
| YouTube Music | yes | **yes** — right track, 25-item queue | **no** |

YouTube Music is still the better target: it gets you a loaded queue with the
right song at item 1, which is one transport command from playing, whereas
Apple Music gets you a foreground app and nothing else.

Note `lib/phone`'s own docstring at `intent_handlers()` says "YouTube Music
implements MEDIA_PLAY_FROM_SEARCH and Apple Music does not". Both halves are
wrong on this device as of today: Apple Music *does* declare it (see
`capabilities`), and YouTube Music declares it without playing.

### Finish what the intent started — you can't, from shell
**Route:** none. **Verified:** 2026-08-23, Android 16 (sdk 36).

The obvious repair for the above is to compose the tiers: intent to load the
queue, then `cmd media_session dispatch play` to start it. **This does not
work, and it is worse than not working — it starts the wrong app.**

`cmd media_session dispatch` has no way to name a session. It hands the key
to the *media button session*, and that is the app which last held audio, not
the app in the foreground. With YouTube Music foregrounded, active, and
holding a loaded queue, the dump still read:

    Media button session is com.apple.android.music/...

and dispatching `play` started **Apple Music** — its position advanced
47193 → 47303 — while YouTube Music stayed at `PAUSED, position=0` with an
unchanged `updated=` stamp. Watched, not inferred. Paused again after.

The consequence for a caller: **a media dispatch is not addressed to the app
you were just talking to.** Read `Media button session` before dispatching,
or you will silently drive a different app than the one you meant.

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

## Not tested, on purpose

Some things are excluded by the charter rather than unexplored:

- **Anything that reaches a person or spends money** — sending, calling,
  posting, purchasing. A capability that can only be proven by contacting
  somebody stays untested, and that is a complete entry.
- **Anything that could cost us the control channel** — Developer options,
  Wireless debugging, Shizuku, screen lock, Tailscale, accounts. Switching
  off wireless debugging severs the link and only a human holding the phone
  can restore it.
