# Racetag

RFID lap-timing for bicycle round-course races. Detects tag passings via a Sirit INfinity 510 reader, computes live standings, and displays them in a browser UI.

## Monorepo layout

| Path | Role | Language / stack |
| --- | --- | --- |
| [`apps/reader-service/`](apps/reader-service/) | TCP client for the Sirit INfinity 510 — discovers the reader, reconnects automatically, normalises tag events and forwards them plus a status heartbeat to the backend | Python 3.11 |
| [`apps/backend/`](apps/backend/) | FastAPI service — race state, rider registry, lap counting, reader status, SSE fan-out, SQLite persistence | Python 3.13 / FastAPI |
| [`apps/frontend/`](apps/frontend/) | Static UI (German) — live standings, status bar, rider registration, settings, first-run assistant, tooltips | HTML / CSS / vanilla JS |
| [`apps/desktop/`](apps/desktop/) | pywebview shell that bundles all three into `Racetag.app` / `Racetag.exe` (supervised reader-service, logs, self-test, Windows installer) | Python + PyInstaller + Inno Setup |

Each `apps/<name>/` directory was merged in via `git subtree add`; full pre-merge history is preserved (`git log apps/backend/`).

---

## Quickstart

### 1. Desktop app (recommended for end users)

1. Download from [GitHub Releases](../../releases):
   - **Windows:** `Racetag-Setup-<version>.exe` (per-user installer, no admin rights; installs the WebView2 runtime if it is missing). `Racetag-<version>-win.zip` stays available as a portable alternative.
   - **macOS:** `Racetag-<version>-mac.zip`.
2. Run the installer and start Racetag from the desktop icon (Windows), or unzip and double-click `Racetag.app` (macOS).
3. On first run, Racetag creates `~/.racetag/data/` and opens the UI in a native window with a short setup assistant.
4. The reader is found on the network automatically (no IP to enter); Settings → "Reader suchen" lists readers if more than one is present. The status bar shows reader, antennas and connection state, and the app reconnects by itself after a cable pull or reader reboot.
5. Logs are in `~/.racetag/logs/`; Settings → "Support-Paket erstellen" zips them together with a database copy.

No Docker, no Python install required. Operator documentation: [German guide for the race marshal](docs/BEDIENUNGSANLEITUNG-WINDOWS.md), [one-page cheat sheet](docs/SPICKZETTEL-WINDOWS.md), [technical operator guide](docs/OPERATOR_GUIDE.md).

