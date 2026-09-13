# Plan: Racetag on a non-technical operator's Windows PC

_Date: 2026-09-13_
_Status: implemented on branch `feature/windows-nontechie` (see [§13](#13-implementation-status-2026-09-13)); not yet verified on a real Windows PC or with the real Sirit reader_

Goal: a race marshal with no technical background plugs the reader into the router, double-clicks a desktop icon, sees a green "Bereit" light, and starts the race. Nothing has to be typed into a terminal, no environment variables, no IP hunting, no manual restarts when something drops.

Four requirements from the brief, mapped to phases below:

| Requirement | Phase |
| --- | --- |
| Every startup setting automated | A, B, C |
| Autodiscovery of the reader | C |
| Tooltips explaining most things | D |
| Automatic failure recovery | A, B |
| A Windows exe (installer) | E |

---

## 0. Assumptions

- Target machine: Windows 10 22H2 or Windows 11, x64, user has **no admin rights** guaranteed. Everything installs per-user.
- Network: reader on a FritzBox LAN port (LAN2, see hardware notes) or a direct cable with Internet Connection Sharing. Both give the PC and reader a common /24 subnet.
- One reader, one PC, one operator. Multi-reader is out of scope.
- UI language for the operator is **German** (decided 2026-09-13: the operator does not read English well). The current UI mixes German and English; Phase D unifies every operator-facing string to German.
- No code signing (decided 2026-09-13). The SmartScreen "Unbekannter Herausgeber" step is documented with a screenshot instead.
- Data directory stays at `%USERPROFILE%\.racetag\` for compatibility with existing installs. It is not moved to `%APPDATA%`. (Decision needed, see §10.)

---

## 1. Where we stand (audit of the current code)

What already works and is kept as-is:

- Single binary that starts backend (thread), reader-service (subprocess) and a native window (`apps/desktop/app.py`).
- Persisted config in SQLite: reader IP, antenna power, lap interval, total laps. The shell reads reader IP and power from the backend at spawn time, so no env vars are needed once set.
- Reader auto-configuration on every connect: session bind, UTC clock push, init commands, antenna auto-detect with fallback union (`sirit_client.py`).
- Backend outage from the reader-service's view: retry with back-off, then spool to disk, drain later (`backend_client/http.py`).
- Frontend SSE reconnect with exponential back-off (`api.js`).
- Single-instance lock, reader PID file, stale-reader reap on POSIX.
- CI builds a Windows one-directory bundle and zips it on tag push.

Concrete gaps that block the goal. These are the things the plan fixes:

| # | Gap | Where | Effect on the operator |
| --- | --- | --- | --- |
| G1 | Reader-service **exits** if the reader is not reachable at start (`start()` raises, `main()` returns 1). The shell never restarts it. | `sirit_client.py:103-117`, `app.py:_spawn_reader_service` | Start the app before the reader has booted (30 s) and nothing ever connects. Must restart the app. |
| G2 | No reconnect when the reader drops the TCP connection (`_recv_loop` just breaks). | `sirit_client.py:179-212` | Cable wiggle or reader reboot mid-race silently stops timing. |
| G3 | The frontend has **no idea whether the reader is connected**. Only antenna read counts exist. | no endpoint | Operator can't tell "no riders yet" from "reader lost". |
| G4 | Changing the reader IP or antenna power in Settings needs an **app restart** ("applied on next app restart"). | `app.py`, `index.html:351` | Confusing; easy to forget. |
| G5 | No reader discovery. The operator must find the IP via router page or `arp -a`. | `docs/OPERATOR_GUIDE.md §3` | The single hardest step for a non-technical person. |
| G6 | Windows-specific dead code paths: stale-reader kill is a no-op, parent-liveness check is a no-op, "already running" dialog is macOS-only. | `app.py:275-337`, `sirit_client.py:120-145` | Orphan reader-service after a crash keeps the reader session and blocks the next launch. Second launch fails silently. |
| G7 | Fatal startup errors go to stderr, which does not exist in a windowed exe (`console=False`). | `app.py:543-545, 519-524` | App "does nothing" with no message. |
| G8 | No persistent logs unless `--debug`. Uvicorn log level warning to nowhere. | `utils.py`, `app.py:_run_server` | Nothing to send to Jan when it fails. |
| G9 | WebView2 runtime is assumed present. pywebview crashes if missing. | `PACKAGING.md §9` risk, unhandled | Blank crash on an old Windows 10. |
| G10 | Distribution is a zip of a folder. No Start menu entry, no desktop icon, SmartScreen warning. | `release.yml` | "Where is the program?" |
| G11 | Only 7 `title=` tooltips in the whole UI; header shows a Backend URL field and Connect button that make no sense in the desktop build. | `index.html` | Intimidating, unexplained controls. |
| G12 | The CI Windows build is never executed, so a missing hidden import only shows up on the operator's PC. | `release.yml` | Broken release discovered at the race. |

---

## 2. Target behaviour (what "done" looks like)

1. Operator double-clicks **Racetag** on the desktop. A window opens within 3 s with a status bar: `Reader: suche…`, `Antennen: –`, `Datenbank: OK`.
2. Within ~10 s of the reader being on the network, the status bar turns `Reader: verbunden (192.168.178.22, S/N …)`, `Antennen: 1, 2 OK`. No input was needed. If the reader is not there yet, the app keeps searching and says so.
3. If the reader IP changed (new router, DHCP), the app finds the reader again by itself and stores the new IP.
4. If the cable is pulled mid-race, status goes amber `Reader: Verbindung verloren, verbinde neu…` and returns to green when the reader is back. Passes that happened during the outage are lost (physics), everything before and after is intact. _(Amended 2026-09-13: an outage of up to about 35 s keeps both sockets open and TCP delivers the reader's queued passes afterwards; the status stays green. Only a link silent for 35–50 s, or an EOF/RST, counts as lost.)_
5. Every control has a hover tooltip in German. Settings fields have an info icon with a one-paragraph explanation and a sensible default.
6. On first launch, a three-step assistant runs: find reader → antenna test with one tag → create the first race.
7. If something is truly broken, the operator sees a dialog in German with one sentence and a button "Support-Paket erstellen" that zips the logs and database copy onto the desktop.

---

## 3. Phase A — Robust process core on Windows (fixes G6, G7, G8, G12)

**A1. `ReaderSupervisor` replaces the bare `Popen`** — new `apps/desktop/reader_supervisor.py`.
- Owns spawn, stop, restart with back-off (1 s, 2 s, 5 s, 10 s, capped at 30 s; unlimited retries, counter shown in status).
- Watches the child on a thread; a non-zero exit triggers a restart, a clean exit requested by us does not.
- `restart(reader_ip=None, antenna_power=None)` for hot config changes (used by Phase B).
- On Windows, creates a **Job Object** with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` via `ctypes` and assigns the child. When the shell dies for any reason, Windows kills the child. This replaces the POSIX parent-liveness check and the stale-PID reap on Windows. Spawn with `CREATE_NO_WINDOW`.
- Tests: fake child script that exits with 1 after N ms → supervisor restarts; requested stop → no restart; Windows-only Job Object test skipped on other platforms.

**A2. Native dialogs and crash handling** — `apps/desktop/native.py`.
- `message_box(title, text, kind)` via `ctypes.windll.user32.MessageBoxW` on Windows, `osascript` on macOS (moves the existing code there).
- `sys.excepthook` and a `try/except` around `main()` write `~/.racetag/logs/crash.log` and show a dialog with the log path.
- Replace every `sys.exit(1)` after a `print(..., file=sys.stderr)` in `app.py` with a dialog plus exit: second instance, server did not start within 10 s, data dir not writable, disk nearly full (< 200 MB).

**A3. Always-on rotating file logs** — `~/.racetag/logs/`.
- `shell.log` (desktop app), `backend.log` (uvicorn + app logger), `reader.log` (reader-service, currently only with `--debug`). `RotatingFileHandler`, 2 MB × 5 each.
- One startup line per component with version, Python version, data dir, chosen port.
- Keep stdout handlers only when a console exists (`sys.stdout is not None`).

**A4. Headless self-test mode** — `Racetag.exe --selftest`.
- Starts the backend on a free port, GETs `/config` and `/races`, spawns the reader-service with `--backend-transport mock` for 2 s, checks it is alive, exits 0. No webview.
- CI: run it on `windows-latest` right after the PyInstaller build. A missing hidden import or data file fails the pipeline, not the race day.

**A5. Windows single-instance behaviour** — keep the `msvcrt.locking` lock, add the dialog from A2. Optional: bring the existing window to front via `FindWindowW` + `SetForegroundWindow`.

---

## 4. Phase B — Reader resilience (fixes G1, G2, G3, G4)

**B1. Connect loop instead of connect-once** — `sirit_client.py`.
- `start()` no longer raises on connection failure. A `_connection_loop` thread: connect CONTROL + EVENT → run until either socket closes → mark lost → back-off (1, 2, 2… s; amended 2026-09-13, originally 1, 2, 5, 10) → reconnect → session bind + config re-run automatically (the existing `event.connection id` handling already re-binds on a new session id; make sure `session.bound` is reset on disconnect).
- `run_forever` keeps the stop-event semantics; `request_stop()` breaks the loop.
- After **N consecutive failures** (default 6, ≈ 30 s) the client calls discovery (Phase C) once per 60 s; if exactly one reader is found and it differs from the configured IP, it switches to it and reports `discovered_ip` in the heartbeat.
- Tests: a fake Sirit server (already partially in `tests/test_sirit_session.py`) that accepts, sends `event.connection id=1`, then closes → client reconnects and re-binds; server unreachable at start → client keeps retrying and connects when the server appears.

**B2. Reader status heartbeat** — reader-service → backend.
- `POST /reader/status` every 2 s and on every state change: `{state: "searching" | "connecting" | "configuring" | "active" | "lost", ip, serial, antennas: [1,2], antenna_power, last_event_at, error, discovered_ip, reader_service_version}`.
- Backend keeps the last status in memory (not SQLite), exposes `GET /reader/status`, and pushes SSE event `reader_status` on change. If no heartbeat for 6 s the backend itself flips the state to `"unknown"` so a dead reader-service is visible too.
- When `discovered_ip` is set, the backend persists it as `reader_ip` (config) and logs the change.
- Tests: backend `test_reader_status.py` (staleness timer, SSE fan-out, discovered_ip persistence).

**B3. Hot restart on Settings change** — no more "applied on next app restart".
- `PATCH /config` with a changed `reader_ip` or `antenna_power` calls `app.state.reader_controller.restart(...)` when the controller is present (desktop build). The desktop shell registers the controller on the FastAPI app in `_build_combined_app()`. In Docker mode the controller is absent and the reader-service reads the values via its own heartbeat reply (the backend can return the current config in the heartbeat response, so the reader-service notices a changed IP and reconnects on its own). Prefer the second mechanism everywhere; the controller restart is the fast path.
- `POST /reader/restart` for the UI button "Reader neu verbinden".

**B4. Periodic clock resync** — push host UTC to the reader every 30 min while connected, not only at bind (the 510 has no RTC and drifts). One `_send_control` call, low risk.

**B5. Backend watchdog** — the shell checks the uvicorn thread every 5 s; if it died, show the A2 dialog and offer restart. Rare, cheap.

---

## 5. Phase C — Autodiscovery (fixes G5)

**C1. Discovery module** — `apps/reader-service/src/discovery.py`, stdlib only (bundles cleanly, no admin, no raw sockets).
1. **ARP table first**: run `arp -a` (Windows) / `arp -a` (macOS), match the Sirit OUI `00-17-9E`. Instant when the reader has already talked on the LAN.
2. **Subnet sweep**: for each non-loopback IPv4 interface, take the /24 around the local address (cap at /24 even if the mask is wider), TCP-connect to port 50007 with a 300 ms timeout using a 64-thread pool. ~1.5 s for a full /24.
3. **Link-local probe**: always also try `169.254.1.2` (factory IP after a hard power loss, documented in the operator guide). Verify on hardware that Windows has the `169.254.0.0/16` on-link route on the reader's interface; otherwise document a one-time fix.
4. **Verification**: for every open 50007, send `info.serial_number\r\n`, expect `ok <hex>`, read `info.time` too. Close. Return `[{ip, serial, source: "arp"|"sweep"|"linklocal"}]`. Check on hardware that a short-lived second CONTROL connection does not disturb an active session (the 510 supports multiple CONTROL clients per its guide; confirm).
5. mDNS `<serial>.local` is documented but the service type is unknown; treat it as an optional accelerator later, not a dependency.
- Tests: fake CONTROL server on 127.0.0.1 answering `ok DEADBEEF01`; sweep restricted to `127.0.0.0/30` in tests; ARP parsing tested against captured Windows and macOS `arp -a` output.

**C2. Backend endpoints** — `POST /reader/discover` runs C1 in a thread pool and returns candidates; `PATCH /config` accepts the chosen IP (already exists). Available in the desktop build via `app.state.reader_controller`; in Docker the endpoint returns 501 and the reader-service's own fallback (B1) still applies.

**C3. Startup policy** (no operator input):
- No persisted IP → discover immediately; one result → use it, persist it, connect.
- Persisted IP → try it for ~30 s (B1) → discover → one result → switch and persist. Several results → status "Mehrere Reader gefunden" and the Settings modal lists them with serials.
- Zero results → keep cycling: try persisted IP, sweep every 60 s, status "Reader wird gesucht… (Kabel und Strom prüfen)".

**C4. UI** — Settings gets "Reader suchen" (lists ip + serial, one click to select) and "Reader neu verbinden". The IP field stays editable for the expert case.

---

## 6. Phase D — Operator UI (fixes G11, plus the tooltips requirement)

**D1. Status bar** replaces the bare `#status` div: three pills with colour and hover detail — Reader (state, IP, serial, last pass age), Antennen (detected ports, reads in last 60 s per port, from the existing diagnostics endpoint), Datenbank/Backend (SSE live, DB path). Amber and red states carry the recovery text ("verbinde neu in 5 s", "Kabel prüfen").

**D2. Tooltip system** — one `tooltips.js` catalogue keyed by element id, applied at load; a CSS tooltip component (`data-tip`) that also works on touch/keyboard focus, and an ⓘ icon next to each Settings field with a longer explanation and the default. Native `title=` stays as fallback. Coverage target: every button, every Settings field, every table column header, the race selector, the Koppel-Modus panel. Text lives in one file so it can be reviewed in one pass.

**D3. German unification (decided)** — `strings.js` with all operator-facing strings; the mixed "Start race / Fahrer / Register rider" becomes consistent German, including toasts, status texts, modal titles, table headers, error messages from the backend that reach the UI, and the native dialogs from A2. English stays in the code, logs and API.

**D4. Desktop mode simplification** — the backend reports `desktop: true` in `/config`; the frontend then hides the Backend URL field and Connect button, auto-connects to the same origin, and hides `Show Tag` behind an "Erweitert" toggle.

**D5. First-run assistant** — modal shown when no race exists and no reader IP is persisted: (1) Reader finden (runs C2, shows result), (2) Antennentest ("Halte einen Tag vor jede Antenne", ports turn green as reads arrive), (3) Erstes Rennen anlegen (name, laps). Skippable; re-openable from Settings.

**D6. Support tooling** — Settings buttons "Datenordner öffnen" (`os.startfile`) and "Support-Paket erstellen" (zip of `logs/` + a `VACUUM INTO` copy of `racetag.db` + `/config` + `/reader/status` JSON, saved via the existing native save dialog). Both go through the existing `js_api` bridge in `app.py`.

**D7. Optional auto-update check** — on start, GET the GitHub releases API once (ignore all errors, 3 s timeout); if a newer tag exists show a small "Neue Version verfügbar" link. No self-update. Low priority.

---

## 7. Phase E — Windows installer and release pipeline (fixes G9, G10, G12)

**E1. Keep one-directory PyInstaller** (fast start, no temp unpack) but wrap it in an **Inno Setup** installer: `Racetag-Setup-<version>.exe`.
- Per-user install to `%LOCALAPPDATA%\Programs\Racetag` (no admin prompt), Start menu entry, desktop icon (checked by default), uninstaller, "Racetag starten" at the end.
- Optional task "Beim Anmelden automatisch starten" → `HKCU\...\Run` entry. Default off. (Decision §7.)
- Upgrade in place; data dir untouched.
- Keep the zip as a portable alternative.

**E2. WebView2 runtime** — the installer checks the Evergreen runtime registry key (`HKCU\Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}` or the HKLM equivalent) and runs the bundled `MicrosoftEdgeWebview2Setup.exe /silent /install` if missing. The app additionally checks at start and shows an A2 dialog with the download link if still absent. (Windows 11 and updated Windows 10 already have it; this is the belt for old machines.)

**E3. No code signing (decided 2026-09-13, reconfirmed).** Mitigations instead:
- Deliver the installer on a USB stick or install it on the operator's PC in person. Files without the "downloaded from the internet" mark never trigger SmartScreen, and the installed app launches without any warning afterwards.
- If the operator does download it, the guide and the Spickzettel show the two clicks "Weitere Informationen → Trotzdem ausführen" with a screenshot; the installer's welcome page repeats it.
- Set `upx=False` in `pyinstaller.win.spec` (both `EXE` and `COLLECT`). UPX-packed executables are a classic Defender false-positive trigger and the size gain is irrelevant here.
- Run the first Windows build through Defender on a real PC well before race day; if it is flagged, submit it via Microsoft's false-positive form (usually cleared within one to two days).
- Revisit Azure Trusted Signing only if the app ships to more than one PC; it needs identity validation (availability for a private person in Germany unverified) and roughly 10 USD per month.

**E4. `release.yml` changes** — install Inno Setup via Chocolatey on `windows-latest`, run the A4 self-test on the built exe, build the installer, attach `Racetag-Setup-<version>.exe` and the zip. Add a `workflow_dispatch` trigger so a test build can be made without tagging. Drop the `+<sha>` suffix from tagged release file names (the operator should see `Racetag-Setup-0.3.0.exe`).

**E5. Windows CI job** — extend `ci.yml` with a `windows-latest` job that runs the desktop tests and the A4 self-test from source. Cheap insurance for `msvcrt`/Job Object code paths.

**E6. Firewall note** — uvicorn binds 127.0.0.1 only and the reader connections are outbound, so Windows Defender Firewall does not prompt. Remove the "allow the firewall prompt" instruction from the docs; keep a troubleshooting line for third-party firewalls.

---

## 8. Phase F — Documentation and field test

- Rewrite `docs/OPERATOR_GUIDE.md` Windows-first for the non-technical reader: three pages, screenshots of the installer, the status bar and the assistant. Move the SSH/CLI sections to `docs/READER_ADVANCED.md`.
- One-page German `SPICKZETTEL-WINDOWS.md`: "Rennen-Tag in 6 Schritten" plus "Wenn die Ampel rot ist".
- Dress rehearsal on the actual target PC with the actual reader: cold boot both, pull the cable mid-test, reboot the reader, kill the app from Task Manager, relaunch. Every row of the matrix below is exercised once and the observed behaviour is recorded in `docs/DRESS_REHEARSAL_COMMANDS.md`.

---

## 9. Failure-mode matrix (what recovers automatically)

| Failure | Detected by | Automatic recovery | Operator sees |
| --- | --- | --- | --- |
| Reader not booted yet at app start | B1 connect fails | Retry loop, then discovery | "Reader wird gesucht…" → green |
| Reader IP changed (DHCP, new router) | B1 failures → C1 discovery | Switch to discovered IP, persist | Toast "Reader unter neuer Adresse gefunden" |
| Reader fell back to factory IP after power loss | C1 link-local probe | Same as above | Same |
| Short cable blip (up to ~35 s) | Unanswered liveness probes (warning only) | Sockets kept open; queued passes delivered after the link returns | Nothing |
| Cable pulled / reader rebooted mid-race | Socket EOF/RST, keepalive expiry, or 3 missed liveness probes in a row | Reconnect (back-off 1, 2 s), re-bind, re-config, clock push | Amber "Verbindung verloren, verbinde neu…" then green; toast with outage duration |
| Laptop goes to sleep (idle timeout or lid closed) | — | Racetag keeps Windows awake and the display on while it runs; lid-close sleep cannot be prevented by an app (guide: "Beim Zuklappen: Nichts unternehmen") | Reconnects after wake; passes while asleep are lost |
| Window closed by accident (X / Alt+F4) | pywebview `confirm_close` | German confirmation "Racetag wirklich beenden?" | Dialog; reading continues on "Abbrechen" |
| Reader-service process crashes | A1 supervisor | Restart with back-off | Amber for a few seconds |
| Backend not reachable from reader-service | existing retry + spool | Spool, drain on recovery | Nothing, unless it persists (log) |
| SSE stream drops | existing `api.js` back-off | Reconnect | "Verbinde neu…" in status pill |
| App killed hard (Task Manager, BSOD) | Job Object, lock release | Child dies with parent; next launch is clean | Nothing |
| Second launch while running | A5 lock | Dialog, existing window to front | Dialog |
| Backend thread dies | B5 watchdog | Dialog with restart button | Dialog |
| WebView2 missing | E2 | Installer installs it; app dialog with link | Dialog (once) |
| Unhandled exception anywhere in the shell | A2 excepthook | Crash log written | Dialog with log path and support-bundle hint |
| Reader clock drift | B4 | Re-push every 30 min | Nothing |
| Disk nearly full / data dir not writable | A2 startup check | — | Dialog before anything is lost |

Not recoverable automatically and deliberately not attempted: antenna cable physically loose (status shows zero reads on that port; the D5 antenna test catches it before the start), tag passes during a reader outage, wrong LAN port on the FritzBox (LAN1 negotiates badly — documented, discovery just keeps searching).

---

## 10. Decisions

Decided 2026-09-13:

1. **UI language**: all operator-facing strings become German (the operator does not read English well).
2. **Code signing**: none. SmartScreen step is documented with a screenshot.

Still open, with the recommended default that will be used unless overruled:

3. **Autostart at login**: installer checkbox, default off.
4. **Data directory**: keep `~/.racetag`, no migration.
5. **Portable zip**: keep alongside the installer.

---

## 11. Suggested order and sizing

| Order | Phase | Size | Why this order |
| --- | --- | --- | --- |
| 1 | A1, A2, A3 | M | Everything after this needs logs, dialogs and a supervisor to be testable on Windows. |
| 2 | B1, B2 | L | The core resilience; unlocks the status bar. |
| 3 | A4, E5 | S | Catch bundling regressions before the rest lands. |
| 4 | C1–C3 | M | Discovery builds on B1's failure counter and B2's heartbeat. |
| 5 | D1, D4, C4 | M | Status bar and discovery button; first visible payoff. |
| 6 | B3, B4, B5 | S | Small follow-ups once the controller exists. |
| 7 | D2, D3 | M | Tooltip catalogue and German strings; pure frontend, can run in parallel with 4–6. |
| 8 | E1–E4, E6 | M | Installer last, so the self-test and dialogs are already in the bundle. |
| 9 | D5, D6, D7 | M | Assistant and support tooling on top of the finished pieces. |
| 10 | F | S | Docs and the dress rehearsal on the real PC. |

S ≈ half a day, M ≈ one to two days, L ≈ three days of focused work including tests.

---

## 12. Out of scope

- Multi-reader or multi-PC setups.
- A self-updating installer (only an update notice, D7).
- Touch-first or tablet layout.
- Replacing pywebview/PyInstaller with another toolchain; the current stack is adequate once the gaps above are closed.

---

## 13. Implementation status (2026-09-13)

Sections 0–12 are the original proposal; this section records what was built. Work items A1–F were implemented on branch `feature/windows-nontechie` against [PLAN-WINDOWS-NONTECHIE-CONTRACT.md](PLAN-WINDOWS-NONTECHIE-CONTRACT.md), whose §0 changes three things relative to this plan: config changes reach the reader-service through the heartbeat reply instead of a controller restart, discovery runs only inside the reader-service, and the Windows child is stopped through stdin plus a Job Object.

**Nothing has been run on a real Windows PC, and nothing has been run against the real Sirit reader.** No PyInstaller build was made, the Inno Setup script was never compiled, and neither GitHub workflow has run with these changes. "Done" below means implemented and covered by automated tests on macOS (plus the smoke tests listed in 13.1); it does not mean field-verified.

### 13.1 Verification actually performed

| Check | Result | Notes |
| --- | --- | --- |
| reader-service suite | 148 passed, 0 failed | Python 3.13 and Python 3.11.15; baseline was 52. Re-run in the final regression pass after the review fixes. |
| backend suite | 242 passed, 0 failed | Baseline 179. Re-run in the final regression pass. |
| desktop suite | 127 passed, 7 skipped, 0 failed | The 7 skipped tests are Windows-only (Job Object, WebView2 registry, `user32` prototypes, `FindWindowW`, stdin stop on Windows, known-folder Desktop, real `SetThreadExecutionState`). Baseline 18. Re-run in the final regression pass. |
| `node --check` on `strings.js`, `api.js`, `script.js`, `tooltips.js` | 4/4 OK | Re-run during the docs pass. |
| `python app.py --selftest` (source, macOS) | exit 0 in 1.1 s | All 4 steps PASS, child stopped via stdin EOF with exit 0, no process left. Re-run during the docs pass. |
| End-to-end smoke: fake Sirit + uvicorn subprocess + reader-service subprocess | 31/31 | Startup `unknown` → `active`; tag arrive counted; connection drop → `lost` → `active` with fresh session; listeners closed and reopened; `PATCH reader_ip` switch and back; `POST /reader/discover` in 4.7 s; `POST /reader/restart` via command; stdin close → `stopped` → `unknown` after 6.2 s. |
| End-to-end discovery rendezvous (in-process client, real backend) | 11/11 | Dead CLI IP → discovery switch → backend persists and logs `reader_ip changed via discovery` → `discovered_ip` cleared, no flapping; `reader_ip: null` → `searching` → found and persisted. (First run 10/11: the script asserted the INFO line on stdout without a handler; fixed with a `--log-config`.) |
| End-to-end desktop mode (combined app + real `ReaderSupervisor`) | 13/13 | `desktop: true`, version; restart via supervisor with new PID and `restart_count` 1; `antenna_power` change applied via heartbeat reply without process restart; clean stop removes PID file. |
| Frontend in headless Chrome against the backend + fake heartbeat | pass 1: 27/27, pass 2: 44/44 | Desktop and browser mode, 420 px width, assistant, pills, toasts, discovery list, tooltips, English-text scan over 6 UI states, translated 409/422 errors. No JS exceptions. Throwaway scripts, not in the repo. |
| Docker codegen of `openapi.yaml` (datamodel-codegen 0.35.0) | new schemas import | Required removing `null` from two enum lists. The backend image as a whole still fails (see 13.4). |
| `ci.yml`, `release.yml` | `actionlint` + `yaml.safe_load` clean | Not executed on GitHub. |
| PyInstaller specs | `ast.parse` + stubbed execution, spec tests in `apps/desktop/tests/test_app.py` | Hidden imports in sync, data paths exist, Windows `upx=False`. No real build. |
| `apps/desktop/installer/racetag.iss` | reviewed by hand | Never compiled (ISCC not available on macOS). |

### 13.2 Status per work item

| Item | Status | What exists | Verified by | Gaps / deviations |
| --- | --- | --- | --- | --- |
| **A1** ReaderSupervisor | Done | `apps/desktop/reader_supervisor.py`: spawn/stop/restart, restart on unexpected exit with back-off 1, 2, 5, 10, 30 s (reset after 60 s uptime), Job Object with kill-on-close and `CREATE_NO_WINDOW` on Windows, stop = close stdin → wait → terminate → kill, PID file, child stderr to `reader-stderr.log` | Unit tests with fake child scripts; real graceful stop on macOS; desktop E2E smoke | `restart()` takes no arguments and re-reads config (contract §2.7) instead of `restart(reader_ip, antenna_power)`. Job Object only covered by a Windows-only test that has not run. |
| **A2** Native dialogs, crash handling | Done | `apps/desktop/native.py` (`MessageBoxW` / `osascript`, yes/no, open path/URL, excepthook + `threading.excepthook` → `crash.log`), German dialogs for second instance, server start timeout, data dir not writable, < 200 MB free, WebView2 missing; `RACETAG_NO_DIALOGS=1` | Mocked cross-platform tests, `main()` early-exit tests | The crash dialog tells the operator to use Settings → "Support-Paket erstellen"; it has no button of its own (plan §2 item 7). ctypes paths not run on Windows. |
| **A3** Rotating logs | Done | `shell.log`, `backend.log` (`desktop_logging.py`), `reader.log` (always on, `RACETAG_FILE_LOG=0` disables), `crash.log`, `reader-stderr.log`, `selftest.log`; 2 MB × 5; startup line per component; console handlers only with a stdout; uvicorn with `log_config=None` | Tests with `sys.stdout = None`, ANSI-free file check | Rotation while another tool has the file open not checked on Windows. |
| **A4** Headless self-test | Done | `apps/desktop/selftest.py`, `--selftest` dispatch; CI: source run in `ci.yml` (`desktop-tests-windows`), frozen run in `release.yml` | macOS source run | Starts the real reader-service against closed ports (contract §4.3) instead of mock transport. Both CI runs never executed. |
| **A5** Single instance on Windows | Done | `seek(0)` before `msvcrt.locking`, `focus_existing_window()` (`FindWindowW` + `SetForegroundWindow`), "Racetag läuft bereits" dialog | Tests (POSIX lock probe; Windows-only parts skipped) | Not run on Windows. |
| **B1** Connect loop | Done | `SiritClient` connection thread: `searching`/`connecting`/`configuring`/`active`/`lost`, 3 s connect timeout, TCP keepalive (15 s idle, 5 s × 8; `TCP_MAXRT` 60 s on Windows), back-off 1, 2 s (no back-off when discovery sees the reader at the target IP), per-connection reset, generation counter, `info.time` liveness probe every 15 s (5 s reply timeout; lost only after 3 misses in a row, so a short blip keeps the connection and its queued passes), standby on every requested stop, discovery after 6 failures at most every 60 s; `--ip` optional; exit 0 on requested stop | Fake Sirit tests (unreachable at start, server close, config change, target selection), E2E smoke | Real reader reply format for probe/resync, keepalive on Windows and real cable-pull timing unverified. |
| **B2** Status heartbeat | Done | `status_reporter.py` (every 2 s + on change, 1 s timeout); backend `reader_status_hub.py`: `POST`/`GET /reader/status`, SSE `reader_status` (on change, ≤ 5 s otherwise), 6 s staleness → `unknown`, `discovered_ip` persisted before the reply, supervisor block; SSE cross-thread delivery fixed (`call_soon_threadsafe`) | Backend `test_reader_status.py` (47 tests), reporter tests, E2E smoke | Status is kept in memory only, as planned. |
| **B3** No restart on Settings change | Done | `PATCH /config` only persists; reader IP / antenna power reach the reader-service in the next heartbeat reply, which reconnects itself (same in Docker); `POST /reader/restart` (supervisor or `reconnect` command); "applied on next app restart" texts removed; `reader_ip: null` clears the persisted IP | Backend tests, E2E smoke (IP switch, power change without process restart) | Deviation from the plan's controller-restart fast path (contract §0.1). A change still interrupts reading for a reconnect of a few seconds. |
| **B4** Clock resync | Done | Re-push `info.time_zone=UTC` + `info.time` every 30 min (`--clock-resync-interval`), waits for the reply | Unit tests | Real reader's reply lines unverified. |
| **B5** Backend watchdog | Done | Server thread checked every 5 s; "Racetag – Interner Fehler" with relaunch | Unit tests | Not run on Windows; cannot be triggered without a test hook. |
| **C1** Discovery module | Done | `apps/reader-service/src/discovery.py`: ARP (Sirit OUI), /24 sweep per interface + default route, factory IP `169.254.1.2`, `info.serial_number` verification, 64 workers, Windows tools with `CREATE_NO_WINDOW` | Parser tests on hand-written Windows (English/German) and macOS samples, probe and `127.0.0.0/30` sweep tests, E2E | Real `arp -a`/`ipconfig` output in the OEM code page, factory-IP routing on Windows, and the effect of a second CONTROL connection on a live session unverified. mDNS not used (as planned). Docker image lacks `arp`/`ip`, so only the default-route sweep runs there. |
| **C2** Backend discovery endpoints | Done | `POST /reader/discover` queues a command and waits ≤ 15 s; works in Docker too (no 501) | Backend tests, E2E | 15 s may be tight on PCs with many adapters. |
| **C3** Startup policy | Done | No IP → discover; known IP → retries then discovery; one result → switch and persist; several → `multiple readers found`; none → keep cycling | Connection-loop tests, discovery rendezvous E2E | A manual "Reader suchen" never replaces a configured target; the operator picks with "Übernehmen". |
| **C4** Settings UI | Done | "Reader suchen" with candidate list (IP, S/N, source, "Übernehmen"), "Reader neu verbinden", editable IP field | Headless Chrome | Against the real reader-service on Windows unverified. |
| **D1** Status bar | Done | Pills "Reader", "Antennen", "Verbindung" with colours, live tooltips and click actions; recovery toasts | Headless Chrome + in-page probes | The third pill is "Verbindung" (SSE state, data folder in the tooltip), not "Datenbank". |
| **D2** Tooltips | Done | `tooltips.js`: 115 German entries, 11 ⓘ info icons starting with the default, dynamic row tips, keyboard and touch handling | Coverage check script (every control has a tip) | Hover on disabled buttons in WebView2 and touch behaviour unverified. |
| **D3** German UI | Done | `strings.js` with 310 keys, `lang="de"`, backend error texts translated via `RT.apiError`, German native dialogs and installer pages | English-text scan in headless Chrome | Intentional English: product name "Racetag Live", CSV column names, DNF/DNS/DSQ, loan words (Reader, Tag, Snapshot). Layout with longer German labels at 125/150 % scaling unverified. |
| **D4** Desktop mode | Done | `desktop: true` in `/config` hides Backend URL and Connect, same origin, "Tag anzeigen" under Settings → "Erweitert" | Headless Chrome | `RACETAG_BUNDLED_READER=0` reports `desktop: false`. |
| **D5** First-run assistant | Done | Three steps (Reader finden, Antennentest, Erstes Rennen), skippable, reopen from Settings; the "done" flag is persisted in the backend (`assistant_done` in `GET`/`PATCH /config`, meta key `assistant_done`), set on finish and on "Überspringen" | Headless Chrome (steps); backend tests for `assistant_done` | Decided 2026-09-13: backend flag instead of `localStorage`, which pywebview (`private_mode=True`, random port) wipes on every launch (contract §0.4, §2.6). `localStorage` remains only as a fallback in browser mode against a backend without the field. Not reappearing after a real app restart is unverified on Windows. |
| **D6** Support tooling | Done | `support_bundle.py` (logs, `VACUUM INTO` DB copy, `config.json`, `reader_status.json`, `app_info.json`, `notes.txt`), js_api `open_data_folder`, `create_support_bundle`, `app_info`; buttons in Settings | Unit tests, headless Chrome (without pywebview) | Save dialog, open data folder and the WAL-mode DB copy while running unverified on Windows. |
| **D7** Update notice | Done, unverified | One GitHub releases API call with 3 s timeout, link "Neue Version … verfügbar" | – | The API returned 404 for `jan-knoblich/racetag` (private repo or no release), so the notice was never seen. Whether `target=_blank` opens the system browser from pywebview is unverified. |
| **E1** Inno Setup installer | Partial | `apps/desktop/installer/racetag.iss`: per-user, `{localappdata}\Programs\Racetag`, German only, desktop icon ticked, autostart unticked, upgrade in place (old `_internal` removed), data kept on uninstall; zip kept | Hand review | Never compiled or installed. |
| **E2** WebView2 runtime | Partial | Installer checks `pv` under HKCU/HKLM and runs the bootstrapper silently after the file copy (warning with link on failure); app checks at start (also treats runtimes < 86.0.622.0 as missing) | Mocked tests of the app check | Installer path and app dialog never run on Windows. |
| **E3** No code signing | Partial | `upx=False` in `EXE` and `COLLECT`; SmartScreen hint on the installer welcome page and in the German guide | Spec test | SmartScreen screenshots not made (placeholders in the guide); Defender scan of a real build not done. |
| **E4** `release.yml` | Done, not run | `workflow_dispatch`; frozen `--selftest` with `RACETAG_NO_DIALOGS=1`; bootstrapper download with signature check; ISCC (preinstalled or Chocolatey); names without `+sha` for tags, with `+sha` for dispatch builds; Release only on tags | `actionlint` | Never executed. |
| **E5** Windows CI job | Done, not run | `desktop-tests-windows` in `ci.yml`: pytest + `python app.py --selftest` | `actionlint` | Never executed. |
| **E6** Firewall note | Done | Loopback-only backend and outbound reader connections documented; "allow the firewall prompt" removed | – | Absence of a Defender prompt unverified on Windows. |
| **F** Documentation and field test | Partial | [OPERATOR_GUIDE.md](OPERATOR_GUIDE.md) rewritten Windows-first (technical), new [BEDIENUNGSANLEITUNG-WINDOWS.md](BEDIENUNGSANLEITUNG-WINDOWS.md), [SPICKZETTEL-WINDOWS.md](SPICKZETTEL-WINDOWS.md), [READER_ADVANCED.md](READER_ADVANCED.md); READMEs and [TESTING.md](TESTING.md) updated; rehearsal checklist [DRESS_REHEARSAL_COMMANDS.md §9](DRESS_REHEARSAL_COMMANDS.md#9-windows-resilience-rehearsal) | Doc review against the final UI strings | Screenshots not made. The dress rehearsal on the target PC with the real reader has not happened. |

### 13.3 Target behaviour (§2) against the implementation

| # | Target | State |
| --- | --- | --- |
| 1 | Window within 3 s, status bar | Implemented with pills "Reader", "Antennen", "Verbindung". Start time on Windows not measured. |
| 2 | Green within ~10 s of the reader being on the network, no input | Implemented (connect retry, discovery without IP). Timing with the real reader not measured. |
| 3 | Changed reader IP found and stored | Implemented; verified against the fake reader. |
| 4 | Cable pull → amber "Verbindung verloren, verbinde neu…" → green, outage toast | Implemented; verified against the fake reader. |
| 5 | German tooltips, info icons with defaults | Done. |
| 6 | Three-step first-run assistant | Done; finished/skipped state persisted in the backend. |
| 7 | German dialog with support-bundle path when something is broken | Partial: dialogs point to Settings → "Support-Paket erstellen" and name the log path; no button inside the dialog. |

### 13.4 Open issues and decisions

- **Docker backend image** fails to start: the Dockerfile regenerates `models_api.py` from `openapi.yaml`, which lacks `RaceSummaryDTO` and other DTOs. Predates this work; either complete `openapi.yaml` or stop overwriting the committed `models_api.py`.
- **Docker logging**: under plain uvicorn, INFO lines of `racetag.backend` (e.g. `reader_ip changed via discovery`) have no handler. In the desktop build they go to `backend.log`.
- **Bootstrap race name** is the English "Default race" in the database and exports; the UI shows "Standard-Rennen" and the assistant relies on the English name.
- **`generate_win_version_info.py`** still writes an English (1033) string table into the exe's file properties.
- **§10 open decisions** were implemented with their recommended defaults: autostart checkbox off, data directory stays `~/.racetag`, portable zip kept.

### 13.5 Still to verify on a real Windows PC

Checklist with result columns: [DRESS_REHEARSAL_COMMANDS.md §9](DRESS_REHEARSAL_COMMANDS.md#9-windows-resilience-rehearsal).

- **Build and CI:** a real PyInstaller build of both specs resolves all hidden imports (including `discovery`, `status_reporter`, `reader_status_hub`); `desktop-tests-windows` passes (pytest, pythonnet install, `python app.py --selftest` < 3 min); `release.yml` produces the zip and `Racetag-Setup-<version>[+sha].exe`, the frozen self-test returns exit code 0 through `Start-Process -Wait -PassThru`, and a deliberately broken build fails fast with `crash.log` printed; Inno Setup ≥ 6.3 is found or installed via Chocolatey; the bootstrapper signature check matches.
- **Installer:** `racetag.iss` compiles (preprocessor version read, `RegQueryStringValue`, `Exec`, marquee progress, uninstall message); no UAC prompt; install to `%LOCALAPPDATA%\Programs\Racetag`; German welcome text not truncated; desktop icon ticked, autostart unticked; "Racetag starten" works; upgrade over a running instance; autostart tick/untick across two installs; uninstall keeps `%USERPROFILE%\.racetag`; WebView2 silent install on a machine without the runtime and the warning without Internet.
- **Defender and SmartScreen:** Defender scan of the first non-UPX build and the installer; SmartScreen flow for a downloaded installer (and screenshots for the guide).
- **Process lifecycle:** killing the shell in Task Manager kills the reader-service child at once (Job Object) and the next launch is clean; closing the window stops the child gracefully (`stdin closed`, spool flush, exit 0); `sys.stdin` exists in the frozen windowed child; second launch brings the window to the front and shows "Racetag läuft bereits", and the lock holds after the PID write.
- **Startup checks and dialogs:** WebView2 check has no false positive on normal Windows 10/11 (including per-user HKCU installs) and shows the dialog when missing; the frozen windowed exe starts uvicorn with `sys.stdout = None`; `shell.log`, `backend.log`, `reader.log` land in `%USERPROFILE%\.racetag\logs` and an import error of the child lands in `reader-stderr.log`; watchdog dialog and relaunch after a server-thread death.
- **Reader-service on Windows:** `SIO_KEEPALIVE_VALS` applied without error; `arp`/`ipconfig` run without a console flash and their German/English OEM-code-page output parses; `reader.log` rotation while the support bundle reads it; SSE `reader_status` frames from worker threads reach the WebView2 window immediately (Proactor event loop).
- **Power and window:** the laptop does not sleep or turn the screen off on idle while Racetag runs (`SetThreadExecutionState`), and does again after closing it; X / Alt+F4 shows the German "Racetag wirklich beenden?" dialog and "Abbrechen" keeps timing; reopening right after closing waits for the old instance instead of showing "läuft bereits"; a system proxy in the Internet Options does not break the reader-service heartbeat.
- **UI in WebView2:** pywebview API bridge available after load; the assistant stays closed after "Überspringen" and an app restart; "Datenordner öffnen" and "Support-Paket erstellen" (save dialog on the Desktop, cancel path, DB copy while running); update link opens the system browser; tooltips on disabled buttons; German date/time placeholder; layout at 1280×800 with 125 % and 150 % scaling; touch behaviour if the PC has a touch screen.

### 13.6 Still to verify with the real Sirit reader

- `info.time` (liveness probe) and `info.time_zone=UTC` / `info.time=<iso>` (resync) each return exactly one `ok`/`error` line; no false "lost" from the 5 s reply timeout.
- A short second CONTROL connection from a discovery probe does not disturb an active session.
- Pulled cable and reader reboot mid-session: time until `lost` (keepalive 15 s + 5 s × 8, or 3 missed probes at 15 s + 5 s each: 35–50 s) and a clean reconnect (back-off 1, 2 s) with a new `event.connection id`, full re-bind, clock push and antenna config.
- Pull the reader-side cable for 20–30 s while tags are read: the queued arrives are delivered after replugging, over the same EVENT connection (no `lost`, no new `event.connection id`).
- Unplug the PC-side cable on Windows for 20–30 s: check whether the sockets survive media sense (Windows may reset connections when the adapter goes down).
- `TCP_MAXRT` and `TCP_KEEPCNT` are accepted on the Windows 10/11 target (look for keepalive warnings in `reader.log`).
- Discovery on the real LAN while connected (connection thread busy ~4–5 s, events keep flowing) and within the 15 s backend timeout on PCs with several adapters (VPN, Hyper-V, VirtualBox).
- Windows reaches the factory IP `169.254.1.2` from a PC on the FritzBox subnet (on-link `169.254.0.0/16` route); otherwise a reader that lost its DHCP config after a power loss is not found automatically.
- "Reader neu verbinden" in the desktop build (supervisor process restart): the new child re-binds, pushes the clock and configures antennas without leaving a stale session on the reader.
- Antenna test tiles and the Antennen pill react fast enough with real tags (status polled every 2 s in the assistant); pills and toasts behave correctly when the cable is pulled mid-race; "Übernehmen" from the discovery list works against the real reader-service.
- Reader tooltip shows German text for real error strings (unknown ones fall back to "technischer Fehler (Details im Protokoll)").
- Measured times for R1, R2, R4 and H3 of the rehearsal checklist are to be entered here once the rehearsal has taken place.
