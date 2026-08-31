---
name: doordash
description: Order food on DoorDash from the phone — find a restaurant, find a dish, set its options, build the cart, and go through checkout with a card the person approves. Use when a task names DoorDash, a restaurant, a dish, or a food order on the phone.
---

# Driving DoorDash

You are spending a real person's money, or one step away from it. Adding to a
cart is reversible; placing an order is not. **Never place an order** — see
the boundary at the bottom.

Read the `phone` skill first if you have not: the route ladder, the lease, and
"verify by reading state back" all apply here unchanged. This file is the
method for one app; **`map.md` next to this file** is what that app actually
does, established by trying it.

## The ladder, for this app

DoorDash has no readable state — no provider, no dumpsys. So route 1 is empty
and the whole game is **how far the intent tier carries you before the screen
starts**.

| | how | cost |
|---|---|---|
| **deep link** | `doordash://…` — lands you *inside* a screen | 0.4s |
| **in-store search** | the store's own `[Search this menu]` | 2 steps |
| **screen** | `look` / `scan` / `tap --label` | ~3.1s per dump |

Every screen you skip is a dump you do not pay for and a tap that cannot go
wrong. Start as deep as you can.

## Recipes

**A store you have an id for** — the fast path, ~5s:

```
phone sh 'am start -a android.intent.action.VIEW \
          -d "doordash://store/1332398" -p com.dd.doordash'
phone look
```

**A dish you have an id for** — straight to its option sheet, ~5s:

```
phone sh 'am start -a android.intent.action.VIEW \
          -d "doordash://store/1332398/item/24154209014" -p com.dd.doordash'
phone look          # confirm the name is the dish you were asked for
```

Ids live in the index in `map.md`. **Always confirm the name on the
sheet** — a menu can be re-uploaded and an id can go stale, and that one line
of `look` is the difference between a shortcut and a wrong order.

Measured end to end, both landing on the same configured item sheet: **7.9s
with the id, 39.9s without.** That is what an id is worth, and why harvesting
one is worth fifteen seconds the first time you visit a store.

**A store you do not have an id for** — search, then **`scan`, not `tap`**:

```
phone sh 'am start -a android.intent.action.VIEW \
          -d "https://www.doordash.com/search/store/chutneys/" -p com.dd.doordash'
phone scan "Chutney's" --max 5 --tap
```

Search results are **ranked, and the store you named is often not the first
one** — searching "chutneys" put Madras Dosa Company on top and Chutney's
below the fold. A `tap --label` there acts on what happens to be visible and
reports nothing wrong; `scan` scrolls until the name is actually on screen,
and says so plainly when it never is.

**A dish on a menu** — use the store's own search, never scroll the menu:

```
phone tap --label "Search this menu"
phone type "bhel"                          # the field is already focused
phone scan "Chinese Bhel" --max 4 --tap --look
```

`--after` matters: after you search, **the search box holds your query as its
own text and will match it**. So will a past-order card. `find --all` shows
you every candidate before you commit to one.

**Set an option and add it:**

```
phone look                                        # [x]/[ ] show what is set
phone tap --label "Medium Spicy" --after "Spice Level" --look
phone tap --label "Add to order" --look
```

**Read the cart, which is the only proof:**

```
phone sh 'am start -a android.intent.action.VIEW \
          -d "doordash://open-carts" -p com.dd.doordash'
phone tap --label "View Cart" --after "Chutney's" --look
```

## Four things this app will do to you

**The floating cart bar and the Add-to-order footer sit on top of the things
you want to tap.** A menu row's centre can be inside the cart bar; an option
row's centre can be inside the Add button. `phone` picks a clear point for you
and prints `?under <thing>` when it cannot. If you see that marker, scroll the
target into the middle of the screen and look again rather than tapping.

**The Add button is `enabled` even when the item cannot be added.** Its label
is the only signal: `Make 1 required selection` means a required group is
unset. Read the footer's words before you tap it and after.

**A selected radio on the sheet is not a modifier in the cart.** Verify in the
cart line — `cart_item_options` prints exactly what was recorded.

**Coordinates move.** The same sheet reached two ways put the same option 180px
apart. Never reuse a coordinate; resolve the label each time.

## Going faster

The dump is 3.1s and it happens on the phone, so shrinking what you ship back
saves nothing. Pay for fewer dumps instead:

- `phone tap --label X --look` is one turn where `find` → read → `tap` → `look`
  is three.
