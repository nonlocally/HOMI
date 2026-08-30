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
**Route:** none. **Established:** 2026-08-23. **Premise corrected:**
2026-08-23 — see below.

The conclusion stands and the reason for it is unchanged: a `PendingIntent` is
a live Binder token held in `system_server`, and `dumpsys` prints only a
*description* of it. There is no path from that text back to a fireable
object, by design. Firing one needs a process holding the live
`Notification.Action` — a bound `NotificationListenerService`. Termux:API has
listener access on this device but exposes list-only; reply is unimplemented
upstream.

**The premise was wrong, though, and it is worth correcting loudly.** This
entry used to say there were "26 live direct-reply (`RemoteInput`) actions in
the notification dump at any given moment, so the capability is right there".
There are not. All 26 hits are one extras key, printed once per notification
record and **null every time**:

    $ phone sh 'dumpsys notification --noredact 2>&1 | grep -i remoteInputHistory
                | sort | uniq -c'
    26 android.remoteInputHistory=null

`android.remoteInputHistory` is a standard field on every notification, not an
action. Counting it counted notifications. Searching for actual reply actions
finds **none** — `grep -c "Action\["` returns 0 — and the packages owning
those 26 lines are Weather, Turbo, Tailscale, Apple Music and YouTube, none of
which has a reply box.

So the honest state is stronger than "we cannot fire one": **there is
currently nothing to fire.** This phone has no messaging notifications at all,
which is consistent with an out-of-service SIM, an empty SMS table, and no
third-party messenger installed. Whether the shell could reach a reply action
if one existed remains unproven either way — and untestable here without
someone sending a message, which the charter forbids arranging.

Note also that a case-sensitive `grep RemoteInput` returns **0** while
`grep -i` returns 26.

**Update, 2026-08-24 — there is now a route.** `phone reply` exists:

    phone reply --list                 what can be replied to right now
    phone reply "<key>" "<text>"       reply to one

It works the way this entry said it would have to: not from shell, but
through a bound `NotificationListenerService` — `com.aadarwal.phonebridge`,
now listed by `capabilities` under `notification_listeners`. The listener
holds the live `Notification.Action` that `dumpsys` could only describe.

`--list` is a read and is verified: it returns
`0|com.termux.api|0|phone-voice|10327 … ember` — the phone's own
press-to-talk notification, which is a repliable target that is not a person.

**Sending a reply is untested here and stays that way.** Proving it works
means messaging somebody, which the charter forbids. So: the *route* exists
and is confirmed present; whether a reply *lands* is somebody else's
observation to record.

Note the counting trap that produced the original wrong premise is now fixed
at the source — `capabilities` reports `notification_replies` honestly rather
than counting `remoteInputHistory` keys.

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

### What `--json` actually carries
**Verified:** 2026-08-23, Android 16 (sdk 36), 23 notifications from 12 apps.

Exactly **eight** fields, and only three are always populated:

| field | populated | notes |
|---|---|---|
| `key` | 23/23 | `userId\|pkg\|id\|tag\|uid` — the join key to `dumpsys` |
| `packageName` | 23/23 | |
| `when` | 23/23 | but see below — one of them is 1969 |
| `title` | 17/23 | empty on every group summary |
| `content` | 16/23 | |
| `tag` | 15/23 | `null` for single-notification apps |
| `group` | 13/23 | set only for grouped apps |
| `id` | 10/23 | often 0 |

**`lines`, `bigText` and `text` never appear.** `body_of()` falls through to
them for MessagingStyle notifications — group chats, the ones that matter —
but on this device the listener does not emit them at all, so that fallback
cannot fire. Whether a real MessagingStyle notification would carry them here
is **untested**: there are none on this phone to try (see below).

`notifs` does **not** go through the corrupting bulk-read path. It calls
`termux-notification-list` — a real `NotificationListenerService` — which
returns ~8 KB. The equivalent `dumpsys notification --noredact` is **545 KB**,
and reading *that* through the bridge is exactly the hazard described under
"Large reads". Five consecutive `notifs --json` runs were byte-identical.
**This is the one bulky-looking surface you can trust.**

### Group summaries, structurally
**Verified:** 2026-08-23. 6 of 23 were summaries.

