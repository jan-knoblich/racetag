# Racetag Operator Guide

Target audience: race marshal with no prior exposure to the software. Follow these steps to set up the system in under 30 minutes.

---

## 1. What you need

**Hardware**
- Sirit INfinity 510 RFID reader
- 1–4 UHF antenna(s) connected to the reader via RP-TNC cables
- PoE switch or PoE injector to power the reader (802.3af)
- Ethernet cable to connect the reader to your laptop or to the LAN switch
- A laptop running macOS or Windows (for the Racetag app)
- UHF passive RFID tags (RAIN/EPC Gen 2, UHF 860–960 MHz) — one per rider

**Network**
- The laptop and the reader must be on the same LAN, or connected directly via Ethernet (see Section 3 for the Windows ICS bridge).
- No internet access is required during a race.

---

## 2. One-time reader setup

1. Mount antennas at the timing line (typically 2 antennas spread across the track width, facing up toward passing riders).
2. Connect antennas to the reader's antenna ports.
3. Power the reader via PoE. The reader boots in approximately 30 seconds.
4. Assign the reader a static IP on your LAN. The default factory IP varies by unit; check the label on the reader or use the Sirit web interface (connect directly via Ethernet and browse to `http://169.254.x.x` — the reader's link-local address). Set the IP to something predictable, e.g., `192.168.1.130`.
5. Note the IP — you will enter it in Racetag's Settings on first launch.

For deep dives on the reader protocol and firmware, see `apps/reader-service/docs/Sirit INfinity 510/`.

---

## 3. ICS bridging on Windows (direct Ethernet connection)

If the reader is connected directly to your laptop's Ethernet port (not through a LAN switch), Windows needs Internet Connection Sharing (ICS) to bridge the Wi-Fi and Ethernet adapters so the reader gets an IP address.

1. Connect the reader to the laptop with an Ethernet cable.
2. Press `Win + R`, type `ncpa.cpl`, press Enter.
3. Right-click the **Wi-Fi** adapter, choose **Properties**.
4. Go to the **Sharing** tab. Check **Allow other network users to connect through this computer's Internet connection**.
5. In the **Home networking connection** dropdown, select the Ethernet adapter connected to the reader.
6. Click **OK**. Windows sets the Ethernet adapter to a static `192.168.137.1` and starts a DHCP server on it. The reader will receive an address in the `192.168.137.x` range.
7. Find the reader's IP:

```powershell
arp -a
# Or search by the reader's MAC prefix, e.g.:
arp -a | findstr "00-17-9e"
```

8. Enter this IP in Racetag's Settings (see Section 4).

Windows Defender Firewall may show a prompt the first time Racetag opens outbound TCP connections. Allow it.

(See `apps/reader-service/docs/Sirit INfinity 510/` for screenshots.)

---

## 4. First launch of Racetag

1. Download `Racetag-<version>-mac.zip` or `Racetag-<version>-win.zip` from the GitHub Releases page.
2. Unzip the archive.
3. Open the app:
   - **macOS:** double-click `Racetag.app`. If macOS shows a security warning ("app from unidentified developer"), right-click → Open.
   - **Windows:** double-click `Racetag.exe`. If Windows SmartScreen prompts, click **More info** → **Run anyway**.
4. A window opens showing the standings UI. On first launch, `~/.racetag/data/` is created automatically.
5. Click the **gear icon** (Settings) in the top-right corner. Set:
   - **Reader IP** — the IP address you noted in Section 2 or 3.
   - **Total laps** — the target lap count for the race (e.g., `5`).
   - **Min lap interval (s)** — minimum seconds between two counted laps for the same tag. Set this lower than the fastest realistic lap time on your course. Default: `10`.
6. Click **Save**. The reader-service will connect to the reader in the background.

**Coupling a tag to a rider (on-antenna registration):**
When an unregistered tag passes the timing line, the UI pops up a **Register rider** modal automatically. The tag ID is pre-filled. Enter the rider's bib number and name, then click **Register**. The mapping is saved to the SQLite database and persists across restarts.

**Bulk import:**
In Settings, use the **Import CSV** button to register many riders at once. The CSV must have three columns: `tag_id`, `bib`, `name`. You can prepare this in Excel or Google Sheets.

---

## 5. Running a race

**Before the start:**
1. Open the **Diagnostics** panel (icon in the top bar) and confirm all antennas show recent read counts. If an antenna shows zero, check the cable connection and the reader web interface.
2. Click **Reset race** (in Settings or via the reset button) and confirm. This clears all lap data but preserves rider registrations.

**During the race:**
- The standings table updates automatically as riders cross the timing line.
- The **Diagnostics** panel shows per-antenna read counts for the last 60 seconds — use it to detect a failing antenna mid-race.
- New unregistered tags trigger the Register rider modal. You can dismiss it and register the rider later via the tag ID in the recent-reads list.

**At the finish:**
- Racetag marks riders `finished` when they reach the configured total laps. The standings table highlights finished riders.
- To export results, open the SQLite database directly at `~/.racetag/data/racetag.db` with any SQLite client (e.g., DB Browser for SQLite). The `events` and `participants` tables contain all race data.

---

## 6. Troubleshooting

**"Reader not reachable" / no tags detected**
- Confirm the reader is powered and the Ethernet cable is plugged in.
- On Windows with a direct connection, confirm ICS is configured (Section 3) and the reader has an IP in the `192.168.137.x` range.
- Verify the IP in Settings matches the reader's actual IP (`arp -a`).
- Check that no firewall is blocking outbound TCP on ports 50007 and 50008.

**Laps counted twice**
- The `Min lap interval (s)` setting in the Settings modal is the primary double-count gate. Increase it to be safely below the shortest realistic lap time on your course.
- The backend has a secondary cooldown (`RACE_MIN_PASS_INTERVAL_S`). In the packaged build this defaults to `8` and is not exposed in the Settings UI; contact the operator guide author if you need to override it.

**Window didn't open / blank screen**
- On macOS: check that Racetag has permission to accept incoming connections (System Preferences → Security & Privacy → Firewall → App exceptions).
- On Windows: allow Racetag through Windows Defender Firewall if prompted.
- Try restarting the app. The backend port is picked dynamically on each launch.

**Lost rider registrations after restart**
- This should not happen — rider data is persisted to SQLite.
- If it does, verify that `~/.racetag/data/` exists and is writable (`ls -la ~/.racetag/data/` on macOS; check folder properties on Windows).
- Confirm the disk is not full.

---

## 7. Data locations

| File | Path | Contents |
| --- | --- | --- |
| SQLite database | `~/.racetag/data/racetag.db` | Rider registrations, lap events, persistent config |
| Reader event spool | Working directory `logs/spool.jsonl` | Batches that failed to reach the backend and are queued for retry (normally empty) |
| Reader debug log | Working directory `logs/reader.log` | Detailed reader-service log when `--debug` / `RACETAG_DEBUG=true` is set |

On Windows, `~` resolves to `C:\Users\<username>`.

In the packaged desktop app the working directory for `logs/` is inside the app bundle; to access spool and debug logs, run the reader-service separately from a terminal with the `--debug` flag.
