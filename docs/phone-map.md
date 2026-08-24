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
you were just talking to.** That is Android's design — `dispatch` takes no
session argument and there is no shell-side way to aim a transport key.

`phone` now surfaces it instead of leaving you to find out. `media` marks the
session the keys will actually reach:

    $ phone media --current
    com.apple.android.music  paused  <- keys  Criminal, Akon, ...

and `media --strict` **refuses to dispatch** when the keys would land on a
different app than the one that looks current, rather than quietly driving
the wrong one (`e7a1999`). Verified: the `<- keys` marker is present.

At a raw shell the trap is unchanged — read `Media button session` from
`dumpsys media_session` before dispatching.

---

## Asking the device things — and knowing what the answer meant

Three traps live in the tools themselves, not in Android. Each turns a loud
failure into a quiet wrong answer, which is the exact thing this file exists
to prevent.

### `phone sh` used to throw the device's stderr away — **fixed 2026-08-23**
**Was true until:** `ad17d7a` *"the device's stderr was being thrown away"*.
**Re-verified fixed:** 2026-08-23, Android 16 (sdk 36).

`phone sh` now merges the device's stderr, so a denied provider says so
without any help:

    $ phone sh 'content query --uri content://com.google.android.keep/notes'
    java.lang.SecurityException: Permission Denial: opening provider
      KeepProviderImpl ... that is not exported from UID 10314

Before the fix that same command printed **nothing** and exited 0, which
reads exactly like "readable and empty". If you are on an older build — the
main checkout lagged the worktree by hours on 2026-08-23 — put `2>&1`
*inside* the quotes. An outer `2>&1` catches the controller's stderr, not the
phone's, and does nothing for this.

The durable lesson, which no fix removes: **silence from a device command is
not a result.** Check that your transport actually carries errors before you
read an empty answer as an empty table.

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
**Verified:** 2026-08-23, Android 16 (sdk 36). This is **Android's**
behaviour, not a tool bug — it is still true at the shell.

Intent resolution matches action **and data**. An action that normally
carries a payload resolves against almost nothing when you omit `-t`:

    cmd package query-activities -a android.intent.action.SEND
      → 4 handlers
    cmd package query-activities -a android.intent.action.SEND -t text/plain
      → 17 handlers, including com.google.android.keep

The four-handler answer would have had you conclude Keep cannot take shared
text. It can (`ShareReceiverActivity`, `exported=true`).

`phone`'s own `intent_handlers()` now takes a `mime` argument
(`0bce460` and neighbours), so the tool asks correctly. **At a raw shell you
must still remember `-t` yourself.**

`MEDIA_PLAY_FROM_SEARCH` carries no data, so the six-package answer above is
unaffected. Any action with a payload is not.

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

## Notifications

### Read every app's notifications at once
**Route:** state. **Verified:** 2026-08-23, Android 16 (sdk 36). 1.7s, ~1.5 KB.

    phone --device aadarshs-pixel-10 notifs
    phone --device aadarshs-pixel-10 notifs --app com.google.android.youtube --json

The best state-tier surface on this phone: one call, every app, timestamped,
with `--json` giving `key`, `group`, `packageName`, `title`, `content`,
`when`. This is the right way to answer "what has been happening" — never a
screenshot of the shade.

**Entries with an empty title are not noise or a parse bug.** They are
Android *group summaries*: `title: ""`, `content: ""`, and a `tag` containing
`::SUMMARY::`. The real notifications are the siblings sharing that `group`.
Filter on `"::SUMMARY::" not in tag` if you want one line per actual item;
`phone notifs` currently prints both, which is why YouTube appears twice per
video.

### App names once resolved to the wrong app — **fixed 2026-08-23**
**Was true until:** `0bce460` *"an app name now resolves to that app, or to
nothing"*. **Re-verified fixed:** 2026-08-23, Android 16 (sdk 36).

Worth keeping because it was the sharpest edge on the phone, and because the
shape of the bug is one to watch for anywhere names get resolved.