A summary has **`group` set, and `title` and `content` both empty**. Google's
apps also put `::SUMMARY::` in the `tag`, but the empty-title-and-content rule
does not depend on that convention:

    summaries = [n for n in items
                 if n.get("group") and not n.get("title") and not n.get("content")]

Children carry the same `group` and a `when` within a second of the summary's.
`phone notifs` prints both, which is why YouTube appears twice per video.
Drop summaries and 23 items become 17 real ones.

### Telling a real event from app chrome
**Verified:** 2026-08-23, Android 16 (sdk 36).

The `--json` fields cannot do it — nothing in those eight says "this is an
event". The signal is `flags`, which lives only in `dumpsys`:

| flags | what it is | examples here |
|---|---|---|
| `ONGOING_EVENT` / `NO_CLEAR` / `FOREGROUND_SERVICE` | permanent service chrome, never news | phonebridge, Termux, Tailscale |
| `AUTO_CANCEL` | a real, dismissible event | weather, GMS, Willow, YouTube, Turbo |
| `ONLY_ALERT_ONCE\|NO_CLEAR` | media transport | Apple Music |

Filter it on the device so the read stays small:

    phone sh 'dumpsys notification --noredact 2>&1 |
              grep -o "pkg=[^ ]* .*flags=[A-Z_|]*"'

**But the discriminator you actually want is not available on this phone.**
A notification with a *direct-reply action* is almost certainly a message from
a person — and there are **zero** of those here (next section). Every one of
the 23 notifications present is app noise. So "tell a real message from noise"
is, today, untestable on this device rather than solved.

### `when` — three traps
**Verified:** 2026-08-23, Android 16 (sdk 36).

1. **It is device-local time, and the controller's clock disagrees.** Read
   back to back: controller `23:31:32`, device `22:31:34` — **an hour apart**.
   `--since` compares against these device-local strings, so a window computed
   from the controller's clock is off by an hour and silently returns the
   wrong set. Get "now" from the phone: `phone sh 'date "+%Y-%m-%d %H:%M:%S"'`.
2. **Not every notification sets it.** Apple Music's reads
   `1969-12-31 18:00:00` — epoch 0. Since `phone notifs` sorts by `when`
   descending, **the currently-playing track sorts last**, looking like the
   oldest thing on the phone. Media and ongoing notifications are the ones to
   watch for.
3. A summary and its children share `when` to within a second, so it is no
   help in separating them. Use the structural rule above.

In `dumpsys` the same value appears raw as `when=<epoch-millis>/<epoch-millis>`.

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

## Where the phone is

### Read the current location
**Route:** state. **`phone where` does not work.**
**Verified:** 2026-08-23, Android 16 (sdk 36).

`phone where` exits **1 with zero bytes and no message whatsoever**. It shells
out to `termux-location`, which fails here; `cmd_where` prints only stdout and
returns 1, so the reason never reaches you. A verb that fails silently is
worse than one that fails — you cannot tell it apart from "no location".

The state tier has the answer:

    phone sh 'dumpsys location 2>&1 | grep -m1 "last location="'
    → last location=Location[network <lat>,<lon> hAcc=100.0
        et=+2d11h54m48s258ms alt=-8.0 vAcc=34.25882 ...]

`Location Setting: true`, provider `network`, `hAcc=100.0` — 100 m.

**`et=` is an absolute stamp, not an age — read it carefully.** It is the
time *since boot* at which the fix was taken, so a fix from one minute ago on
a phone that has been up for days reads `et=+2d11h54m48s`. Taking that as an
age would make you throw away a perfectly fresh location. The age is:

    age = (time since boot) - et

Measured together: `/proc/uptime` = **215784.84 s**, `et` = 2d11h54m48.258s =
**215688.26 s**, so the fix was **97 seconds old** — not two and a half days.

    phone sh 'cut -d" " -f1 /proc/uptime; dumpsys location 2>&1 |
              grep -m1 "last location="'

Read both in the same call, or the subtraction drifts.

This is the "climb down, never up" case in miniature: the verb built for the
job is broken, the tier above it is unavailable, and a `dumpsys` grep answers
in one call — provided you read the timestamp for what it is.

### The clipboard
**Route:** none established. **Verified:** 2026-08-23, Android 16 (sdk 36).

