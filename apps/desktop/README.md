# apps/desktop — Racetag desktop bundle

Packages the backend (`apps/backend/`), frontend (`apps/frontend/`), and reader-service (`apps/reader-service/`) into a single double-clickable binary per OS using **pywebview** + **PyInstaller**.

## What it does

`app.py` is the main entry point. `main()` runs these steps in order:

1. `--reader-service` → runs the bundled reader-service instead of the shell (used for the child process in frozen mode). `--selftest` → runs `selftest.run_selftest()` and exits with its code.
2. Sets `RACETAG_DATA_DIR` (`~/.racetag/data`), `RACETAG_LOG_DIR` (`~/.racetag/logs`) and `RACETAG_VERSION` (from `VERSION`) unless already set, and creates the directories.
3. Sets up rotating file logging (`desktop_logging.py`) and the crash handler (`native.install_excepthook`: `crash.log` + German dialog).
4. Takes the single-instance lock `~/.racetag/racetag.lock` (`msvcrt.locking` after `seek(0)` on Windows, `fcntl.flock` elsewhere). If it is held: bring the existing window to the front (Windows) and show "Racetag läuft bereits", exit 1.
5. Checks that the data dir is writable with ≥ 200 MB free, and on Windows that the WebView2 runtime is installed; each failure shows a German dialog and exits 1.
6. Reaps a reader-service orphaned by a hard crash (POSIX; Windows uses the Job Object instead).
7. Loads the backend (`apps/backend/racetag-backend/app.py` as `racetag_backend_app`), mounts the frontend at `/`, and registers a `ReaderSupervisor` as `app.state.reader_controller` (skipped with `RACETAG_BUNDLED_READER=0`).
8. Starts uvicorn on `127.0.0.1:<free port>` in a thread (`log_config=None`, so it works without a console) and waits up to 10 s ("Racetag – Startfehler" otherwise).
9. Starts the supervisor, which spawns the reader-service child with `--stop-on-stdin-eof`, `--ip` only when a reader IP is known (persisted, or the `READER_IP` fallback), and the persisted antenna power. The argv is rebuilt from the backend's effective config on every spawn. Starts the backend watchdog (checks the server thread every 5 s, offers a relaunch).
10. Opens the pywebview window (`gui="edgechromium"` on Windows) with the `js_api` bridge `_RacetagApi`: `save_csv`, `open_data_folder`, `create_support_bundle`, `app_info`.
11. On window close: stops the supervisor (close stdin → wait → terminate → kill) and uvicorn.

Configuration changes (reader IP, antenna power) do **not** restart anything: the reader-service picks them up from its next heartbeat reply. The supervisor restarts the child only after a crash (back-off 1, 2, 5, 10, 30 s, reset after 60 s uptime) or when the UI calls `POST /reader/restart`.

## Current files

```
apps/desktop/
├── app.py                      # main entry point: startup order, argv builder, watchdog, js_api bridge
├── desktop_logging.py          # shell.log + backend.log (rotating), console handler only with a stdout
├── native.py                   # message boxes, yes/no, open path/url, crash handler, WebView2 check, disk space, focus window
├── reader_supervisor.py        # ReaderSupervisor: spawn/stop/restart, back-off, Windows Job Object, PID file
├── support_bundle.py           # Racetag-Support-YYYYMMDD-HHMM.zip (logs, VACUUM INTO db copy, JSON)
├── selftest.py                 # headless --selftest
├── pyinstaller.mac.spec        # macOS build spec
├── pyinstaller.win.spec        # Windows build spec (upx=False)
├── installer/
│   ├── racetag.iss             # Inno Setup 6.3+ installer script
│   └── README.md               # building and testing the installer
├── generate_win_version_info.py
├── win_version_info.txt        # Windows VERSIONINFO resource (generated from VERSION)
├── VERSION                     # single-line version string, e.g. "0.1.0"
├── requirements.txt
├── requirements-dev.txt
├── pyproject.toml
├── tests/
└── icons/
    ├── generate_icon.py
    ├── racetag-source.png
    ├── racetag.icns            # macOS icon
    └── racetag.ico             # Windows icon
```

## Build locally

```bash
# Install PyInstaller (CI uses 6.19.*), inside a venv with the desktop,
# backend and reader-service requirements installed
pip install "pyinstaller==6.19.*"

# macOS
cd apps/desktop
pyinstaller pyinstaller.mac.spec --clean
# Produces: dist/Racetag.app

# Windows
cd apps/desktop
python generate_win_version_info.py
pyinstaller pyinstaller.win.spec --clean --noconfirm
# Produces the one-directory build dist/Racetag/ with dist/Racetag/Racetag.exe
```

Then self-test the frozen Windows build in PowerShell. `Racetag.exe` is a windowed program without a console: started directly, it prints nothing, the shell returns at once and does not put its exit code into `$LASTEXITCODE` / `%ERRORLEVEL%`, so an immediate silent return says nothing about the bundle. Wait for the process and read its exit code instead:

```powershell
$env:RACETAG_LOG_DIR = "$env:TEMP\rt-selftest"; $env:RACETAG_NO_DIALOGS = "1"
(Start-Process dist\Racetag\Racetag.exe -ArgumentList '--selftest' -Wait -PassThru).ExitCode
# 0 = bundle is complete; step results (PASS/FAIL per step) are in $env:TEMP\rt-selftest\selftest.log
```

