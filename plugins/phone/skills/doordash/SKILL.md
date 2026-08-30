---
name: doordash
description: Order food on DoorDash from the phone — find a restaurant, find a dish, set its options, put it in the cart. Use when a task names DoorDash, a restaurant, a dish, or a food order on the phone.
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

## The boundary

- **Never tap Place Order, Continue to checkout, or anything past the cart.**
  Build the cart, report exactly what is in it and what it costs, and let the
  person press the button. If they ask you to order outright, confirm the
  full contents and total in the same turn first.
- Never touch payment methods, addresses, tips, DashPass, or the
  `/orders/help` refund flows — those speak to a merchant in their name.
- Leave the cart as you found it if you were only exploring, and say what you
  changed.
- Take the lease before acting: `phone lease acquire --as <you>`.

## If you remember three things

Deep-link as far in as you can. Disambiguate with `--after` and check with
`find --all`, because this app is full of text that matches what you meant.
Verify in the cart, never on the sheet — and stop at the cart.

Dated specifics — ids, resource-ids, which store refuses what — live in
`map.md` beside this file. This file is the method; that one is the territory.