`phone clip get` returns rc=0 and **zero bytes**, which cannot be told apart
from an empty clipboard. There is no state-tier fallback either:

    $ phone sh 'cmd clipboard get-primary 2>&1'
    No shell command implementation.

The clipboard service exposes no shell command on this build. Android has
restricted clipboard reads to the foreground app and the active IME since
Android 10, so a background read returning nothing is the expected outcome
rather than a bug — but **that is inference, not observation**: this entry
cannot distinguish "empty" from "blocked", and no read here would settle it
without putting known content on the clipboard first. That would overwrite
whatever the owner had copied, which is destroying something this agent did
not create, so it was not done.

Recorded as: **no reliable clipboard read, and the failure mode is silent.**
Deliberately not tested further.

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

### Large reads from the device come back CORRUPTED — silently
**Verified:** 2026-08-23, Android 16 (sdk 36), controller over the forwarded
socket. **This is the most consequential thing in this file.** It is not a
screen-tier bug; the screen tier is just where it shows up first.

**Symptom.** `phone ui` and `phone look` fail intermittently on a complex
screen — measured **6/10**, **6/10**, and **2/8** (spacing calls 4s apart) on
YouTube Music — with four different parser errors at wandering offsets:

    not well-formed (invalid token): line 1, column 8192
    mismatched tag: line 1, column 50457
    duplicate attribute: line 1, column 9977
    no element found: line 1, column 0

**It is not the phone.** `uiautomator dump` run ten times in one on-device
loop produced ten byte-identical, well-formed files, every one ending
`</hierarchy>`. The XML the device writes is fine.

**It is not truncation.** Received byte counts match the device's `wc -c`
*exactly*, on good and bad reads alike.

**It is corruption in the read back.** Diffing a good against a bad capture
of the *same* file:

    sizes: 79894  79894          (identical)
    first differing byte: 65536  (= exactly 8 x 8192)
    the bad file's bytes at 65536 are found in the good file at offset 5610

So at a 64 KiB boundary the reader **splices in bytes from earlier in the
same stream**. Same length, plausible-looking XML, wrong content. In the
capture above, `bounds="[200,1364][339,1490]"` runs straight into
`ce-id="android:id/icon"` — the tail of a `resource-id` from 60 KB earlier.

**It is not specific to UI dumps.** With a deterministic file — `seq 1 30000`,
169 KB, so every line states what it should be — **five of six reads came
back scrambled**, some beginning at line `1861` instead of `1`, one splicing
`1860` and `2680` into `18602680`. Where corruption starts:

| file size | intact reads |
|---|---|
| 3.9 KB | 4/4 |
| 8.9 KB | 2/4 |
| 23.9 KB | 2/4 |
| 60.9 KB | 0/4 |
| 78.9 KB | 3/4 |
| 168.9 KB | 1/4 |

Clean below ~4 KB; unreliable from ~8 KB — the pipe-buffer boundary — and it
does not improve with size. **Any `phone` read over a few KB may be quietly
wrong**: `dumpsys` output, notification dumps, package lists, anything bulky.

Disabling the persistent shell (`phone shelld --stop`) did **not** change the
rate (still 6/10), so `shelld` is not the sole path involved — though 65536
is exactly its `recv` chunk size, which is worth someone's attention.

**Why nothing catches it.** The read exits 0. The length is right. The
document still contains `<hierarchy` and still ends `</hierarchy>`, so
`get_ui`'s guards pass. Only a *parser* notices, and only because XML happens
to be brittle. **A corrupted `dumpsys` read would have been used as fact.**
The UI tree is the canary, not the victim.

### Fixed, and re-measured — **2026-08-24**

`get_ui` now goes through `dev_shell_checked` (`lib/phone:1289`), and the
screen tier is **clean**:

| | `look` | `ui` |
|---|---|---|
| before the checked read | 4/8 failed | 6/10 failed |
| after (2026-08-24) | **0/12 failed** | **0/12 failed** |

Same app (YouTube Music), same kind of screen, comparable tree — 76 061 bytes
against the 66–80 KB the baseline ran on — and the binary confirmed to carry
the checked read before measuring. 24 of 24 clean.

So the corruption below was the whole cause of the screen tier's
unreliability, not a symptom sitting alongside it. **The tier ladder still
holds** — the screen is still ~5x the wall clock, still 20–40x the output,
and still the only route that cannot check its own work — but "`look` fails
outright a fair fraction of the time" is **no longer true on this build**.

