# The DoorDash map

What can actually be done in DoorDash on this phone, established by trying it.

This is the app-level twin of the `phone` skill's `map.md`. That file is the
territory of
the *device*; this one is the territory of *one app*, because an app is where
an agent actually spends its time and where the tier ladder gets interesting.
DoorDash is a closed app with no content provider and no readable state — so
the whole question is how much of it can be reached by **intent** before
falling to the **screen**, and it turns out to be a great deal more than it
looks.

Same rules as the phone map. Every entry names the route that worked and the
date it was verified. Failed attempts belong here too, and are usually the
more useful half. If an entry conflicts with what the app does now, the app is
right.

**Verified:** 2026-08-30, DoorDash on aadarshs-pixel-10, Android 16 (sdk 36),
account logged in, Cambridge/Somerville MA delivery address.

---

## The one-paragraph version

`doordash://store/<store_id>/item/<item_id>` opens an item's configuration
sheet in **0.4 seconds**, skipping app launch, search, store page and menu
scroll — eight-plus screen steps and roughly forty seconds of tapping. Store
and item ids are stable and can be harvested from the app's own Share button.
Everything after that — choosing options, adding to the cart — needs the
screen, and the screen on this app has two overlays that will steal your taps
and one button that lies about being ready.

---

## Route 2 — deep links

### The scheme is registered and browsable
**Route:** intent. **Verified:** 2026-08-30.

    phone sh 'dumpsys package com.dd.doordash | grep -E "Scheme:|Path:"'

`com.dd.doordash/com.doordash.consumer.deeplink.DeeplinkHandlerActivity`
declares schemes `doordash`, `com.dd.doordash`, `http`, `https` (authority
`www.doordash.com`) with **~90 path patterns**. Both forms work and are
interchangeable in everything tested:

    am start -a android.intent.action.VIEW -d "doordash://store/1332398" \
             -p com.dd.doordash

Always pass `-p com.dd.doordash`. Without it the `https` form can be picked up
by a browser instead, and you get a web page that looks almost right.

### What was tried, and what it did

| link | result | route it replaces |
|---|---|---|
| `doordash://home` | home feed | launching the app |
| `doordash://store/<id>` | that store's menu page | search + tap |
| `doordash://store/<id>/item/<id>` | **the item sheet, options and all** | search + store + menu scroll + tap |
| `https://www.doordash.com/search/store/<query>/` | store search results for `<query>` | tap search + tap field + type + submit |
| `doordash://open-carts` | the list of open carts | tapping the cart bar |
| `doordash://orders` | order history | account + orders |
| `doordash://cart` | **home feed. Not the cart.** | — |

`/cart` is registered as a path prefix and the app accepts it and does
something else entirely. This is the same shape as the music entry in
the phone map: **declaring an intent is not honouring it**, and `am start`
exits 0 either way. Use `open-carts`.

The search link takes a URL-encoded query — `search/store/pad%20thai/` — and
lands on the results list with the query echoed in the header, which is how
you know it arrived rather than falling back to the feed.

**Results are ranked, and the store you named may be below the fold.**
Searching "chutneys" returned Madras Dosa Company first, with Chutney's off
screen; a `tap --label "Chutney's"` on that page did not enter the store and
did not complain, while `scan "Chutney's" --tap` did. Use `scan` on any
results list.

### Store and item ids, and where to get one
**Route:** screen, once per store. **Verified:** 2026-08-30.

Nothing at shell level knows these. `dumpsys activity recents` shows only the
intent that *launched* the task, so once you navigate inside the app the ids
are invisible; in-app navigation leaves no trace a shell can read.

The app hands them over through its own Share button:

- On a **store page**: `[Share via]` → "Share the store page" → the sharesheet
  preview contains `https://www.doordash.com/store/<store_id>?utm_source=…`
- On an **item sheet**: `[Share via]` (id `share`) goes straight to the
  sharesheet, whose preview contains
  `https://www.doordash.com/store/<store_id>/item/<item_id>?utm_source=…`

