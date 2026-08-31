---
name: communicate-wake
description: Waking agents on a schedule or event — recurring nudges and new-PR triggers. Use when asked to wake, nudge, poll, keep-alive, or notify an agent every N seconds/minutes or when a repository gets a new pull request.
---

# communicate-wake — triggers

**A message IS a wake**: an inbound peer message resumes an idle or finished
session as a new turn. `wake` is just a trigger loop that routes a message when
its condition fires.

## Timer

```sh
communicate wake <name> --every 300                      # nudge every 5 min
communicate wake <name> --every 60 --times 10 --message "status?"
```

Default message is a gentle keep-working nudge; `--times N` stops after N
firings.

## Event: new PR

```sh
communicate wake <name> --on-pr owner/repo [--every 60] [--catchup]
```

Polls `gh pr list` (needs an authenticated `gh` CLI); fires ONCE per new PR
number. The seen-set is seeded at start so only genuinely new PRs fire —
`--catchup` fires for currently-open ones too.

## Manage

```sh
communicate wake ls                 # mode / repo / period / fired count / up?
communicate wake stop <name|all>
```

## When NOT to use wake

From inside a Claude Code session, a one-shot "tell me when that session is
done" is better served by the native `SendMessage` tool's `notify_when_idle`
option — no polling loop. Use `wake` for recurring schedules, PR events, or
when driving from scripts/cron outside any session.
