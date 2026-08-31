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

A PCI card widget is not a special case. DoorDash's number field is a
`com.verygoodsecurity.vgscollect.view.internal.CardInputField`, not a plain
EditText, and it takes `input text` normally — all four fields filled and read
back correctly. If a card field does not accept a fill, suspect focus, not the
widget.

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

**The push is real, but do not treat it as guaranteed.** The Link app pushes
the approval to the person's phone, and that is the primary channel — it is
how these actually get approved, in about 46 seconds start to finish. But one
push in testing did not arrive, and `await` simply sits there when that
happens. So still surface the `approval_url` through whatever channel the
request came in on: a terminal session prints it in the terminal, a spoken
`talk` turn speaks it. Belt and braces, because the failure mode is a silent
hang and the fix costs one line of output.

**When the push IS the only channel, `--context` carries the entire weight.**
An unattended or scheduled agent may mint — the person really will be told.
But they will then be approving from a notification with no conversation
around it, no screen to glance at, and no way to ask you what this is. Every
scrap of what they need to judge it has to already be in that sentence. If you
would not be comfortable with them deciding on the context line alone, you are
not ready to mint.

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
- **Uncheck "make this the default" before saving a minted card, anywhere.**
  Add-card forms routinely pre-tick something like *Default for DashPass
  subscription* — DoorDash's is checked on arrival. Pointing a recurring
  charge at a credential that dies in an hour is a broken subscription you
  handed the person. If a form offers to remember this card, the answer is no:
  it is a one-time card and remembering it is the one thing it must not do.
- **If you are practising, pass `--test` — but see the trap below.**
- Take the lease before acting: `phone lease acquire --as <you>`.

### `--test` proves the mechanism, not acceptance

A testmode card **will be rejected by a real checkout form**, and the
rejection looks exactly like a bug you just introduced.

The testmode number issued here, `4000009990001984`, is **not Luhn-valid**:
its checksum is 57, and 57 mod 10 = 7. Client-side validators check Luhn
before anything reaches a network, so DoorDash renders *"Invalid card number"*
under the field — with the digits correctly typed and `chars_typed` matching.

So when practising: `--test` confirms mint, approval, fill, read-back and
shred. It confirms **nothing** about whether the merchant accepts the card.
Seeing "Invalid card number" after a testmode fill means the flow worked. Do
not go hunting for a fill bug, and do not "fix" it by retyping.

## This skill does not override a per-app boundary

**If a per-app skill says stop at the cart, stop at the cart.** Today the
`doordash` skill does. Having a safe card does not grant you passage; the
per-app skill wins until it has itself been updated to say otherwise, by
someone who has actually mapped that app's checkout. Do not read this file as
permission and do not adjudicate the difference at a live checkout screen.

The reason is that **there are two different risks here and this skill only
addresses one of them.**

*The money risk* — spending without the person's say-so. That one is handled:
no card exists until they approve a named amount.

*The mis-tap risk* — the screen route cannot check its own work, and on a
crowded checkout it can act on something you did not mean. This is not
hypothetical. On DoorDash, measured twice in one session: an option row's
centre at (540,2211) sits **inside** `addToCart_button`, and a menu row's
centre sits inside the floating cart bar. Both taps exited 0. The same
accident on a checkout screen taps Place Order — and an approved card in the
vault is exactly what makes that tap go through.

Checkout makes it worse in a way worth generalising: **an id can be reused
across screens, so the id does not tell you what the button does.** DoorDash's
footer is one component, `button_place_order`, on *every* checkout screen — it
reads `Next` on the delivery step and advances, and `Place order` on the last
step and spends. `Continue`, `Next`, `Place order` and `Add card` all sit at
**540,2224**. So guarding on the id paralyses you, trusting the id stops you
two screens early, and reusing the coordinate buys food. Only the rendered
label (`textView_prism_button_title`) separates them. **Resolve by visible
text, every time, and never reuse a coordinate.**

And do not assume an empty wallet fails safe. On this account no *card* is
saved, but Google Pay is a saved method and is what checkout previews — so a
stray Place order attempts a real payment rather than erroring out. "They have
no card on file" is not a safety net.

So the approval gate does **not** cover the mis-tap risk. A per-app skill's
stopping point is what covers it, which is why that stopping point outranks
this file. When one is eventually moved, the work is: map the checkout for
real, then `find --all` before every tap, never `--raw`, and treat any
`?under` marker as a full stop.

What never changes under any reconciliation: never touch the person's saved
payment methods, stored addresses, tips, subscriptions, or refund flows.
Those speak to a merchant in their name and have nothing to do with this card.

## If you remember three things

A per-app skill's stopping point outranks this file — a safe card is not
permission to go further. Mint only for a total you have actually read, and
put it honestly in the `--context`, because that sentence is the whole
protection and it may be the only thing they ever see. And surface the
`approval_url` in your own channel too: the push is the primary route and it
works, but it is not guaranteed, and a missed one is a silent hang.

Dated specifics — what each app's checkout form calls its fields, which ones
refuse a slash in the expiry — belong in the per-app `map.md`, not here. This
file is the method.