Read it without touching anything:

    phone sh 'uiautomator dump /sdcard/.phone/s.xml >/dev/null 2>&1;
              tr "<" "\n" < /sdcard/.phone/s.xml |
              grep -o -E "https://www\.doordash\.com/[^\"&]*" | head -2'

Then `phone key BACK` twice. Nothing is sent; the sharesheet is only ever
read. Cost: about 15 seconds, once, for an id that is then good indefinitely.

### The index

Harvested this way, dated because a menu can be renumbered:

| store | store_id | item | item_id | verified |
|---|---|---|---|---|
| Chutney's | `1332398` | Chinese Bhel | `24154209014` | 2026-08-30 |
| Nakhon Thai Halal | `30505888` | Pad Thai | `39347246430` | 2026-08-30 |

**A cached id is a shortcut, never an assertion.** Store ids are DoorDash
merchant ids and appear stable; item ids belong to a menu, and a menu gets
re-uploaded. So the rule is: deep-link by id, then **read the item name off
the sheet and check it is what was asked for** before touching anything. That
check is free — it is the `look` you were going to do anyway — and it turns a
stale id from a silent wrong order into a visible miss.

---

## Route 3 — the screen, and its two thieves

DoorDash floats two persistent surfaces over its own scrolling content, and
both sit exactly where the thing you want to tap is.

### The floating cart bar covers menu rows
**Verified:** 2026-08-30. **This one opened the cart instead of the item.**

On a store page with anything in the cart, `button_orderCart` floats at the
bottom. On a 1080×2424 screen it occupied `[21,1319][1059,1445]`, while the
menu row for Chinese Bhel — `container_card_view` — occupied
`[42,1240][1038,1466]`. The row's **centre is (540,1353), which is inside the
cart bar.** `find` returned that centre, the tap dispatched, `input tap`
exited 0, and the cart opened. Nothing in the exit codes said otherwise.

### The Add-to-order footer covers the last option rows
**Verified:** 2026-08-30. **This one would have added the item to the cart.**

On an item sheet, `footer_background` / `addToCart_button` occupy
`[0,2171][1080,2329]`. The option row for "Medium Spicy" was
`[0,2130][1080,2292]`, centre **(540,2211) — inside the Add button**. Tapping
what `look` printed for "Medium Spicy" would have added the dish at the
default spice level rather than changing it. On somebody's food order that is
not a cosmetic bug.

### What `phone` does about it now

`find`, `look` and `tap` pick a point that a tap will actually reach: among
the clickable elements whose bounds contain a candidate point, the one latest
in the tree is treated as the one on top, and a covered point is rejected in
favour of a clear one inside the same target — usually a band above the
overlay, where the label lives. Both cases above now resolve to safe points
(364,1316 and 540,2136), verified against the captured trees.

It is a **warning, not a veto** — `look` prints `?under <thing>` and `tap`
still acts. See the Compose entry below for why vetoing would be wrong.

### Coordinates move between visits to the same screen
**Verified:** 2026-08-30.

The same item sheet, reached by menu tap and then by deep link, put "Mild
Spicy" at y=2043 and then at y=1862: the deep-linked entry has no store header
row. **Never cache a coordinate, not even from four seconds ago.** Resolve the
label every time; that is what `tap --label` is for.

---

## The item sheet, structurally

This is the screen that matters, and it reads cleanly. Ids, top to bottom:

| resource-id | what it is |
|---|---|
| `share` | Share button — the item's id lives behind it |
| `textView_storeItem_description` | the dish description |
| `cart_item_presence_text` | "1 × in your cart", when it already is |
| `checkBox_storeItemReorder_title` / `_description` | "your recommended options" reorder cards — **a saved configuration, not an option** |
| `textView_storeItemHeader_title` | an option group's name ("Choice of Meat") |
| `textView_storeItemHeader_isRequired` | `Required` or `Optional` |
| `header_subtitle` | the constraint: "Select 1", "Select up to 1" |
| `item_option` + `radio_button` + `checkBox_storeItemOption_title` | one option row |
| `button_storeItem_instructions` | Special instructions |
| `addToCart_button` | the footer button |

