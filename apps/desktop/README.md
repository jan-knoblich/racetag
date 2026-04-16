# apps/desktop — Racetag desktop bundle

Packages the backend (`apps/backend/`), frontend (`apps/frontend/`), and reader-service (`apps/reader-service/`) into a single double-clickable binary per OS using **pywebview** + **PyInstaller**.

## Status

Scaffolding only. Implementation is tracked in [`../../PLAN.md`](../../PLAN.md):

- `W-070` — pywebview shell (`app.py`) that spawns the backend + reader-service as subprocesses and opens a native window on the served frontend.
- `W-071` — PyInstaller macOS spec (`pyinstaller.mac.spec`) producing `Racetag.app`.
- `W-072` — PyInstaller Windows spec (`pyinstaller.win.spec`) producing `Racetag.exe`.
- `W-080` — GitHub Actions release workflow (`.github/workflows/release.yml`).

See [`../../PACKAGING.md`](../../PACKAGING.md) for the rationale behind pywebview+PyInstaller (vs Tauri, Electron, nativefier).

## Branding invariants

- **Product name:** `Racetag`
- **Bundle identifier:** `com.jan-knoblich.racetag`
- **Version:** single source of truth is [`VERSION`](VERSION). Read at build time into `CFBundleShortVersionString` / `CFBundleVersion` (macOS) and the Windows `VERSIONINFO` resource (via `win_version_info.txt`).
- **Icon set:** [`icons/racetag.icns`](icons/) (macOS), [`icons/racetag.ico`](icons/) (Windows). Current artefacts are placeholder — regenerate by running `python icons/generate_icon.py`, or replace `icons/racetag-source.png` with a designed 1024×1024 master and re-run.

## Icon regeneration

```bash
cd apps/desktop/icons
python3 generate_icon.py
```

Requires Pillow and, on macOS, `iconutil` (Xcode command-line tools).

## Files to come

```
apps/desktop/
├── VERSION                     # present
├── README.md                   # present
├── icons/                      # present (placeholder artwork)
│   ├── generate_icon.py
│   ├── racetag-source.png
│   ├── racetag.icns
│   └── racetag.ico
├── app.py                      # W-070
├── pyinstaller.mac.spec        # W-071
├── pyinstaller.win.spec        # W-072
├── win_version_info.txt        # W-072 (generated from VERSION)
└── requirements.txt            # W-070
```
