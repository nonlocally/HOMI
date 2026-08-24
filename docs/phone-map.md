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

Untested: whether YouTube Music honours it (it declares it). Worth being the
first thing checked.

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
