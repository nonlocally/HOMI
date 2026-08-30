---
name: pay
description: Pay for something on the phone with a one-time virtual card the person approves per purchase. Use when a task on the phone reaches a checkout, a payment form, a card field, or asks to buy, order, book, or pay for something. Also use when deciding whether an agent may go past a cart.
---

# Paying from the phone

You are spending a real person's money. Not "one step away from it" — this
skill is the step. Everything below exists to make that safe enough to do,
and none of it works if you route around it.

The rule that makes this safe is not a policy you follow. It is structural:
**no card exists until the person approves this specific purchase.** You
cannot spend by mistake, because until a human taps approve there is nothing
to spend with. Asking costs nothing and charges nothing.

Read the `phone` skill first — the route ladder, the lease, and "verify by
reading state back" all apply here unchanged.

## What you get, and what you never get

You get a card that is **single-use, amount-capped, merchant-named, and dead
in about an hour** (measured: `valid_until` is issue + 61 minutes). If it
leaks, what leaked is a spent, expiring credential — not the person's card.
Their real card is never shared and never reaches this device.

You never get the digits. Not in a variable, not in a return value, not in
your context. You mint a card and then fill fields **by name**. This is not
politeness — it is why the number cannot end up in your transcript or in the
action ledger, both of which are durable and neither of which should hold a
PAN.

**Never type a card number with `phone type`.** That verb records
`detail.requested` verbatim to the ledger, so it would write the number to
disk. `phone-pay fill` exists precisely so you never need to.

## Where this runs, and why it is not optional

`phone-pay` runs **on the phone, as the Termux user.** Not on a controller.

The Link token lives in Termux app-private storage. A shell-uid process —
which is what `phone sh` and every screen verb run as — cannot read it.
Measured from uid 2000: `cat` of the token is `Permission denied`, and so is
a bare `ls` of `/data/data/com.termux`. So the mint happens where the token
is, and the number never crosses a machine boundary.

From a controller, reach it over ssh:

```
ssh aadarshs-pixel-10 'phone-pay status'
```

On the phone, just `phone-pay`. Every example below is identical either way.

## The four verbs

```
phone-pay mint --amount <cents> --merchant "<name>" \
               --merchant-url "<url>" --context "<why, min 100 chars>"
phone-pay await                  # blocks until the person decides
phone-pay fill <field>           # types into the CURRENTLY FOCUSED input
phone-pay done                   # shreds the credential
```

`status` at any time says what is held, redacted.

**`mint` returns an `approval_url` and nothing else of value.** Show that URL
to the person. Then `await` blocks until they decide. On denial you get
`status: denied`, no card, and the held request is discarded — that path is
tested and it works.

**`fill` takes a field name**, one of: `number`, `cvc`, `exp`, `exp_month`,
`exp_year`, `zip`, `name`, `line1`, `city`, `state`, `country`. Focus the
field on the screen first — `fill` types into whatever is focused, exactly
like a keyboard. `--digits-only` strips the slash for expiry fields that
refuse it.

A worked checkout:

```
phone tap --label "Card number"        # focus it first
ssh <dev> 'phone-pay fill number'
phone look                             # READ IT BACK — see below
phone tap --label "MM/YY"
ssh <dev> 'phone-pay fill exp'
phone tap --label "CVC"
ssh <dev> 'phone-pay fill cvc'
```

## Four things this will do to you

**`fill` says "dispatched", not "filled", and means it.** `input text` exits 0
whether or not any field consumed the keystrokes. If nothing was focused, the
digits went nowhere and you were told success. **Read the field back with
`phone look` after every fill.** The last four digits are printed by `status`
precisely so you can check the field shows the right card without ever
knowing the whole number.

**The approval does not reach the person on its own.** There is no push today.
`mint` posts a notification on the Pixel and prints the URL — but if the
person is not looking at the Pixel, *nothing tells them*, and `await` will sit
there until it times out. **You must deliver the `approval_url` to wherever
they actually are.** Do not mint and then wait silently; that is the single
most likely way this hangs.

**A card minted before the form is ready burns its clock.** An hour is plenty,
but the whole round trip — ask, human decides, fill — was measured at 46
seconds when the person was watching. Get the checkout form on screen, *then*
mint.

**The device clock is not UTC.** `valid_until` is UTC and this bit once
already: a card minted two seconds earlier reported `-2` seconds left and
refused to fill. It is fixed, but if `expired: true` shows up on a fresh card,
suspect the clock before you suspect the card.

## The boundary

- **Mint only for a purchase the person asked for**, at the amount actually
  shown on the checkout. Not a rounded-up amount, not a guess, not a
  "probably about". If you cannot read the total, do not mint.
- **The `--context` is not boilerplate. It is the only thing the person reads
  before approving.** Minimum 100 characters, enforced. Say what is being
  bought, from where, and why. A vague context trains them to approve blind,
  which quietly destroys the one protection in this whole design.
- **Never mint to "have one ready."** No speculative cards, no reuse across
  purchases, no keeping one alive between tasks. `done` when the form is
  submitted or abandoned.
- **Never fill a field you did not just focus and cannot read back.**
- **One `--test` rule: if you are practising, pass `--test`.** Testmode
  credentials cost nothing and behave identically.
- Take the lease before acting: `phone lease acquire --as <you>`.

## How this changes the per-app skills

The `doordash` skill says *never tap Place Order* and *never touch payment
methods*. That boundary exists because there was no safe way to pay — not
because stopping at the cart was the goal.

This skill moves the boundary rather than removing it. With `phone-pay` the
stopping point is no longer the cart; it is **the approval**. The agent may
build the cart, reach the checkout, read the real total, and mint for that
total — and then it stops dead until a human approves. The human still
decides, on the actual number, before any money moves.

What does **not** change: never touch the person's saved payment methods,
their stored addresses, tips, subscriptions, or refund flows. Those speak to
a merchant in their name and have nothing to do with this card.

## If you remember three things

Mint only for a total you have actually read, and put that total honestly in
the `--context`, because that sentence is the whole protection. Deliver the
`approval_url` to where the person really is — the phone will not tell them.
And after every `fill`, read the field back, because "dispatched" is not
"landed".

Dated specifics — what each app's checkout form calls its fields, which ones
refuse a slash in the expiry — belong in the per-app `map.md`, not here. This
file is the method.