`phone look` now prints `[x]` / `[ ]` for each option, because the checkable
node is a *sibling* of the label rather than an ancestor and was being dropped:

    540,1862    [ ] Mild Spicy
    540,2030    [x] Medium Spicy
    540,2123    [ ] Spicy

That is the whole state of an option group in three lines, and it is what lets
you verify a modifier landed instead of assuming it.

### The Add button is enabled when the item is not orderable
**Verified:** 2026-08-30.

With a required group unselected, `addToCart_button` still reports
`enabled="true"` and `clickable="true"`. The *only* signal is its label:

    540,2234    Make 1 required selection      <- not ready
    540,2234    Add to order                   <- ready

So **read the footer's text before tapping it, and again after.** A tap on
"Make 1 required selection" dispatches fine and adds nothing, which is
indistinguishable from success at every level except the words on the button.

---

## Finding an item on a menu

### In-store search beats scrolling
**Route:** screen. **Verified:** 2026-08-30.

The store page's `[Search this menu]` (`search`) opens a field that is
**already focused** — `phone type "bhel"` works with no tap first — and
filters the menu live, with its category headers intact. Two round trips
instead of scrolling a menu of a hundred items.

### Your own query becomes a decoy
**Verified:** 2026-08-30.

After searching, the search box holds your query as its `text`. So:

    phone find "Pad Thai" --exact     ->  597,267   the SEARCH BOX

`--exact` did not save it; the box's text is exactly the query. `find --all`
shows the trap immediately:

    597,267     edit_text                  pad thai
    540,790     container_card_view        Pad Thai
    540,1353    container_card_view        Pad Thai

and `--after "Most Ordered"` cuts past the chrome to the menu itself.

### The reorder cards are also decoys
**Verified:** 2026-08-30.

`find "Medium Spicy"` on an item sheet matched a **reorder card** whose
description happened to read "Medium Spicy" — a saved past configuration
sitting above the option group, earlier in the tree, so it won. Tapping it
applies a whole configuration rather than one choice.

    phone find "Medium Spicy" --after "Spice Level"

`--after` is the answer to "which one did you mean": it drops everything
before the group header.

---

## The cart

`doordash://open-carts` lists every open cart (this account had two). One
`[View Cart]` per store, so disambiguate:

    phone tap --label "View Cart" --after "Chutney's"

A cart line reads out completely, which makes it **the verification surface**:

| resource-id | value |
|---|---|
| `cartItemNameTextView` | `Chinese Bhel` |
| `cart_item_options` | `Medium Spicy` |
| `cartItemPriceTextView` | `$14.99` |
| `textView_formatted_text` | `1 ×` |
| `cart_item_delete_option` | `[Delete Item]` |

**`cart_item_options` is where you confirm a modifier actually applied.** The
item sheet can show a radio selected and the cart is what was really recorded.
Check the cart, not the sheet.

Two items that differ only in their options are **two lines**, not a quantity
of two — while the store page's cart bar shows the combined count. Reading
"2 ×" on the bar and "1 ×" on the line is not a contradiction.

Deleting asks: `[Delete Item]` opens a "Remove item?" dialog with
`Remove item` / `Cancel`. Use `--exact`, or "Remove item" also matches the
title.

---

## Modifiers, and the honest no

### Some stores refuse special instructions outright
**Verified:** 2026-08-30, Nakhon Thai Halal.

The worked example was "no egg in a pad thai". This store's Pad Thai has four
option groups — Choice of Meat (required), Spicy Level, and two add-on groups
— and **none of them mentions egg**. The remaining route is Special
instructions, and there:

> The store has chosen not to accept special requests. Contact them directly
> with questions about their menus.

Structurally, `instructions_input` carries `enabled="false"`, and its inner
`edit_text` holds that sentence as its text while *still* reporting
`clickable="true"`.

So the correct answer to "pad thai, no egg" at this store is **that it cannot
be done in the app**, plus the two things that can: order it as-is, or call
the restaurant. Adding the dish and reporting the modification would be a lie
about somebody's food, and egg is a common allergen — this is the one place in
this document where getting it wrong is not just embarrassing.

