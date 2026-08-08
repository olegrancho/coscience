# Inline capacity steppers on the Compute page

**Date:** 2026-08-08
**Status:** approved, ready to plan
**Builds on:** `2026-08-08-worker-concurrency-limits-design.md`

## Problem

Capacity can now be edited, but only through the Edit capacity modal: open it,
change a number, save, close. That is the right shape for adding or removing a
resource. It is too slow for the thing the modal gets used for most — nudging a
limit up or down while the box is busy, which was one of the stated goals of the
capacity work.

## The approach

Each gauge on the Compute page gets `−` / `+` buttons. A click changes the number
on screen immediately; the write is debounced so an adjustment costs one request
and one commit however many times you click.

The modal stays exactly as it is. Steppers adjust the value of a resource that
already exists; adding, removing or renaming one still goes through the modal.
That split keeps the fast path free of the controls it does not need.

No backend change. `PUT /api/capacity` already accepts a whole capacity map, and
`Service.set_capacity` already validates, writes atomically and commits.

## The stepper control (`components/ui.tsx`)

`Gauge` gains an optional prop:

```ts
onAdjust?: (delta: number) => void
```

When it is absent the gauge renders exactly as it does today. When present, `−`
and `+` buttons appear beside the `used / capacity` readout.

The prop is optional because `Gauge` is shared: `views/Overview.tsx` renders it
too, and the Overview is a read-only summary. Opt-in keeps the steppers on the
Compute page without a second component or a copy of the markup.

Details:

- **Step is 1** for every resource. Nothing in the pool is denominated finely
  enough to want more, and a uniform step keeps the control predictable.
- **`−` is disabled at 0.** Zero is a valid, meaningful setting (a full stop for
  that resource), so it is a floor, not a forbidden value. There is no ceiling.
- Buttons are real `<button>` elements with `aria-label`s of the form
  `increase cpu` / `decrease cpu`, so the control is keyboard-reachable and
  addressable in tests without a test id.

## Pending state and the debounced save (`views/Ledger.tsx`)

`Ledger.tsx` owns a pending overlay:

```ts
const [pending, setPending] = useState<Record<string, number>>({});
```

The number displayed for a resource is `pending[k] ?? l.capacity[k]`.

This overlay is what makes the control feel immediate, and it is also what keeps
the 10s `["ledger"]` poll from fighting the user: a refetch mid-adjustment
updates `l.capacity` underneath, but the overlay stays on top, so a half-finished
adjustment never snaps back to the server's value.

**One save, 1s after the last click.** The timer resets on every click; when it
fires, a single `api.setCapacity({ ...l.capacity, ...pending })` goes out. On
success the overlay clears and `["ledger"]` is invalidated, so the gauges
re-render from the server's own answer rather than from what the client believed.

Two failure modes are handled explicitly, because both silently lose work:

- **Navigating away with a save still pending** flushes it on unmount instead of
  dropping it.
- **A rejected save** reverts the overlay to the server's values and shows the
  error inside the card. A stepper must never leave a number on screen that is
  not what the server holds.

## Testing

`views/Ledger.test.tsx`, with fake timers:

- a click updates the displayed number and issues no request;
- after the debounce elapses, exactly one `setCapacity` call lands, carrying the
  full map with the adjustment merged in;
- four rapid clicks still produce one call, not four;
- `−` is disabled when a resource is at 0;
- a rejected save reverts the displayed number and shows an error.

`components/ui.test.tsx`: `Gauge` renders no stepper buttons when `onAdjust` is
omitted, and calls `onAdjust` with `+1` / `-1` when it is supplied.

## Out of scope

Adding, removing or renaming resources (the modal's job); per-resource step
sizes; an undo affordance; a distinct visual treatment for not-yet-saved values;
any confirmation before reaching 0 — the request was explicitly for speed, and a
1s window is short enough that the gauge reading `0` is the signal.
