# Racetag — First Field Test Checklist

Three lists: what to prepare on the day, what to close before then, and what to actually test for. Print this and bring it to the venue.

---

## 1. Prepare on the day

### Hardware bag (check the night before, again at the venue)

- Sirit INfinity 510 reader + power (PoE injector **or** PoE switch + the actual power brick)
- ≥1 UHF antenna + RP-TNC cable, finger-tight
- FritzBox 4040 + its power adapter
- 2× Ethernet cables (reader ↔ FritzBox, plus a spare)
- USB-Ethernet adapter for the Mac if your model lacks built-in (only if you want a wired laptop link as backup)
- ≥10 RAIN/EPC Gen 2 UHF tags — more than you think, plus spares for testing and breakage
- Mac with battery charged **and** a power adapter; plan for the FritzBox having no internet
- Roll of gaffer tape or zip-ties for antenna mounting
- Printed copy of this checklist

### Software state on the laptop (before leaving home)

- Last good `Racetag.app` built. Make sure the version you bring is **after commit `c05399c`** (the explicit-start commit) so the Start button is there.
- `~/.ssh/config` block for the reader's legacy KEX/RSA algos is in place. Verify with:
  ```
  grep -A 4 "User cliuser" ~/.ssh/config
  ```
- Full dry-run at home: launch Racetag with the FritzBox + reader running, see the Start button, register a fake rider with a tag, do one out-and-back. If that works on the kitchen table it'll work at the venue.

### At the venue, in this order

1. Power the FritzBox; let it boot fully (lights stable ~30 s).
2. Power the reader via PoE; let it boot (~30 s) — its DHCP lease from the FritzBox should be the static-reservation IP you noted earlier.
3. Connect Mac to the FritzBox SSID (or LAN).
4. `ping <reader-ip>` from the Mac. If that fails, stop and debug network before touching Racetag.
5. (Optional) SSH in once with `info.time` to confirm the reader's clock will get pushed by Racetag.
6. For a brand-new event, start with a clean DB:
   ```
   rm -f ~/.racetag/data/racetag.db*
   ```
7. Launch Racetag from a terminal so you can see logs:
   ```
   READER_IP=<ip> /Users/jan/Documents/git/racetag/apps/desktop/dist/Racetag.app/Contents/MacOS/Racetag
   ```

---

## 2. Gaps to close before the field test

Roughly worst-first.

- **No results export.** Finished riders sit in SQLite. If someone asks for a printable result sheet, you don't have one. Easiest fix: a `GET /classification.csv` endpoint that streams `bib,name,tag_id,laps,total_time_ms,finished` as CSV. ~30 min of work.
- **No "stop / pause race" concept.** The race can only be Reset (destructive) or run forever. Fine for a single race-day, but for prelims/finals on the same DB you'd want a "freeze standings here" snapshot. Optional for v0.1.
- **Untested rider-registration flow with real reader events.** Unit-tested and works via curl, but the on-antenna register flow (couple tag → rider modal triggered by `unknown_tag` SSE) has never run against a real reader emitting `unknown_tag` frames. Worth a 10-minute pre-event rehearsal: launch app, click "Couple tag → rider", wave a fresh unregistered tag, verify modal opens with the tag id. Debug now, not at the start line.
- **Cooldown tuning unverified.** Default `min_pass_interval_s=10` (reader-side) + `8` (backend). For a bicycle round-course the shortest lap time is much longer than 10 s, so this is safe. For a kid's pump track or a very tight criterium where laps are under 30 s, set cooldown to half of the fastest expected lap. Tune at the venue with the **Settings** modal.
- **Antenna power / reach unverified for your specific course geometry.** Today it's at 19 dBm (`antennas.1.conducted_power 190`). If riders are passing >2 m above/beside the antenna, bump to 25–30 dBm via SSH:
  ```
  antennas.1.conducted_power=250
  setup.operating_mode=active
  ```
  Test at the venue with a tag at the height riders will carry it.
- **No "rider DNF" UI.** A rider who drops out keeps showing in standings. Workaround for v0.1: ignore them.
- **Stress test missing.** Never run with 20+ tags simultaneously. The async SSE refactor should handle it, but unverified. Mitigation: 5 minutes of "wave 5+ tags rapidly past the antenna" pre-event to spot throughput bugs.
- **BUG-002: same physical tag may produce two participant rows.** Observed live (2026-05-13): a tag passed twice resulted in two rows in standings; the second pass did not increment laps on the first row. Suspected cause: the same physical tag is emitting two distinct `tag_id` strings (case, `0x` prefix, whitespace, or length difference) between reads, so the backend's per-tag dedupe treats them as different riders. Reader-service does `.upper()` + strip `0X` in `sirit_client.py:309-311`, so this shouldn't happen — needs investigation. Diagnostic plan documented in the chat log; runs `sqlite3 ~/.racetag/data/racetag.db "SELECT DISTINCT tag_id, length(tag_id) FROM tag_events;"` after a repro to compare the two strings character-by-character. Fix is likely 5 lines once the discrepancy is identified. Until fixed, manually delete duplicate riders via `DELETE /riders/{tag_id}` when they appear.

---

## 3. What to actually test for

### Pre-race functional smoke (15 minutes on-site, before riders arrive)

1. Cold-boot the laptop. Launch Racetag. UI shows **"Race: not started"**.
2. SSH to reader, run `tag.db.get()` — confirm reader is reading at all.
3. Wave 2 different tags one at a time. Each triggers an `unknown_tag` event → register modal pops with the tag id → enter bib + name → save → standings table shows the row at 0 laps with bib/name.
4. Click **Start race**. Status banner flips to **"Race: running since HH:MM:SS"**.
5. Wait 10 s. Wave one of the registered tags. Standings show `laps: 1`. `total_time_ms` is a positive number on the order of 10 000 ms.
6. Immediately wave the same tag again (within 5 s). Standings still show `laps: 1` (cooldown working).
7. Wait 15 s, wave again. `laps: 2`.
8. Open the **Diagnostics** `<details>` panel. Confirm `antenna 1: <count>` is incrementing.
9. Click **Reset**. Standings clear. Status banner returns to "not started". Rider list preserved (`GET /riders` still has both).

If all 9 pass, the system is ready.

### During the race

- Sanity-check standings every ~5 minutes against an eyeball count of who's leading. If the app says rider A is one lap ahead of rider B but you can see them riding together, something's wrong — likely a missed antenna read.
- Watch the Diagnostics panel for sudden antenna-read drops (cable or RF problem).
- If the Mac runs low on battery, plug it in — don't let it sleep. SSE will reconnect on wake but you may lose 10–30 s of standings refresh.

### Failure-mode tests to do at least once in dry-run (not on race day)

- Pull the Ethernet cable from the reader for 5 s, reconnect. Reader-service should reconnect (W-061), events resume.
- Kill `Racetag.app` mid-race, relaunch with same `READER_IP`. Standings should be exactly preserved (SQLite + replay).
- Power-cycle the FritzBox mid-race. Reader and Mac re-associate, Racetag's reader-service reconnects.
- Run for 30+ minutes continuously to make sure nothing leaks or dies on a long session.

### Post-race

Snapshot the SQLite DB to a safe location as your authoritative record until CSV export ships:

```
cp -r ~/.racetag/data ~/Desktop/racetag-<event-name>-$(date +%Y%m%d).bak
```