The underlying transport hazard is unchanged for anything that does *not* go
through a checked read; that is what the rest of this section is about.

### Reading something big, safely
**Verified:** 2026-08-23.

Ask the device for the checksum, then read until you match it:

    phone sh 'md5sum /sdcard/x.xml'        # the truth
    phone sh 'cat /sdcard/x.xml' > local   # the read (strip 1 trailing \n)
    # compare; retry on mismatch

Five trials against a 78 KB dump converged in **3, 5, 2, 1, 1** attempts —
always, and cheaply. Anything under ~4 KB does not need this.

`phone ui --from <file>` / `look --from` / `media --from` accept a local
capture, so a verified read can be parsed offline as many times as you like.
Note the two-step capture is **not** a workaround by itself — it failed 3/8
until the md5 check was added. It is the *verification* that fixes it, not
the extra step.

### When the tree is genuinely unreadable
`uiautomator dump` also fails outright sometimes — it waits for window idle
and gives up on anything animating, on secure windows, and with the screen
off. Then the file is simply absent, and `phone sh 'cat ...'` returns
`No such file or directory` (51 bytes that are easy to mistake for content if
you only check the length — I did, for one round).

`grep` on the raw dump still works when the XML will not parse, and does not
care about well-formedness:

    phone sh 'uiautomator dump /sdcard/d.xml >/dev/null 2>&1;
              grep -o "text=\"[^\"]*\"" /sdcard/d.xml'

That is how the YouTube Music search box was read. But **grep on a corrupted
read is corrupt too** — it just cannot tell you so. Verify first.

---

## Reading the screen — what it costs, and what it gets wrong

### The dump is the cost, and it is on the device
**Measured:** 2026-08-30, controller over the forwarded socket.

| step | time |
|---|---|
| `uiautomator dump` alone | **3.11s** |
| shipping the resulting 61 KB back, `--checked` | **0.54s** |
| `uiautomator dump --compressed` (44 KB) | 2.87s |
| `dumpsys activity top` instead | 5.59s |
| one `phone sh` round trip | 0.31s |

This corrects the instinct the batching entry above encourages. For *shell
probes* — a dozen `settings get` — putting the loop on the device is a large
win, and that entry stands. For the **UI tree it is not**: the expensive part
is `uiautomator dump` waiting for window idle, which happens on the phone
whoever asks. Filtering the XML on-device to ship fewer bytes optimises the
0.54s and leaves the 3.11s alone.

What actually pays is **taking fewer dumps**. Hence `tap --label` (resolve and
tap in one call, so a coordinate never round-trips through the caller's
reasoning) and `scan` (the scroll-until-found loop, on the device: three
screens of a DoorDash item sheet in 10.9s and one call).

It also means the honest 10x here is not a faster transport. It is an
**AccessibilityService** in phonebridge: the tree without the window-idle
wait, on screens where `uiautomator dump` fails outright, and
`performAction(ACTION_CLICK)` on a *node* instead of a point — which would
delete the occlusion problem below rather than working around it. Not built;
it needs the owner to grant Accessibility once.

### The centre of what you named can be inside something else
**Verified:** 2026-08-30, DoorDash, Android 16 (sdk 36).

`find` returns the centre of the clickable ancestor, which is right until an
app floats a bar over its own content. Two cases, both watched:

- A store page's menu row `container_card_view` `[42,1240][1038,1466]`, centre
  **(540,1353)** — inside the floating cart bar `button_orderCart`
  `[21,1319][1059,1445]`. The tap opened the cart.
- An item sheet's option row `[0,2130][1080,2292]`, centre **(540,2211)** —
  inside `addToCart_button` `[42,2171][1038,2297]`. That tap would have added
  the item instead of selecting the option.

`input tap` exited 0 both times, `find` was confident both times, and nothing
below the screen tier could have told you.

`find`/`look`/`tap` now treat the **latest** clickable element in the tree
whose bounds contain a point as the one on top, and prefer a point in the
target that nothing later covers — usually a band above the overlay. Both
cases resolve correctly now (364,1316 and 540,2136).

### …but "later in the tree" is not "drawn on top" under Compose
**Verified:** 2026-08-30. **This is why the check warns instead of refusing.**