`pkg_of()` resolved names against `pm list packages -3` — **third-party
only** — so every preinstalled Google app was invisible to it. Two failures
came out of that. It *refused* names its own error message advertised
(`youtube`, `ytmusic`, `photos`, `calendar`, none of which are third-party on
a Pixel). And when an alias missed, it fell through to a **substring** match
over the third-party list and took the single hit:

    "chrome" → com.android.chrome is installed but not third-party, so the
               alias was skipped
             → substring match found the one third-party package containing
               "chrome":
             → com.google.android.apps.chromecast.app   (Google Home)

So `--app chrome` reported on Google Home and printed `(no notifications)` —
which reads as a clean negative about Chrome. `open chrome` exited 0 having
done nothing.

Now: resolution runs against the **full** package list and matches whole
dotted segments, so a substring can never capture a different app; genuine
ambiguity is *named* rather than silently narrowed. Verified by observation
rather than by exit code —

    $ phone open chrome ; phone foreground
    → com.android.chrome/org.chromium.chrome.browser.ChromeTabbedActivity

**Passing the full package name is still the safer habit** in anything
scripted. Note `phone apps` still lists third-party packages only; for the
real list use `phone sh 'pm list packages | grep <name>'`.

The durable lesson: a name that resolves to the *wrong* app is the same class
of error as tapping a coordinate — a confident answer about something you did
not ask about. `(no notifications)` was indistinguishable from the truth.

---

## Moving between apps

### Bring an app to the foreground
**Route:** intent. **Verified:** 2026-08-23, Android 16 (sdk 36). ~1.4s.

    phone --device aadarshs-pixel-10 open com.google.android.keep
    phone --device aadarshs-pixel-10 foreground
    → com.google.android.keep/.activities.BrowseActivity

**`open` is one of the few verbs that checks its own work.** It resolves the
launcher component, starts it, then polls `ResumedActivity` until the package
is actually in front, and fails if it never arrives. Contrast `am start` and
`input tap`, which exit 0 regardless. Its contract:

| outcome | rc | output |
|---|---|---|
| landed | 0 | *silent* |
| not installed | 4 | `phone: <pkg> is not installed on this device` |
| started but never came forward | 4 | `phone: <pkg> did not come to the foreground` |

Silence means success here. That is the opposite of `phone sh`, where silence
means you lost the stderr — do not carry the habit across.

Pass the **full package name**; `open` goes through the same broken
resolution as `--app` above, so `open chrome` launches Google Home.

`phone key HOME` returns to the launcher and is the cheap way to leave the
phone as you found it.

---

## Settings you can read

**Route:** state. **Verified:** 2026-08-23, Android 16 (sdk 36).

All three namespaces are readable in full — 260 rows in `global`, 246 in
`secure`, 52 in `system`. Read many at once; each separate call costs ~0.31s:

    phone sh 'for k in zen_mode airplane_mode_on low_power; do
                echo "$k=$(settings get global $k 2>&1)"; done'

Useful ones confirmed present on this phone:

| namespace | key | here | meaning |
|---|---|---|---|
| global | `zen_mode` | 0 | Do Not Disturb off |
| global | `airplane_mode_on` | 0 | |
| global | `wifi_on` / `bluetooth_on` | 1 / 1 | |
| global | `low_power` | 0 | battery saver off |
| global | `device_name` | Pixel 10 | |
| system | `screen_off_timeout` | 1800000 | 30 min |
| system | `screen_brightness_mode` | 1 | auto |
| system | `accelerometer_rotation` | 1 | auto-rotate on |
| secure | `location_mode` | 3 | high accuracy |
| secure | `default_input_method` | LatinIME | |
| secure | `lock_screen_show_notifications` | 1 | |

### `settings get system volume_music` is not the volume
**Verified:** 2026-08-23, Android 16 (sdk 36).

Read back to back, they disagree:

    settings get system volume_music          → 5
    cmd media_session volume --stream 3 --get → volume is 17 in range [0..25]

17 is the true live value — it is what actually changed when the volume was
set, and what it was restored to. `volume_music` in `settings` is not the
live stream index (it does not track the active output device), and a caller
that reads it gets a plausible number that is simply wrong.

**For the live music volume use `cmd media_session volume --stream 3 --get`.**
Another instance of the same lesson as the media-session decoy: a real value
from a real table is not automatically an answer to your question.

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