Whether instructions are accepted is **per store**; read the screen, do not
assume from this entry.

### Typing into a field that is refusing input
**Verified:** 2026-08-30.

Tapping that disabled box and typing succeeds at every layer: `input text`
exits 0, and `phone type` used to report every character delivered. `type` now
checks `dumpsys input_method` first and refuses:

    phone: nothing is accepting text right now — the window itself is served,
    not a text field.

Its limits are in the phone map; the short version is that it catches a
screen with no field focused and misses a field whose window just closed.

---

## What the tiers cost here

Measured 2026-08-30, controller over the forwarded socket, wall clock.

| call | time |
|---|---|
| deep link + verify | **0.4s** |
| `uiautomator dump` (on device) | **3.1s** |
| shipping a 61 KB tree back, checked | **0.54s** |
| `look` on a DoorDash screen | 4.7s |
| `tap --label … --look` (act **and** verify) | ~9.8s |
| `scan` across 3 screens of an item sheet | 10.9s |
| **whole task, id known** — link to a configured item sheet | **7.9s** |
| **whole task, cold** — search, store, menu search, item | **39.9s** |

The dump dominates and it is **on-device work**, so filtering the XML on the
phone to ship less saves almost nothing — the classic optimisation here is the
wrong one. What pays is **fewer dumps**: deep links that skip screens, one
`scan` instead of a scroll-look-decide loop, and `tap --label` instead of
`find` then `tap`.

---

## Checkout, structurally
**Route:** screen. **Verified:** 2026-08-30, mapped end to end **without
placing an order**. Read this before going past the cart; the rest of this
file was written from the cart backwards.

The flow is three screens and one footer button:

| screen | activity | footer id | footer **label** |
|---|---|---|---|
| cart | `OrderCartActivity` | `button_orderCart_continue` | `Continue` |
| delivery details | `OrderActivity` | **`button_place_order`** | **`Next`** |
| review / tip / pay | `OrderActivity` | **`button_place_order`** | **`Place order`** |

### The id lies. Only the label is true.
**This is the most important thing on this page.**

DoorDash uses **one footer component with the id `button_place_order` at every
step**. On the delivery screen it reads `Next` and it advances. On the last
screen it reads `Place order` and it spends money. Measured, in that order, on
the same id.

So:

- An agent that refuses to touch `button_place_order` cannot get through
  checkout at all.
- An agent that treats `button_place_order` as "the thing that orders" stops
  two screens early and reports the wrong reason.
- An agent that taps it because last time it said `Next` **places the order.**

**Read `textView_prism_button_title` inside the footer before every tap, and
decide on that string.** Nothing else on this screen distinguishes the two.

### Every confirm button in the flow is at the same coordinate

`Continue`, `Next`, `Place order` and the card form's `Add card` all resolve to
**540,2224** on this 1080×2424 screen. A coordinate cached from one screen taps
whichever confirm happens to be under it on the next. The general rule in the
phone map — never reuse a coordinate — is not a style note here; it is the
difference between advancing and buying.

### The receipt reads cleanly, which is what makes a mint possible

| resource-id | value seen |
|---|---|
| `line_label` / `line_cost_final` | Subtotal $28.97, Bag Fee $0.10, Delivery Fee $1.99, Service Fee $4.35, Estimated Tax $2.03 |
| `tip_amount` + `tip_tab_layout` | $5.75, chosen from $5.25 / $5.75 / $6.25 / Other |
| **`total_line_item_final_total`** | **$43.19** |
| `payment_label` / `payment_preview` | `Payment` → **`Google Pay`** |
| `always_open_store_text_view` | "19 min left to order from this store" |

`total_line_item_final_total` is the number to mint for. Read it **after** the
tip is settled — the tip is part of the total and changing it changes the
number, which would strand an already-approved card at the wrong amount.

### What is actually on this account
**Verified:** 2026-08-30, read off `PaymentsActivity`.

    Saved Payment Methods
      Google Pay
    Backup payments        Disabled
    DoorDash Credits       $0.00 USD

