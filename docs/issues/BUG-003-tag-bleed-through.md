# BUG-003 — Unexpected tag in standings (RF bleed-through from adjacent space)

**Status:** Open — operator-side mitigations available; optional code mitigation drafted.
**First observed:** 2026-05-13 during live testing on a FritzBox 4040 LAN with a Sirit INfinity 510, one antenna at default power.
**Originally filed as:** BUG-002 (same physical tag, two participant rows) — closed after confirming the two rows were two distinct EPC values. This is the follow-up explaining where the second EPC actually came from.

---

## Symptoms

- One participant row appears in the Racetag standings UI for a tag the operator is **not actively waving**.
- The tag's row stays at `laps: 1` and never progresses (cooldown is rejecting subsequent reads).
- In the reader's web portal / `tag.db.get()`, the unwanted tag has:
  - a low total repeat count compared to the actively-waved tag (e.g. ~200 vs ~28,000), and
  - reads spaced minutes apart rather than a continuous burst.
- The operator's actively-waved tag works correctly and is the high-repeat-count entry — *not* the unwanted one.

## Concrete example from 2026-05-13

```
0x4972332549440335BF07803A1C5E132A    ISOC    1    28433    1999-11-30T00:00:13.144    2026-05-13T20:35:55.065
0x3036143CA426558CC9503EF300000000    ISOC    1    197      1999-11-30T00:00:59.316    2026-05-13T18:30:48.570
```

- `4972…132A` — the operator's intended tag. 28,433 reads. Continuous presence. Normal.
- `3036…0000` — unexpected. 197 reads over a long window (≈1 read/minute). The operator reports this tag is **in another room**, with no intent to be read.

---

## Root cause

UHF passive RFID (EPC Gen 2) reads at 860-960 MHz. With the antenna's default config (`antennas.1.conducted_power=190` = 19 dBm, directional UHF antenna with ~6 dBi gain), effective ERP is ~25 dBm. Drywall attenuates UHF but does **not** fully block it — a tag a wall or two away can still be excited and respond at the edge of the antenna's beam.

The sporadic read pattern (≈1 / minute over hours) is the signature of an edge-of-range read: the tag is briefly oriented favourably or the operator briefly stands clear of the RF path, and one read squeezes through.

The Sirit's `tag.db` then keeps the entry; each sporadic read increments the repeat counter and updates `last`.

Each fresh read produces an `event.tag.arrive` (followed by `event.tag.depart` 1 s later per `tag.reporting.depart_time=1000`), which Racetag's reader-service forwards to the backend. The backend creates a participant row keyed on `tag_id`. The row sticks around — the W-002 cooldown stops it from accumulating laps, but the row itself isn't removed.

This is **not** the "always-present stray tag near the reader" failure mode that was originally hypothesised as BUG-002. That mode has a high repeat count and a continuously-updating `last`. The bleed-through mode has a low repeat count and a slowly-updating `last`.

---

## Diagnostic steps

### 1. Confirm bleed-through vs stale `tag.db` entry

In the SSH session to the reader:

```
tag.db.clear()
```

Walk away with the intended test tag. Wait 30 seconds. Then:

```
tag.db.get()
```

- **Empty** → the unwanted tag was a stale entry. Not currently bleeding through. Less urgent — but it can resurrect if conditions favour it.
- **Unwanted tag reappears alone** → bleed-through confirmed. Apply one of the mitigations.

### 2. Estimate current effective range

With Tag A in hand, walk slowly away from the antenna until it stops registering in `tag.db.get()`. Record the distance. That's the effective range right now. The unwanted tag is somewhere inside that radius (often farther than expected due to wall reflections and beam side-lobes).

### 3. Decide which mitigation(s) to apply

See the next section.

---

## Mitigation options (pick one or combine)

### Option A — Lower TX power on the antenna (recommended)

Targeted, immediate, no code change. Reduces the read radius so cross-room reads stop while still cleanly reading riders crossing the timing line at <1 m.

In the SSH session:

```
antennas.1.conducted_power=140
setup.operating_mode=active
```

`140` = 14 dBm. Try `160` (16 dBm) or `180` (18 dBm) if you need a bit more range. For a typical timing-line geometry (antenna 2 m above the course pointing down at riders 1-2 m below it), 14-18 dBm is plenty.

After lowering, redo step 2 of Diagnostic — confirm the unwanted tag stops registering and the intended tag still registers with the rider at the expected position.

**Caveat:** this setting is volatile; the reader reverts to its persisted value on reboot. To make it stick, save with the reader's profile-save command (check `reader.profile.*` in `tag.db.get()` — Sirit syntax is firmware-dependent, but typically `reader.profile.save()` or similar).

### Option B — Physical isolation of the bleed-through source

Move the unwanted tag at least one more room away (or one floor up/down), or wrap it in a Faraday shield. Practical Faraday materials:

- Aluminium foil, three layers, fully enclosing the tag — kills UHF response cold.
- A metal cookie/biscuit tin with the lid closed.
- A static-shielded ESD bag (the silvery anti-static packaging chips ship in).

Useful for race day if you can't move tags from the venue and want a zero-config option.

### Option C — Software filter: only register taggees who are registered riders

Code change. Currently `RaceState.add_lap` (and the storage append step in `app.py`'s batch handler) creates a Participant row for every incoming tag_id, registered or not. Instead, change the policy to:

- If the tag is registered (present in `RiderStore`), proceed as today.
- If not registered, still fire the `unknown_tag` SSE event (so the register-tag modal can pop), and still append to the diagnostics event log, but **don't create a Participant row**. The row materialises only when the operator couples the tag to a rider via the modal.

Sketch (in `apps/backend/racetag-backend/app.py`, batch-ingest handler):

```python
for ev in body.events:
    storage.append_event(ev)              # always persist
    if ev.tag_id in rider_store:
        p = race.add_lap(ev.tag_id, ev.timestamp)
        # … existing lap/standings broadcast …
    else:
        # Already firing unknown_tag SSE here today — keep that.
        # Just skip race.add_lap so no phantom row appears.
        pass
```

Pros: handles bleed-through, stray-near-reader, and future "library book passes through the venue" failure modes uniformly. Operator never sees phantom rows.
Cons: changes the UX flow slightly — a tag must be coupled to a rider via the modal *before* it can show up in standings. Today the row appears first and is then enriched with bib/name on coupling.

Tests would be in `apps/backend/tests/test_unknown_tag.py` (add: unregistered tags do not appear in `/classification`).

This is a 10-20 line change including tests. Open for a call.

### Option D — Antenna beam shaping (only if A+B+C aren't enough)

Add a metal-mesh "horn" around the antenna to physically narrow the beam pattern. Not needed for the typical setup — listed for completeness.

---

## Recommendation for the field test

1. Before the event, in the venue, run **Option A** (lower TX power) to whatever value stops cross-area reads in a walk-around test.
2. Pack a few squares of aluminium foil in the bag for **Option B** as a last-resort.
3. Land **Option C** in code as a defensive layer in the next sprint (low priority for the first event if A+B work, higher priority before any event with audience-side tags within ~10 m).

## Decision log

- 2026-05-13: hypothesised "always-present stray tag near reader" (BUG-002). User confirmed only one tag is physically near the reader → invalidated.
- 2026-05-13: re-examined the read-count pattern (high vs low) and timestamp deltas → identified bleed-through from adjacent room as the actual cause.
- Open: pending a `tag.db.clear()` / walk-30-s / `tag.db.get()` repro to confirm the diagnosis on this specific deployment.