- `phone scan "<label>"` scrolls **on the device** until the label appears and
  returns a tap point. Three screens of an item sheet in 10.9s and one turn; it
  also stops when the list stops moving, so a miss is a real miss.
- Deep-link past everything you can.

## When it cannot be done

Some dishes have no option for what was asked, and **some stores refuse
special instructions entirely** — the field renders disabled with the store's
refusal in it. The one worked example is "pad thai, no egg" at Nakhon Thai
Halal: no egg option, instructions disabled.

Say so. Offer what is real — order it as-is, or call the restaurant. Do not
add the dish and describe it as modified: allergies are why someone asks, and
a confident wrong answer here reaches their plate. Typing into the disabled
field looks like it works at every level; `phone type` refuses on your behalf,
but the judgement is yours.

## Going through checkout

Checkout is mapped (`map.md`, "Checkout, structurally"). Read that before your
first one. The short version, and the two things that will hurt you:

```
phone tap --id button_orderCart_continue        cart  -> delivery
phone look --grep "Next"                        READ THE LABEL
phone tap --id button_place_order               delivery -> review
phone look                                      tip, total, payment method
```

**`button_place_order` is the id of the footer button on *every* checkout
screen.** It reads `Next` on the delivery screen and advances; it reads
`Place order` on the last screen and spends money. The id cannot tell you
which one you are looking at. `textView_prism_button_title` can. Read it
before every tap and decide on that string.

**Every confirm button in the flow sits at 540,2224** — `Continue`, `Next`,
`Place order`, `Add card`. A coordinate carried over from the previous screen
taps whatever confirm is under it now. Resolve the label every time.

## Paying

Ordering goes through the `pay` skill's one-time card. That is the only
sanctioned route: **read the real total, mint for exactly it, let the person
approve, fill, then order.**

```
phone look --grep Total                    total_line_item_final_total
phone-pay mint --amount <cents> --merchant "DoorDash — <store>" \
                --context "<what they are buying and why, 100+ chars>"
# hand the approval_url to the person, on the surface they are actually on
phone-pay await                            blocks until they decide
```

Then set the payment method to that card — Payment → `Credit/Debit Card` —
and fill it. `PaymentsActivity` has **no resource-ids**; match on labels.

```
phone tap 540 646 ; phone-pay fill number   then phone look to READ IT BACK
phone tap 208 935 ; phone-pay fill exp
phone tap 540 935 ; phone-pay fill cvc
phone tap 871 935 ; phone-pay fill zip
```

**Uncheck `Default for DashPass subscription` before adding.** It is checked
by default, and leaving it on points a recurring subscription at a credential
that expires in an hour.

`fill` reports *dispatched*, not *filled*. Read every field back with `look`
and check the last4 against `phone-pay status` before you go on. Shred with
`phone-pay done` when you are finished, success or not.

Settle the **tip first**. It is part of the total, so changing it after a mint
strands an approved card at the wrong amount.

## The boundary

- **The stopping point is the approval, not the cart** — but only for a card
  the person approved for *this* total, in this session. Build the cart, reach
  checkout, read the real total, mint for it, and stop dead until they decide.
- **A safe card is not permission to tap a confirm button.** The approval
  covers the money; it does nothing about tapping the wrong thing. This app
  has stolen two taps already (see the occlusion entries in `map.md`), and its
  confirm buttons all share one coordinate and one resource-id. `find --all`
  before any tap on a checkout screen, never `--raw`, and treat a `?under`
  marker as a full stop.
- **The default payment method here is Google Pay, not your minted card.**
  Tapping `Place order` without switching the method first does not fail
  safely — it attempts Google Pay. "There is no saved card" is true and is not
  the same as "this cannot be charged".
- Never touch the person's **saved** payment methods, stored addresses,
  DashPass, or the `/orders/help` refund flows — those speak to a merchant in
  their name. Adding a one-time card for one order is not that.
- Leave the cart as you found it if you were only exploring, and say what you
  changed.
- Take the lease before acting: `phone lease acquire --as <you>`.

## If you remember three things

Deep-link as far in as you can. Disambiguate with `--after` and check with
`find --all`, because this app is full of text that matches what you meant —
and at checkout, one id and one coordinate mean four different buttons. Verify
in the cart and in the field you just typed into, never in the exit code.

Dated specifics — ids, resource-ids, which store refuses what, and the whole
checkout map — live in `map.md` beside this file. This file is the method;
that one is the territory.