> Status 2026-09-13: the Windows installer, discovery and automatic recovery are implemented and tested on macOS against a simulated reader, but not yet verified on a real Windows PC or with the real reader. See [docs/PLAN-WINDOWS-NONTECHIE.md §13](docs/PLAN-WINDOWS-NONTECHIE.md#13-implementation-status-2026-09-13).

### 2. Docker Compose (LAN deployment with a real reader)

```bash
# Clone the repo
git clone <repo-url> racetag && cd racetag

# Optional: customise env vars (see apps/*/.env.example)
cp apps/backend/.env.example apps/backend/.env
cp apps/reader-service/.env.example apps/reader-service/.env
# Optional: set READER_IP=<reader IP> in apps/reader-service/.env.
# Without it the reader-service searches the network for the reader.

# Start all three services
docker compose up --build
```

- Backend: http://localhost:8600
- Frontend: http://localhost:8680

The reader-service container uses `network_mode: host` so it can reach the Sirit reader on the LAN. The slim image has no `arp`/`ip` tools, so discovery there only sweeps the default route's /24 plus the reader's factory IP.

> Known issue (predates the Windows work): the backend image regenerates `models_api.py` from `openapi.yaml`, which lacks several DTOs (e.g. `RaceSummaryDTO`), so the backend container currently fails to start. The desktop build and native dev are unaffected.

See each app's README for the full env var list:
[reader-service](apps/reader-service/README.md) · [backend](apps/backend/README.md) · [frontend](apps/frontend/README.md)

### 3. Native dev (for contributors)

```bash
# backend
cd apps/backend
python3.13 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
uvicorn --app-dir racetag-backend app:app --reload --host 0.0.0.0 --port 8600

# reader-service (separate shell)
cd apps/reader-service
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python src/racetag_reader_service.py --backend-url http://localhost:8600   # add --ip <reader-ip> to skip discovery

# frontend (separate shell)
cd apps/frontend && python3 serve.py --host 127.0.0.1 --port 8680
```

See [docs/TESTING.md](docs/TESTING.md) for test setup and CI details.

---

## Key features

- Live lap timing with RFID tags (Sirit INfinity 510, UHF passive)
- Multi-antenna sensor fusion — per-antenna presence tracking and per-tag cooldown eliminate double-counts
- Fast rider coupling — scan a tag from the timing line to trigger the register-rider modal
- SQLite persistence (`journal_mode=WAL`, `synchronous=FULL`) — race and rider data survive restarts
- Browser-local timestamp display — reader captures UTC; frontend renders in the visitor's timezone
- Antenna diagnostics panel — per-antenna read counts for the last configurable window
- Reader discovery (ARP table, /24 sweep, factory IP) and automatic reconnect with a live status heartbeat
- Status bar (Reader / Antennen / Verbindung) with German recovery hints, German UI with tooltips on every control, first-run assistant
- Settings UI — reader IP, antenna power, lap cooldown, lap count and snapshot interval; changes apply without restarting the app
- Windows desktop build: per-user installer, supervised reader-service child, rotating logs, crash dialogs, support bundle, headless `--selftest`

---

## API surface

Main endpoints. Every route with its parameters: [`apps/backend/README.md`](apps/backend/README.md) and `GET /docs` on a running backend. [`apps/backend/openapi.yaml`](apps/backend/openapi.yaml) is incomplete (race endpoints and several DTOs are missing).

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/events/tag/batch` | Ingest a batch of tag events from the reader-service |
| `GET` | `/classification` | Standings snapshot of the active race (ordered) |
| `GET` | `/classification.csv`, `/races/{race_id}/classification.csv` | Results as CSV |
| `GET` | `/tags.csv` | Every tag read in the active race, as a start-list template |
| `GET` | `/race` | Active race metadata + participant list |
| `PATCH` | `/race` | Update `total_laps` |
| `POST` | `/race/start`, `/race/end`, `/race/reopen`, `/race/reset` | Race lifecycle of the active race (`reset` clears passes, keeps riders) |
| `GET` / `POST` | `/races` | List / create races |
| `GET` / `PATCH` / `DELETE` | `/races/{race_id}` | One race |
| `POST` | `/races/{race_id}/activate` | Make a race the active race |
| `GET` / `POST` | `/races/{race_id}/snapshots` | List / write CSV + database snapshots |
| `GET` | `/config` | Effective config (env defaults merged with persisted overrides) plus `desktop` and `version` |
| `PATCH` | `/config` | Update `reader_ip` (`null` clears it), `antenna_power`, `min_lap_interval_s` or `total_laps`; never restarts anything |
| `POST` | `/reader/status` | Reader-service heartbeat; the reply carries reader config and an optional command |
| `GET` | `/reader/status` | Last reader status (`unknown` after 6 s without heartbeat) plus supervisor status |
| `POST` | `/reader/discover` | Search the network for readers (waits up to 15 s) |
| `POST` | `/reader/restart` | Reconnect the reader (`202`) |
| `GET` | `/diagnostics/antennas` | Per-antenna pass counts (`?window_s=60`) |
| `GET` | `/stream` | SSE stream: `lap`, `standings`, `unknown_tag`, `tag_seen`, `race_started`, `race_ended`, `race_reopened`, `active_race_changed`, `race_reset`, `race_updated`, `reader_status` |
| `POST` / `GET` | `/riders` | Register or update a rider (upsert by `tag_id`) / list riders |
| `GET` | `/riders/recent-reads` | Recent unregistered tag passings (ring buffer) |
| `GET` / `DELETE` | `/riders/{tag_id}` | Look up / remove a rider |
| `PATCH` | `/riders/{tag_id}/status` | Set DNF / DNS / DSQ |
| `POST` / `DELETE` | `/riders/{tag_id}/laps` | Add a manual lap / remove the last lap |
| `DELETE` | `/riders/{tag_id}/passes` | Delete all passes of a rider |

Reader status protocol (heartbeat, commands, discovery): [docs/READER_ADVANCED.md §7](docs/READER_ADVANCED.md#7-status-heartbeat-and-command-protocol-summary) and [docs/PLAN-WINDOWS-NONTECHIE-CONTRACT.md](docs/PLAN-WINDOWS-NONTECHIE-CONTRACT.md) §2.

Authentication is via `X-API-Key` header. The key is **off by default** in the packaged build; set `RACETAG_API_KEY` to enable it.

---

## Build and release

To cut a release, push a version tag:

```bash
git tag v0.1.0
git push origin v0.1.0
```

The CI workflow (`.github/workflows/release.yml`) builds `Racetag-<version>-mac.zip`, `Racetag-<version>-win.zip` and the Windows installer `Racetag-Setup-<version>.exe` on macOS and Windows runners, self-tests the frozen Windows exe (`Racetag.exe --selftest`) and attaches the files to the GitHub Release automatically. A manual run (`workflow_dispatch`) builds test files named `...-<version>+<sha>...` without creating a release. See [apps/desktop/installer/README.md](apps/desktop/installer/README.md) for building the installer locally.

To build locally (CI pins `pyinstaller==6.19.*`):

```bash
pip install "pyinstaller==6.19.*"
cd apps/desktop && pyinstaller pyinstaller.mac.spec --clean   # macOS   -> dist/Racetag.app
cd apps/desktop && pyinstaller pyinstaller.win.spec --clean   # Windows -> dist/Racetag/Racetag.exe (one-directory build)
```

Then check the Windows build headless. The exe is windowed and prints nothing, and a shell does not wait for it, so read the exit code through `Start-Process` in PowerShell (`0` = pass, step results in `selftest.log` under `RACETAG_LOG_DIR`):

```powershell
$env:RACETAG_LOG_DIR = "$env:TEMP\rt-selftest"; $env:RACETAG_NO_DIALOGS = "1"
(Start-Process dist\Racetag\Racetag.exe -ArgumentList '--selftest' -Wait -PassThru).ExitCode
```

Neither the installer nor the release workflow has been run yet with the current changes.

---

## Testing

See [docs/TESTING.md](docs/TESTING.md). As of 2026-09-13: reader-service 148, backend 242, desktop 134 tests (127 passed + 7 Windows-only skipped on macOS). The CI workflow (`.github/workflows/ci.yml`) runs all three suites on Ubuntu and the desktop suite plus `python app.py --selftest` on Windows on every push; the release workflow self-tests the frozen Windows exe.

---

## Related docs

- [docs/BEDIENUNGSANLEITUNG-WINDOWS.md](docs/BEDIENUNGSANLEITUNG-WINDOWS.md) — German step-by-step guide for the race marshal (Windows)
- [docs/SPICKZETTEL-WINDOWS.md](docs/SPICKZETTEL-WINDOWS.md) — German one-page race-day cheat sheet
- [docs/OPERATOR_GUIDE.md](docs/OPERATOR_GUIDE.md) — technical setup and support guide (install, network, recovery, logs)
- [docs/READER_ADVANCED.md](docs/READER_ADVANCED.md) — reader SSH/CLI, one-time network setup, discovery and heartbeat internals
- [docs/DRESS_REHEARSAL_COMMANDS.md](docs/DRESS_REHEARSAL_COMMANDS.md) — rehearsal notes and the Windows resilience checklist (§9)
- [docs/PLAN-WINDOWS-NONTECHIE.md](docs/PLAN-WINDOWS-NONTECHIE.md) — Windows plan with implementation status (§13); interfaces in [docs/PLAN-WINDOWS-NONTECHIE-CONTRACT.md](docs/PLAN-WINDOWS-NONTECHIE-CONTRACT.md)
- [apps/desktop/installer/README.md](apps/desktop/installer/README.md) — building and testing the Windows installer
- [docs/TESTING.md](docs/TESTING.md) — local test runs and CI overview
- [docs/DEV_TOOLS.md](docs/DEV_TOOLS.md) — local dev helpers (e.g. `rtsearch` repo search via Ollama)

---

## License

Inherited per-subdirectory from the upstream source repos; see each `apps/*/LICENSE` where present.
