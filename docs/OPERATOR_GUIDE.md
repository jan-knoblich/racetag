# Racetag Operator Guide (technical)

_Audience: the person who installs Racetag on the race PC, sets up the reader and supports the race marshal. English, technical._

| If you are … | Read |
| --- | --- |
| the race marshal operating the timing (German, no technical background) | [BEDIENUNGSANLEITUNG-WINDOWS.md](BEDIENUNGSANLEITUNG-WINDOWS.md) and the one-page [SPICKZETTEL-WINDOWS.md](SPICKZETTEL-WINDOWS.md) |
| setting up or supporting a race PC | this guide |
| working on the reader itself (SSH, CLI, one-time DHCP setup, discovery and heartbeat internals) | [READER_ADVANCED.md](READER_ADVANCED.md) |
| rehearsing failure recovery on the real hardware | [DRESS_REHEARSAL_COMMANDS.md](DRESS_REHEARSAL_COMMANDS.md) |

The operator-facing UI is German. Button and status texts below are quoted exactly as they appear in the app.

> **Verification status (2026-09-13).** The Windows installer, the reader discovery and the automatic recovery described here are implemented and covered by automated tests on macOS against a simulated reader. They have **not yet been run on a real Windows PC or against the real Sirit reader**. See [PLAN-WINDOWS-NONTECHIE.md §13](PLAN-WINDOWS-NONTECHIE.md#13-implementation-status-2026-09-13) for exactly what still needs verification.

---

## 1. What you need

**Hardware**
- Sirit INfinity 510 RFID reader with its power supply
- 1–4 UHF antennas with RP-TNC cables (the kit uses ports 1 and 2)
- FritzBox 7330 (or any router with DHCP) and Ethernet cables
- UHF passive RFID tags (RAIN/EPC Gen 2), one per rider plus spares

**Race PC**
- Windows 10 22H2 or Windows 11, x64. Admin rights are **not** required.
- Microsoft Edge WebView2 runtime (preinstalled on Windows 11 and updated Windows 10; the installer adds it if missing).
- At least 200 MB free disk space on the drive holding `%USERPROFILE%` (checked at start).
- macOS 12+ also works (`Racetag.app`), but Windows is the supported race-day platform.

No Internet access is needed during a race. Internet is needed once if the installer has to download WebView2.

---

## 2. Install

### 2.1 Windows installer (recommended)

Artefact: `Racetag-Setup-<version>.exe` from the GitHub release (test builds from a manual workflow run are named `Racetag-Setup-<version>+<sha>.exe`).

- Installs **per user** to `%LOCALAPPDATA%\Programs\Racetag`, no UAC prompt. Adds a Start menu entry and a desktop icon (task ticked by default). The task "Racetag beim Anmelden an Windows automatisch starten" (`HKCU\Software\Microsoft\Windows\CurrentVersion\Run`) is unticked by default.
- **SmartScreen.** The installer is not code-signed. A downloaded copy triggers "Der Computer wurde durch Windows geschützt": click "Weitere Informationen", then "Trotzdem ausführen". Copying the installer from a USB stick avoids the prompt, because the file carries no mark-of-the-web. The installed app starts without the warning.
- **WebView2.** If the registry value `pv` of `{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}` is missing (HKCU or HKLM WOW6432Node), the installer runs the bundled Evergreen bootstrapper silently. Without Internet that fails; setup still completes and shows the download link. Racetag checks again at start and shows the dialog "Racetag – WebView2 fehlt" with a link to `https://go.microsoft.com/fwlink/p/?LinkId=2124703`.
- **Upgrade:** run the new installer. A running Racetag is closed first (not restarted automatically), the old `_internal` folder is replaced, the finish page offers "Racetag starten".
- **Uninstall:** Settings → Apps → Racetag. The data directory `%USERPROFILE%\.racetag` is kept; the uninstaller says so.

Building the installer locally and a manual installer test checklist: [apps/desktop/installer/README.md](../apps/desktop/installer/README.md).

### 2.2 Windows portable zip

`Racetag-<version>-win.zip` contains the PyInstaller one-directory build. Extract it anywhere and start `Racetag\Racetag.exe`. There is no Start menu entry and no WebView2 install; the SmartScreen prompt appears on first start of a downloaded copy. Data and logs go to the same `%USERPROFILE%\.racetag` as the installed app, so do not run both at once (the single-instance lock prevents it anyway).

### 2.3 macOS

`Racetag-<version>-mac.zip` → unzip → `Racetag.app`. The app is unsigned: right-click → Open the first time. macOS may ask whether Racetag may find devices on the local network; allow it, otherwise the reader cannot be reached.

---

## 3. Network setup

1. **Reader in DHCP mode.** A reader fresh from the factory (or after a hard power loss) sits on the static IP `169.254.1.2`. Switch it to DHCP once: [READER_ADVANCED.md §2](READER_ADVANCED.md#2-one-time-reader-network-setup-factory-ip--dhcp).
2. **Reader into FritzBox LAN 2.** Not LAN 1: the Gigabit port does not negotiate a link with the Sirit.
3. **PC on the same FritzBox network** (its Wi-Fi or a cable into another LAN port). Discovery only sweeps the PC's own subnets.
4. **No IP needs to be entered.** Racetag finds the reader (§4.2). A fixed DHCP lease in the FritzBox is still recommended because it makes reconnects faster, but a changed address is picked up automatically.

Direct-cable setups without a router (Windows ICS, macOS Internet Sharing) are described in [READER_ADVANCED.md §3](READER_ADVANCED.md#3-alternative-topologies-no-router).

### 3.1 Firewall

- The backend listens on **127.0.0.1 only**, on a random free port chosen at each launch. Nothing listens on a LAN interface.
- The reader connections are **outbound** TCP to ports 50007 (CONTROL) and 50008 (EVENT); discovery makes short outbound TCP connections to port 50007 across the local /24.
- Windows Defender Firewall therefore shows **no prompt**, and no firewall rule is needed.
- Third-party security suites may block outbound connections or flag the subnet sweep as a port scan. If the reader is reachable (`Test-NetConnection <ip> -Port 50007`) but Racetag never connects, allow `Racetag.exe` in that product.

---

## 4. First launch: what happens

Double-click **Racetag**. The shell (`Racetag.exe`) then:

1. Creates `%USERPROFILE%\.racetag\data` and `…\logs` and starts logging (`shell.log`).
2. Takes the single-instance lock `~/.racetag/racetag.lock`. A second launch brings the existing window to the front and shows "Racetag läuft bereits".
3. Checks that the data directory is writable and has ≥ 200 MB free ("Racetag – Datenordner" dialog otherwise) and, on Windows, that WebView2 is installed ("Racetag – WebView2 fehlt").
4. Starts the backend on `127.0.0.1:<random port>` in a thread ("Racetag – Startfehler" if it is not ready within 10 s).
5. Starts the reader-service as a supervised child process. Task Manager shows a **second `Racetag.exe`** (`--reader-service`). It is restarted automatically if it crashes and killed with the shell (Windows Job Object).
6. Opens the window. A watchdog checks the backend thread every 5 s and offers a restart ("Racetag – Interner Fehler") if it died.

Any unhandled exception writes `crash.log` and shows "Racetag – Unerwarteter Fehler" with the log path.

### 4.1 First-run assistant

In the desktop build the **Einrichtungs-Assistent** opens when no real race exists yet (none, or only the untouched bootstrap race shown as "Standard-Rennen") and it has not been finished or skipped before. Steps: "Reader finden" (live status and "Reader suchen"), "Antennentest" (one tile per detected port turns green on reads), "Erstes Rennen anlegen". It can be skipped and reopened from Settings → "Hilfe & Support" → "Einrichtungs-Assistent". Step 3 offers to keep a not-started active race that already has riders ("Dieses Rennen weiterverwenden", default; `PATCH /races/{id}` with name and laps) instead of creating a new one, asks before activating a new race while another one is running, and ends with a toast plus a pulsing "Rennen starten" button: laps only count after the start. If the untouched bootstrap race already has riders, the assistant does not open at all.

Finishing or skipping it stores `assistant_done` in the database (`PATCH /config`, meta key `assistant_done`), so it does not reappear on the next start. The flag survives app restarts and updates; it is reset only with a fresh data directory (new `racetag.db`).

### 4.2 How the reader address is chosen

1. The IP persisted in the database (Settings "Reader-Adresse (IP)", or saved automatically after discovery).
2. Else the `READER_IP` environment variable (developer/Docker fallback).
3. Else no IP: the reader-service starts in `searching` and runs discovery (ARP table with the Sirit OUI, TCP sweep of each local /24 on port 50007, the factory IP `169.254.1.2`; each hit verified with `info.serial_number`).

If a known IP stops answering, the reader-service retries with back-off (1, 2, 5, 10 s) and runs discovery after 6 consecutive failures (about a minute), at most every 60 s. If exactly one reader is found at a different address, it switches, the backend persists the new IP and the UI shows the toast "Reader unter neuer Adresse gefunden: …". Two or more readers → "mehrere gefunden"; pick one in Settings → "Reader suchen" → "Übernehmen". Details: [READER_ADVANCED.md §5](READER_ADVANCED.md#5-discovery-internals).

### 4.3 Settings take effect without a restart

`PATCH /config` only persists. The reader-service receives the new reader IP and antenna power in the reply to its next heartbeat (≤ 2 s) and reconnects by itself; lap interval and total laps apply immediately in the backend. **A reader IP or power change, and the button "Reader neu verbinden", interrupt reading for a few seconds** (reconnect, re-bind, clock push, antenna config). Avoid them while riders cross the line.

---

## 5. Status bar

Three pills under the header; hovering shows details, clicking acts. Full German explanation with every text: [BEDIENUNGSANLEITUNG-WINDOWS.md §5](BEDIENUNGSANLEITUNG-WINDOWS.md#5-das-racetag-fenster-und-die-statusleiste).

| Pill | Source | Green | Amber | Red | Click |
| --- | --- | --- | --- | --- | --- |
| **Reader** | `GET /reader/status` + SSE `reader_status` | `active`: "verbunden (IP)" | `searching` "wird gesucht…", `connecting` "verbinde…"/"verbinde neu in N s", `configuring` "wird eingerichtet…", `lost` "Verbindung verloren, verbinde neu…", "mehrere gefunden" | `unknown` "keine Rückmeldung" (no heartbeat for 6 s), `stopped` "gestoppt". Desktop build: `stopped` while `supervisor.restart_pending` or `supervisor.running` is true shows amber "wird neu gestartet…"; `unknown` before the first heartbeat while the child runs shows amber "startet…" for up to 30 s after page load | opens Settings |
| **Antennen** | `antennas` + `antenna_reads` deltas from the status, passes from `/diagnostics/antennas` (polled every 10 s) | "1, 2 OK" (ports powered, not proof a cable is attached) | "1 OK, 2 liest nichts" (race running, 0 reads on a powered port in 60 s while the other ports have ≥ 5), "keine erkannt"; "2 nicht erkannt" only once the status carries `antennas_detected` (not sent yet) | – | opens "Antennen-Diagnose" |
| **Verbindung** | SSE stream of the window | "live" | "verbinde…", "verbinde neu in N s" | "getrennt – neuer Versuch in N s" (after 3 failed attempts) | reconnects now |

While "Verbindung" is not live, Reader and Antennen keep their last text but turn grey. The Reader tooltip shows IP and its source (Startparameter / Einstellungen / automatischer Suche), serial, antennas, last read age, connected-since, failure count, the translated last error and the reader-service restart count.

Toasts: "Verbindung zum Reader verloren – verbinde neu…", "Reader wieder verbunden (Unterbrechung …)", "Reader unter neuer Adresse gefunden: …", "Reader-Dienst antwortet nicht".

---

## 6. Running a race

Click paths in German are in the [Bedienungsanleitung §6–9](BEDIENUNGSANLEITUNG-WINDOWS.md#6-rennen-anlegen-starten-und-beenden). The essentials:

- **Races.** One race per start group. "+" next to "Rennen:" creates one (name, "Rennformat" "Feste Rundenzahl" or "Zeit + Schlussrunden", laps, "Einzelwertung", "Auto-Snapshot-Intervall" default 120 s). The race selected in "Rennen:" is the active race; all passes count for it.
- **Riders are per race.** Select the race first, then couple:
  - "CSV importieren": columns `tag_id`, `bib`, `name`, optional `verein`, `uci_id`; delimiter (semicolon, comma, tab) is detected; Excel CSV works.
  - "Tag → Fahrer koppeln": hold one tag at an antenna, fill in the "Fahrer koppeln" dialog. The dialog only opens after this button was pressed.
  - "Koppel-Modus": scan, type bib, Enter; "Auto-Zuweisung starten" assigns numbers from a range like `1-75` automatically.
  - "Tags exportieren": CSV template of every tag read in the active race, for filling in names in Excel.
  - "Fahrer": edit bib/name/club/UCI-ID without a reader.
- **Timing.** "Rennen starten" (explicit start; passes before it do not count as laps) → banner "Läuft seit …". "Rennen beenden" asks for confirmation and freezes the standings ("Beendet um …"); "Rennen wieder öffnen" undoes it. "Rennen zurücksetzen" deletes all passes of the active race (riders stay); not undoable.
- **Corrections.** Per row in "Runden ±": "+1", "−1", "✎" (lap with explicit UTC timestamp, DNF/DNS/DSQ). "⚠" marks a suspected missed lap.
- **Results.** "Ergebnisse exportieren" writes the active race's classification as CSV (UTF-8 with BOM for German Excel) through a native save dialog. Export right after ending a race.
- **Snapshots.** For the active race, every `snapshot_interval_s` (default 120 s) a CSV and a SQLite backup go to `data/snapshots/<race_id>/`; the last 30 per race are kept.
- **Double counts.** Settings → "Mindest-Rundenabstand (s)" (default 8 s) is the backend lap cooldown; keep it well below the fastest possible lap. The reader-service's own cooldown (`MIN_LAP_INTERVAL_S`) is 0 in the desktop build.
- **Antenna power.** Settings → "Antennenleistung" (100–300, default 300). Lower it if tags are read far from the line; see [READER_ADVANCED.md §1.4](READER_ADVANCED.md#14-antenna-transmit-power).

---

## 7. Automatic recovery

Implemented per the failure-mode matrix of the plan. "Verified" means automated tests plus end-to-end smoke tests against a fake reader on macOS; none of it has been exercised on Windows or the real reader yet.

| Failure | What Racetag does | Operator sees |
| --- | --- | --- |
| Reader not booted yet at app start | Connect retries with back-off, then discovery | amber "verbinde…"/"wird gesucht…" → green |
| Reader IP changed (DHCP, new router) | Discovery after 6 failures, switch, backend persists new IP | toast "Reader unter neuer Adresse gefunden: …" |
| Reader fell back to factory IP `169.254.1.2` | Discovery probes it | same toast, **if** Windows can route to 169.254/16 (unverified) |
| Short cable blip (up to ~35 s, sockets stay open) | TCP keepalive (15 s idle, 5 s × 8) and 3 unanswered liveness probes in a row tolerate it; the reader's queued passes arrive after the link returns | nothing (a warning in `reader.log`) |
| Cable pulled longer / reader rebooted mid-race | EOF, RST, keepalive expiry or 3 missed liveness probes (35–50 s of silence) → reconnect with 1 s / 2 s back-off, re-bind, re-config, clock push | amber "Verbindung verloren, verbinde neu…", then green and "Reader wieder verbunden (Unterbrechung …)" |
| Laptop sleeps (lid closed) | Racetag keeps Windows awake (`SetThreadExecutionState`) but cannot stop lid-close sleep; set "Beim Zuklappen: Nichts unternehmen" | reconnects after wake; passes while asleep are lost |
| Window closed with X / Alt+F4 | Confirmation "Racetag wirklich beenden?" | reading stops only after OK |
| Reader-service process crashes | Supervisor restarts it (1, 2, 5, 10, 30 s) | amber for a few seconds; restart count in the Reader tooltip |
| Backend unreachable from the reader-service | Retry, then spool to `logs/spool.jsonl`, drain on recovery | nothing |
| Window's SSE stream drops | Reconnect with back-off | Verbindung amber, red after 3 attempts |
| App killed hard (Task Manager) | Job Object kills the child; lock released by the OS | nothing; next launch is clean |
| Second launch while running | Lock; existing window to front | "Racetag läuft bereits" |
| Backend thread dies | Watchdog | "Racetag – Interner Fehler", "Ja" relaunches |
| WebView2 missing | Installer installs it; app checks at start | "Racetag – WebView2 fehlt" |
| Unhandled exception in the shell | `crash.log` | "Racetag – Unerwarteter Fehler" |
| Reader clock drift | Clock re-push every 30 min | nothing |
| Disk nearly full / data dir not writable | Startup check | "Racetag – Datenordner" |

Not recoverable: passes during a reader outage (add them with "+1"/"✎"), a loose antenna cable (zero reads on that port in the Antennen tooltip; the assistant's antenna test catches it before the start), the reader in FritzBox LAN 1 (discovery keeps searching).

---

## 8. Troubleshooting

**Reader stays "wird gesucht…" / "verbinde…"**
1. Reader powered and booted (30–60 s)? Ethernet in FritzBox **LAN 2**?
2. PC on the same subnet? `ipconfig` should show a `192.168.178.x` address on the adapter connected to the FritzBox.
3. Reader visible? FritzBox device list, or `arp -a | findstr "00-17-9e"`.
4. Port reachable? `Test-NetConnection <reader-ip> -Port 50007`. If yes but Racetag does not connect, check for a third-party firewall (§3.1) and read `reader.log`.
5. Settings → "Reader suchen" shows what discovery finds, with source ("aus ARP-Tabelle", "Netzwerk-Suche", "Werks-Adresse", "aktuell verbunden").
6. As a last resort type the IP into "Reader-Adresse (IP)" and save. The reader-service switches within one heartbeat.
7. After a hard power loss the reader may be back on `169.254.1.2`; see [READER_ADVANCED.md §2](READER_ADVANCED.md#2-one-time-reader-network-setup-factory-ip--dhcp).

**Reader "keine Rückmeldung" (state `unknown`)**
The backend got no heartbeat for 6 s: the reader-service child is not running or cannot reach the backend. Click "Reader neu verbinden" (restarts the child via the supervisor). Check `reader-stderr.log` (import errors in a broken build) and `shell.log` (supervisor restarts, exit codes).

**"mehrere gefunden"**
Two or more Sirit readers answer on the network. Settings → "Reader suchen", compare the serial ("S/N …") with the sticker, "Übernehmen".

**Antennen "keine erkannt", "… liest nichts" or one port with zero reads**
Check the RP-TNC cables, then "Reader neu verbinden". The reader-service always powers the fallback ports `1 2` and adds auto-detected ones, so "1, 2 OK" means powered, not connected; the antenna test (a tag at each antenna) is the real check.

**Laps counted twice**
Raise "Mindest-Rundenabstand (s)". Remove extra laps with "−1".

**Window blank or white**
WebView2 missing or broken: rerun the installer with Internet, or install the runtime from the link above. `shell.log` shows the startup sequence.

**Nothing happens on double-click**
Look for `crash.log` and `shell.log` in `%USERPROFILE%\.racetag\logs`. A stuck previous instance holds the lock: check Task Manager for `Racetag.exe` processes and end them.

**Racetag shows "Neue Version … verfügbar"**
The UI checked the GitHub releases API once at start. Update after the race day, not during it.

---

## 9. Data and log locations

`~` is `%USERPROFILE%` (`C:\Users\<name>`) on Windows. Settings → "Datenordner öffnen" opens `~/.racetag`.

| Path | Contents |
| --- | --- |
| `~/.racetag/data/racetag.db` | SQLite database (WAL mode): races, riders, passes, persisted config (reader IP, antenna power, lap interval, total laps, assistant done) |
| `~/.racetag/data/snapshots/<race_id>/` | Automatic CSV + database snapshots of the active race (last 30 per race) |
| `~/.racetag/logs/shell.log` | Desktop shell: startup line (version, Python, platform, data and log dir), lock, checks, supervisor, dialogs |
| `~/.racetag/logs/backend.log` | Backend and uvicorn (including `reader_ip changed via discovery: …`) |
| `~/.racetag/logs/reader.log` | Reader-service: connection states, discovery, CONTROL traffic (DEBUG with `RACETAG_DEBUG`) |
| `~/.racetag/logs/reader-stderr.log` | Raw stderr of the reader-service child |
| `~/.racetag/logs/crash.log` | Unhandled exceptions |
| `~/.racetag/logs/selftest.log` | `--selftest` runs (only when `RACETAG_LOG_DIR` points here) |
| `~/.racetag/logs/spool.jsonl` | Tag event batches that could not reach the backend, delivered later (normally empty) |
| `~/.racetag/racetag.lock` | Single-instance lock (holds the PID) |
| `~/.racetag/reader-service.pid` | PID of the reader-service child |

All `.log` files rotate at 2 MB × 5. `RACETAG_DATA_DIR` and `RACETAG_LOG_DIR` override the two directories.

---

## 10. Support bundle

Settings → "Hilfe & Support" → "Support-Paket erstellen" opens a save dialog (Desktop, `Racetag-Support-YYYYMMDD-HHMM.zip`). The zip contains every file in the log directory, a `VACUUM INTO` copy of `racetag.db` (consistent while the app runs), `config.json` (`GET /config`), `reader_status.json` (`GET /reader/status`), `app_info.json` (version, data and log dir, platform, Python, WebView2 version, supervisor status) and `notes.txt`. It contains rider names from the database.

If the app does not start, zip `%USERPROFILE%\.racetag\logs` by hand.

---

## 11. Advanced configuration

Environment variables for the desktop build (`RACETAG_BUNDLED_READER`, `RACETAG_NO_DIALOGS`, `READER_IP`, …): [apps/desktop/README.md](../apps/desktop/README.md#environment-variables-desktop-mode). Reader-service flags: [apps/reader-service/README.md](../apps/reader-service/README.md). Backend configuration and the `/reader/*` API: [apps/backend/README.md](../apps/backend/README.md). Headless self-test: `Racetag.exe --selftest` (exit code 0 = pass). The exe is windowed and prints nothing, so run it with `(Start-Process Racetag.exe -ArgumentList '--selftest' -Wait -PassThru).ExitCode` and read `selftest.log` in `RACETAG_LOG_DIR`; see [TESTING.md](TESTING.md#headless-self-test).