**No card is saved — and that is not the same as "cannot be charged".** Google
Pay is a saved method and it is what `payment_preview` shows at checkout, so a
stray `Place order` tap attempts Google Pay, not nothing. Google Wallet is
installed (`com.google.android.apps.walletnfcrel`). Whether it holds a funding
instrument is inside Wallet and was **not** opened. Treat `Place order` as
live.

`PaymentsActivity` is **pure Compose: it has no resource-ids at all** beyond
`action_bar_root`/`content`. Everything is a bare text node, so `--label` is
the only handle on this screen, and `find --all` is worth the dump.

### The add-card form
**Verified:** 2026-08-30. Payment → `Credit/Debit Card`.

Four `EditText`s on two rows, and the number field is a **VGS PCI widget**
(`com.verygoodsecurity.vgscollect.view.internal.CardInputField`), not a plain
input — it accepts `input text` normally:

| field | centre | note |
|---|---|---|
| Card Number | 540,646 | VGS `CardInputField` |
| Exp. Date | 208,935 | `MM/YY` |
| CVV | 540,935 | |
| Zip Code | 871,935 | |
| `Add card` | 540,1361 → **540,2224 once the keyboard closes** | |

**`Default for DashPass subscription` is CHECKED by default.** Adding a
one-time card with that left on points a recurring subscription at a
credential that dies in an hour. **Uncheck it before adding a minted card.**
`Default for business orders` is unchecked and should stay that way.

---

## Paying with a minted card
**Verified:** 2026-08-30, **fill proven end to end; acceptance not.**

The `pay` skill mints a one-time card the person approves per purchase. What
was tested here is the half that touches this app.

### `phone-pay fill` works on DoorDash's PCI field

Focus the field, fill by name, read it back:

    phone tap 540 646
    phone-pay fill number        -> {"chars_typed": 16, "dispatched": true}
    phone look                   -> 4000 0099 9000 1984

All four filled and verified by reading the tree back: `4000 0099 9000 1984`,
`12/30`, `123`, `94103`. `chars_typed` matched, and the last4 matched the
`last4` the mint reported. The agent never held the digits at any point.

`fill` reports **dispatched, not filled** — `input text` exits 0 into a field
that ignored it. Read every field back before continuing; that is what proved
the above rather than assumed it.

### A `--test` card cannot be added to DoorDash
**This is the limit of what can be verified without spending money.**

The testmode number minted here, `4000009990001984`, is **not Luhn-valid**
(checksum 57, mod 10 = 7), and DoorDash's client-side validator rejects it
with **"Invalid card number"** under the field. The digits arrived perfectly;
the card is simply not one this form will take.

So `--test` proves the **mechanism** — focus, fill, read back, shred — and
proves nothing about **acceptance**. A real end-to-end DoorDash payment needs
a live mint, which spends real money and was therefore not done. Anyone
completing that path is doing the first real one; say so.

---

## Not tested, on purpose

**No order has ever been placed from this file.** Checkout is now mapped all
the way to the `Place order` button and the card form was filled with a
testmode card, but nothing was submitted: `Place order` was never tapped and
`Add card` was never tapped. Verified afterwards by observation, not by
intent — order history still ends at Aug 24, Saved Payment Methods still reads
`Google Pay` alone, and the cart was left exactly as it was found (1 × Chinese
Bhel, 2 × Single Vada Pav at Chutney's).

Still not done, and each for a reason:

- **A live (non-test) mint.** That is real money; it is the one step that
  cannot be rehearsed, and it needs the owner asking for it in the moment.
- **Tapping `Place order`.** See above — and note it would go to Google Pay,
  not to a minted card, unless the payment method was changed first.
- **Opening Google Wallet** to see whether it holds a funding instrument.
  That is the owner's payment data and reading it is not needed for any task
  here; the safe assumption is that it does.
- **Scheduling, addresses, and the tip beyond reading it.** Changing the tip
  changes the total, which invalidates an approved mint.

Also untouched: group orders, DashPass management, and anything under
`/orders/help` — the refund and "never delivered" flows make claims to a
merchant in the owner's name.