On DoorDash's home feed the bottom navigation bar is at tree index 16 and a
promo card at index 159, and the card's bounds `[42,1731][1038,2291]` swallow
the nav item at `[74,2161][779,2287]` — yet the nav is what a finger hits.
Compose emits semantics nodes in traversal order, not draw order, so the rule
simply does not hold there.

A veto would therefore make real buttons unreachable, which is worse than the
failure it prevents. So the point is still returned and `look` marks it:

    426,2224    [Search DoorDash]  ?under View

Read `?under` as "check this one", not "do not touch".

### Coordinates move between visits to the same screen
**Verified:** 2026-08-30.

The same DoorDash item sheet, reached once by tapping a menu row and once by
deep link, placed the same option at y=2043 and y=1862 — the deep-linked entry
has no store header. **A coordinate is only good for the dump it came from.**

### `input text` types into nothing and says it worked
**Verified:** 2026-08-30.

A DoorDash item's Special Instructions box renders `enabled="false"` with the
store's refusal message as its text, while still reporting
`clickable="true"`. Tapping it and typing succeeds at every layer, and `type`
counted every character as delivered.

`type` now reads `dumpsys input_method` first (~0.65s, against 3.11s for a
dump) and refuses when nothing is accepting text:

    phone: nothing is accepting text right now — the window itself is served,
    not a text field.

**Its limit, measured:** `mServedView` goes **stale**. After leaving a search
field, dumpsys still named that field as served — enabled and focused — while
the screen had moved on. So the check catches a screen with no field (the
served view is the window's `DecorView`, which is the case that bit us) and
misses a field whose window has just closed. It never wrongly refuses. After
typing something that matters, read it back.

Also note three lines of `dumpsys input_method` contain the string
`mServedView`, and the useful one is the anchored `^ *mServedView=`; the
`mFallbackInputConnection` line above it always carries `mServedView=null` as
a substring, so `grep -m1 mServedView` reports "nothing focused" while a field
is focused.

### `dev_shell_checked` and a trailing newline
**Verified:** 2026-08-30. A tooling fact, recorded because it cost an hour.

`dev_shell_checked` wraps its argument as `{ <cmd> ; } > file`. A command that
**ends in a newline** therefore produces a line beginning with a bare `;`, a
syntax error that kills the persistent shell — and the failure surfaces as

    the read did not survive the trip after 4 attempts — no checksum

which points at the known corruption bug rather than at the quoting. Multi-line
scripts are otherwise fine (the daemon reads the whole payload, not one line).
`.strip()` anything you build before passing it.

---

## More than one screen

### A second display, unrooted, that apps really run on
**Route:** state (a held process). **Verified:** 2026-08-30, Android 16
(sdk 36), Pixel 10.

    d=$(phone display create)      -> 6
    phone --display $d open chrome
    phone display ls
    -> 0   Built-in Scr  1080x2424  dpi 420  internal  ...nexuslauncher
       6   phone-vd-1    1080x2424  dpi 420  virtual   com.android.chrome

Three apps were resumed at once — launcher on 0, Chrome on 6, Calendar on 7 —
and the built-in screen never changed. `dumpsys activity activities` lists a
`Display #N` section per display, each with its own `topResumedActivity`.

**Why it works, since it looks like it should not.** The display must be
TRUSTED to be useful; an untrusted one silently loses IME, own focus and
system decorations. TRUSTED needs `ADD_TRUSTED_DISPLAY`, which is
`signature|role` — `pm grant` refuses it as "not a changeable permission
type". But **com.android.shell holds it** (in AOSP's
`packages/Shell/AndroidManifest.xml`, and has since Android 13), and
`phone sh` is already uid 2000 via Shizuku. So the holder runs under
`app_process` at the uid that already has the permission. Nothing is
bypassed. This is the mechanism behind scrcpy's `--new-display`.

**`getSystemService` is not enough, and the error blames the wrong thing.**
`ContextWrapper.getSystemService()` delegates to the BASE context, so the
DisplayManager comes back holding the system context, whose package is
`android` — owned by uid 1000. Sent from a uid-2000 caller it fails:

    SecurityException: packageName must match the owner uid

which reads as a permission problem and is a naming one. The manager has to
be constructed against a Context that reports `com.android.shell`.

### What each tool does with a display

