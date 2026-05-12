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
- A wireless router or LAN switch (recommended — simplest setup), **or** a direct Ethernet cable between the reader and your laptop (Section 3.2 / 3.3 below).
- No internet access is required during a race.

---

## 2. One-time reader setup

The reader ships with a static link-local IP `169.254.1.2`. Before plugging it into a normal network, flip it to DHCP so a router or your laptop can assign it an address.

1. **Mount antennas** at the timing line (typically 2 antennas spread across the track width, facing up toward passing riders).
2. **Connect antennas** to the reader's antenna ports.
3. **Power the reader via PoE.** It boots in ~30 seconds.
4. **Connect the reader's Ethernet directly to your laptop** for this one-time step.
5. **Set your laptop's Ethernet adapter to `169.254.1.100` / netmask `255.255.255.0`** so it can talk to the reader's factory IP.
   - **macOS:** System Settings → Network → Ethernet → Details → **TCP/IP** → Configure IPv4: **Manually** → IP `169.254.1.100`, Subnet `255.255.255.0`. (Router/DNS fields can stay empty.)
   - **Windows:** `ncpa.cpl` → right-click Ethernet → Properties → IPv4 → Properties → Use the following IP address: `169.254.1.100` / `255.255.255.0`.
6. **SSH into the reader and flip it to DHCP:**
   ```
   ssh cliuser@169.254.1.2          # no password
   com.network.1.set(dhcp)
   reader.reboot()
   ```
7. **Revert your laptop's Ethernet** back to "Using DHCP" (or "Obtain an IP address automatically").

The reader will now ask for an IP via DHCP every time it boots. Pick **one** of the network topologies in Section 3.

For deep dives on the reader's CLI and protocol, see `apps/reader-service/docs/Sirit INfinity 510/`.

---

## 3. Network topology — pick one

### 3.1 Wireless router or LAN switch (recommended)

The easiest setup once the reader is in DHCP mode.

1. **Plug the reader's Ethernet into your router's LAN port** (or any switch on the same network your laptop is on — wired or wireless).
2. **Find the IP the router gave it:**
   - **Easiest:** open your router's admin page and look for a device with hostname starting with `00179e…` (Sirit's MAC prefix).
   - **macOS / Linux:** `arp -a | grep -i "0:17:9e"`
   - **Windows:** `arp -a | findstr "00-17-9e"`
   - **mDNS shortcut:** the reader advertises itself as `<serial>.local`. Try `ping 00179eXXXXXX.local` where `XXXXXX` is the last six characters of the serial number on the reader's sticker, or open `http://00179eXXXXXX.local` in a browser to hit the reader's web portal (user `admin`, pass `readeradmin`).
3. **Note the IP** — you'll enter it in Racetag's Settings (Section 4).

### 3.2 Direct Ethernet on macOS (Internet Sharing)

Use this if there's no router available and the reader is plugged straight into your Mac.

1. **Connect the reader to the Mac with an Ethernet cable** (USB-Ethernet adapters work fine).
2. **System Settings → General → Sharing → Internet Sharing → ⓘ.**
3. **Share your connection from:** Wi-Fi.
4. **To devices using:** check the Ethernet adapter (e.g., "USB 10/100/1000 LAN").
5. **Toggle Internet Sharing on** (top-right of the sheet). Confirm "Start" when prompted.
6. macOS sets the Ethernet adapter to `192.168.2.1` and runs a DHCP server on it. The reader will get an address in the `192.168.2.x` range. (Note: the exact subnet depends on the macOS version — older versions used `10.0.2.x`; check the actual Ethernet IP under Network details.)
7. **Find the reader's IP:**
   ```
   arp -a | grep -i "0:17:9e"
   ```
8. **Note the IP** — you'll enter it in Racetag's Settings.

If macOS asks for firewall permission when Racetag opens, allow it (System Settings → Network → Firewall → Options).

### 3.3 Direct Ethernet on Windows (Internet Connection Sharing)

1. Connect the reader to the laptop with an Ethernet cable.
2. Press `Win + R`, type `ncpa.cpl`, press Enter.
3. Right-click the **Wi-Fi** adapter, choose **Properties**.
4. Go to the **Sharing** tab. Check **Allow other network users to connect through this computer's Internet connection**.
5. In the **Home networking connection** dropdown, select the Ethernet adapter connected to the reader.
6. Click **OK**. Windows sets the Ethernet adapter to a static `192.168.137.1` and starts a DHCP server on it. The reader will receive an address in the `192.168.137.x` range.
7. Find the reader's IP:
   ```powershell
   arp -a | findstr "00-17-9e"
   ```
8. Note the IP — you'll enter it in Racetag's Settings.

Windows Defender Firewall may show a prompt the first time Racetag opens outbound TCP connections. Allow it.

(See `apps/reader-service/docs/Sirit INfinity 510/` for screenshots of the web portal.)

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
- Ping the reader: `ping <reader-ip>` should succeed. If it doesn't, the laptop and reader aren't on the same network.
- If using a router (§3.1), check the router admin page or `arp -a` again — the reader may have got a different DHCP lease this boot.
- If using direct Ethernet on macOS (§3.2), confirm Internet Sharing is still on (System Settings → Sharing) and the Ethernet adapter has the `192.168.2.1`-ish address.
- If using direct Ethernet on Windows (§3.3), confirm ICS is configured and the reader has an IP in the `192.168.137.x` range.
- Verify the IP in Settings matches the reader's actual IP.
- Check that no firewall is blocking outbound TCP on ports 50007 and 50008.

**Laps counted twice**
- The `Min lap interval (s)` setting in the Settings modal is the primary double-count gate. Increase it to be safely below the shortest realistic lap time on your course.
- The backend has a secondary cooldown (`RACE_MIN_PASS_INTERVAL_S`). In the packaged build this defaults to `8` and is not exposed in the Settings UI; contact the operator guide author if you need to override it.

**Window didn't open / blank screen**
- On macOS: check that Racetag has permission to accept incoming connections (System Settings → Network → Firewall → Options). If you're testing an unsigned local build, right-click `Racetag.app` → Open the first time to clear Gatekeeper.
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