In `cmd.exe`: `set RACETAG_LOG_DIR=%TEMP%\rt-selftest`, `set RACETAG_NO_DIALOGS=1`, then `start "" /wait dist\Racetag\Racetag.exe --selftest` and `echo %ERRORLEVEL%`. Close the shell afterwards so `RACETAG_NO_DIALOGS` does not stay set for a normal start. Details: [Self-test](#self-test) and [docs/TESTING.md](../../docs/TESTING.md#headless-self-test).

Both specs bundle, as data files, `apps/frontend` → `frontend`, `apps/backend/racetag-backend` → `backend_src`, `apps/reader-service/src` → `reader_src` and `VERSION` → the bundle root. The hidden-import lists (desktop modules, reader-service modules including `discovery` and `status_reporter`, backend modules including `reader_status_hub`) must stay in sync between the two specs; `tests/test_app.py` checks this and that the Windows spec has `upx=False` in `EXE` and `COLLECT` (UPX-packed executables are a common Defender false positive).

The Windows installer wraps `dist/Racetag`: see [installer/README.md](installer/README.md).

## Release

First set the version, commit, then tag that commit with the **same** version and push:

```bash
echo 0.1.0 > apps/desktop/VERSION
git commit -am "release 0.1.0"
git tag v0.1.0
git push origin HEAD v0.1.0
```

CI refuses a tag that does not match `VERSION` (a `-rc`/`-beta` suffix on the tag is ignored), because the installed app would report the old version and the update notice would never go away. The full list of release gates (tag/VERSION check, hidden-import errors, GUI stack imports and DLLs, frozen self-test) is in [installer/README.md](installer/README.md#releases-from-ci).

The workflow at `.github/workflows/release.yml` runs on `macos-latest` and `windows-latest`, builds both binaries, runs the headless self-test on the frozen Windows exe, builds the Inno Setup installer and attaches `Racetag-<version>-mac.zip`, `Racetag-<version>-win.zip` and `Racetag-Setup-<version>.exe` to the GitHub Release. Manual runs (`workflow_dispatch`) produce test files with a `+<sha>` suffix and no release. The version string is read from `apps/desktop/VERSION`. Installer details and a manual test checklist: [installer/README.md](installer/README.md).

## Self-test

`python app.py --selftest` (source) or `Racetag.exe --selftest` (frozen; run it through `Start-Process -Wait -PassThru` as shown in [Build locally](#build-locally), since the windowed exe prints nothing and a shell does not wait for it) starts the backend, the frontend files and a reader-service child against temporary directories, checks that a reader heartbeat arrives and exits `0` on success. It never touches `~/.racetag`; step results go to `selftest.log` in `RACETAG_LOG_DIR` (or a `racetag-selftest-*` temp dir).

## Logs

All logs rotate at 2 MB × 5 files and live in `~/.racetag/logs/` (override with `RACETAG_LOG_DIR`):

| File | Written by |
| --- | --- |
| `shell.log` | desktop shell (startup, supervisor, dialogs) |
| `backend.log` | backend and uvicorn |
| `reader.log` | reader-service child |
| `reader-stderr.log` | raw stderr of the reader-service child (import errors of a broken build land here) |
| `crash.log` | unhandled exceptions, also announced in a dialog |
| `selftest.log` | `--selftest` runs |

Settings → "Support-Paket erstellen" zips these logs, a copy of the database and the current config/reader status.

## Environment variables (desktop mode)

In the packaged app, the backend and reader-service read their config from env vars set by `app.py`. The most important ones an operator can override:

| Variable | Default | Purpose |
| --- | --- | --- |
| `RACETAG_DATA_DIR` | `~/.racetag/data/` | SQLite database and `snapshots/` |
| `RACETAG_LOG_DIR` | `~/.racetag/logs/` | Log directory (see Logs) and the reader-service event spool `spool.jsonl` |
| `RACETAG_VERSION` | contents of `VERSION` | Version shown in the UI, in `GET /config` and in the reader heartbeat; an explicit value wins (useful to test the update notice) |
| `READER_IP` | none | Fallback reader IP when none is saved; without any IP the reader-service searches the network. Usually set in the Settings modal instead |
| `MIN_LAP_INTERVAL_S` | `0` | Reader-side cooldown passed to the reader-service subprocess (the backend is the lap-cooldown authority) |
| `RACETAG_BUNDLED_READER` | `1` | Set to `0` to skip spawning the reader subprocess (useful when running a separate reader during development). No reader controller is registered then, so `GET /config` reports `desktop: false` and the UI shows the browser-mode controls |
| `RACETAG_NO_DIALOGS` | unset | `1` logs native dialogs (errors, crash reports) instead of showing them; used by CI and the test suite |

## Branding invariants

- **Product name:** `Racetag` (not `RaceTag`, `racetag`, or `race-tag` in user-visible strings)
- **Bundle identifier:** `com.racetag.app` (as set in `pyinstaller.mac.spec`; changing it makes macOS treat the app as a new one and ask for permissions again)
- **Version:** single source of truth is [`VERSION`](VERSION)
- **Icon:** `icons/racetag.icns` (macOS) and `icons/racetag.ico` (Windows). Regenerate with `python icons/generate_icon.py` (requires Pillow and, on macOS, `iconutil`).

## Tests

```bash
cd apps/desktop
source .venv/bin/activate
pytest
```

Install the desktop, backend and reader-service requirements first (see [docs/TESTING.md](../../docs/TESTING.md)). As of 2026-09-13 the suite has 134 tests: 127 pass and 7 Windows-only tests are skipped on macOS. `RACETAG_NO_DIALOGS=1` is set by `tests/conftest.py`, so no test opens a native dialog, and no test imports `webview`.

CI runs the desktop tests with Python 3.12 on every push, on Ubuntu (`desktop-tests`) and on Windows together with `python app.py --selftest` (`desktop-tests-windows`). Neither the Windows job nor the release workflow has run on GitHub with the current changes yet.