| tool | flag | works? |
|---|---|---|
| `am start` | `--display N` | yes, on **any** display — shell holds `INTERNAL_SYSTEM_WINDOW`, which skips the trust check |
| `input` | `-d N` | yes. `-d`, never `--display` |
| `wm size` | `-d N` | yes |
| `uiautomator dump` | `--windows` | yes, but see below |
| `screencap` | `-d N` | **no** — see below |

### `screencap -d` cannot see a virtual display
**Verified:** 2026-08-30.

`screencap` addresses SurfaceFlinger's **physical** display ids, a different
namespace from the WM/AM ids that `am start --display` and `input -d` use. A
virtual display has no physical id, so the flag quietly returns the built-in
screen or nothing. The only way to pixels is for the process that owns the
display's Surface to read it back — which is why the holder carries an
ImageReader and answers `cap <path>`.

A virtual display renders only when its content **changes**, so the holder
has to RETAIN the newest frame. The first version closed every image as it
arrived and `cap` answered "no frame available yet" permanently on any
settled screen.

### `uiautomator dump --windows` — the shape, and the two traps
**Verified:** 2026-08-30.

    <displays><display id="0"><window index="0" ... layer="1"><hierarchy>...

**Trap 1: windows are listed TOP-FIRST.** The status bar (`layer=1`) comes
before the application window (`layer=0`). Everything that reads a tree
assumes the opposite — inside a hierarchy a *later* sibling is drawn on top,
which is what the occlusion check relies on. Merging in document order tells
the tap resolver the status bar is underneath the app. Merge bottom-first.

**Trap 2: it returns EVERY display, and that read corrupts.** Measured at
121 KB with two extra displays, and the checked read failed 4 attempts
running — the same large-read corruption as everything else this size. Cut
the display out **on the device** before shipping it back. `sed` does it in
one pass, because the document is a single line:

    sed -e 's|.*<display id="7">|<display id="7">|' -e 's|</display>.*|</display>|'

### `grep -m1 ResumedActivity` stops meaning "display 0"
**Verified:** 2026-08-30. **A silent wrong answer, not an error.**

dumpsys prints **virtual displays before the built-in one**, so the old
foreground probe returned whichever display it printed first. Measured: with
a clock on display 5, `phone foreground` reported the clock while the person
was looking at their launcher. `cmd_msg` gates *sending* on that check. Every
foreground probe now scopes to a `Display #N` section first, display 0
included.

### Releasing a display MOVES its apps to the person's screen
**Verified:** 2026-08-30, then fixed.

Killing two holders put Settings in front of the launcher with nothing to
explain it. `VIRTUAL_DISPLAY_FLAG_DESTROY_CONTENT_ON_REMOVAL` (1 << 8) makes
a released display take its work with it.

### One app cannot be on two displays
**Verified:** 2026-08-30.

`am start --display` on a package that already has a task elsewhere does not
make a second copy — it **moves the task**. The agent driving it on the old
display loses its app mid-turn, with no error anywhere. `phone open` refuses
first (exit 5). Displays multiply screens, never apps: one process, one
account, one notification stream behind a package however many displays point
at it.

### A lane is a shell of your own
**Verified:** 2026-08-30. **Measured 8.6s -> 4.3s.**

`shelld` is one single-threaded accept loop feeding one persistent `sh`, so
every driver's commands queue behind every other driver's — a 3s
`uiautomator dump` is 3s nobody else's `settings get` can run. Two 4-second
commands took **8.6s** through the shared daemon and **4.3s** in two lanes:

    PHONE_LANE=alpha phone --device <dev> sh '...'   # its own daemon
    PHONE_LANE=beta  phone --device <dev> sh '...'

The default lane is unchanged and shared, so this is opt-in. The lease is
keyed by display *inside* a daemon, so two drivers sharing a lane still
arbitrate and two drivers in different lanes do not see each other's leases —
acceptable only because a lane is something you choose.

### What a display does NOT give you

The microphone, the speaker, the notification shade and the person are all
still singular. `say`, `listen`, `talk`, `notify` and `ptt` are one-at-a-time
no matter what `--display` says. `FLAG_SECURE` content (banking, DRM) renders
**black** on any virtual display — SurfaceFlinger allows secure-display
creation only to AID_GRAPHICS/AID_SYSTEM, and root does not help.

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
