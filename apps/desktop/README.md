2# apps/desktop — Racetag desktop bundle

Packages the backend (`apps/backend/`), frontend (`apps/frontend/`), and reader-service (`apps/reader-service/`) into a single double-clickable binary per OS using **pywebview** + **PyInstaller**.

## What it does

`app.py` is the main entry point. On launch it:

1. Sets `RACETAG_DATA_DIR` to `~/.racetag/data/` (created if absent).
2. Picks a free loopback port (bind-to-zero — no hard-coded port conflicts).
3. Imports and starts the FastAPI backend on a daemon thread (via uvicorn). The backend also mounts the frontend static files at `/`.
4. Waits for the backend to signal it is ready (up to 10 s).
5. Spawns the reader-service as a subprocess (`--reader-service` dispatch flag in frozen mode; direct script invocation in source mode).
6. Opens a native window via pywebview pointing at `http://127.0.0.1:<port>/`.
7. On window close, terminates the reader-service subprocess and signals uvicorn to shut down.


## Current files

```
apps/desktop/
├── app.py                      # main entry point (W-070, W-073)
├── pyinstaller.mac.spec        # macOS build spec (W-071)
├── pyinstaller.win.spec        # Windows build spec (W-072)
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
# Install PyInstaller (do this inside a venv or globally)
pip install pyinstaller

# macOS
cd apps/desktop
pyinstaller pyinstaller.mac.spec --clean
# Produces: dist/Racetag.app

# Windows
cd apps/desktop
pyinstaller pyinstaller.win.spec --clean
# Produces: dist/Racetag.exe
```

The specs bundle the backend source, frontend static files, and reader-service source into the binary. The `prepare_bundle.py` step (invoked by the spec) copies `apps/frontend/` and `apps/reader-service/src/` into the bundle and rewrites the frontend's placeholder variables.

## Release

Push a version tag and CI does the rest:

```bash
git tag v0.1.0
git push origin v0.1.0
```

The workflow at `.github/workflows/release.yml` runs on `macos-latest` and `windows-latest`, builds both binaries, and attaches `Racetag-<version>-mac.zip` and `Racetag-<version>-win.zip` to the GitHub Release. The version string is read from `apps/desktop/VERSION`.

## Environment variables (desktop mode)

In the packaged app, the backend and reader-service read their config from env vars set by `app.py`. The most important ones an operator can override:

| Variable | Default | Purpose |
| --- | --- | --- |
| `RACETAG_DATA_DIR` | `~/.racetag/data/` | SQLite DB and spool directory |
| `READER_IP` | `192.168.1.130` | Reader IP (also configurable in the Settings modal) |
| `MIN_LAP_INTERVAL_S` | `10` | Lap cooldown passed to the reader-service subprocess |
| `RACETAG_BUNDLED_READER` | `1` | Set to `0` to skip spawning the reader subprocess (useful when running a separate reader during development) |

## Branding invariants

- **Product name:** `Racetag` (not `RaceTag`, `racetag`, or `race-tag` in user-visible strings)
- **Bundle identifier:** `com.jan-knoblich.racetag`
- **Version:** single source of truth is [`VERSION`](VERSION)
- **Icon:** `icons/racetag.icns` (macOS) and `icons/racetag.ico` (Windows). Regenerate with `python icons/generate_icon.py` (requires Pillow and, on macOS, `iconutil`).

## Tests

```bash
cd apps/desktop
source .venv/bin/activate
pytest
```

CI runs 9 desktop tests with Python 3.13 on every push.
