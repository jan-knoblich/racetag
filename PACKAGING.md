# Racetag — Desktop Packaging Plan (macOS + Windows)

_Date: 2026-04-15_
_Prepared by: Agent 1 — The Architect_

Goal: ship **one** double-clickable executable for macOS and **one** for Windows that contains everything Racetag needs — no Docker, no Python install, no separate reader-service process for the end user to launch.

> **Monorepo note (decided 2026-04-15):** All paths in this document reference the new `racetag` monorepo layout. Legacy three-repo paths appear in square brackets `[…]` where useful. The product is named **`Racetag`** (exact case), with version stamped from `apps/desktop/VERSION` and icons delivered by work item `W-M09` (see `PLAN.md`).

---

## 1. What we’re packaging

Three applications, two languages, one network:

| Piece (monorepo path) | Language | What it needs at runtime | Where it belongs in the desktop app |
| --- | --- | --- | --- |
| `apps/reader-service/` | Python 3.11, `requests`, stdlib sockets | Outbound TCP to reader on the LAN | Embedded Python process (**subprocess** inside the app) |
| `apps/backend/` | Python 3.13, FastAPI, uvicorn, pydantic | Local TCP listener (e.g. 127.0.0.1:8600), filesystem writes (SQLite, `synchronous=FULL`) | Embedded Python process (**thread** inside the app) |
| `apps/frontend/` | Static HTML/CSS/JS | An HTTP server + a browser-capable window | Static files shipped as **assets**, rendered by an embedded webview |

Because **all three components are already Python or static web assets**, we do NOT need Tauri, Electron, nativefier, or a Go/Rust wrapper. Those are great when you have Node/React on one side and Rust/Go on the other; they would add hundreds of MB and a second toolchain here for no benefit.

---

## 2. The chosen stack: **pywebview + PyInstaller**

Recommendation:

- **pywebview** to host the frontend in a native window (WKWebView on macOS, WebView2 on Windows).
- **uvicorn** as a threaded server serving both the FastAPI backend **and** the static frontend assets (FastAPI can serve static files via `StaticFiles`).
- **subprocess** for the reader service — started by the main app so the operator only launches one thing.
- **PyInstaller** to produce a one-file exe on Windows and a `.app` bundle on macOS.

Why this over the alternatives:

| Option | Pros | Cons | Verdict |
| --- | --- | --- | --- |
| **pywebview + PyInstaller** (chosen) | Pure Python, uses the OS’s webview (tiny binary, native look), no JS toolchain | macOS notarisation + `.app` bundle needs care | **Pick** |
| Tauri | Very small binaries, excellent perf | Requires a Rust toolchain and a Node build; your app is 100 % Python; no Node project exists | Reject |
| Electron | Huge ecosystem | 100+ MB baseline, new JS build step, overkill | Reject |
| nativefier | Cheap | Ships full Chromium every time, bad for a 3-service app | Reject |
| py2app (mac) + separate Windows tool | Simple on mac | Two different build pipelines; PyInstaller covers both | Reject |
| Docker Desktop bundling | “Ships what already works” | Users need Docker Desktop installed; not a “double-click exe” | Reject |
| Nuitka | Faster runtime, C-compiled | Harder to debug cross-platform signing; no strong win vs PyInstaller for a largely-IO app | Consider if PyInstaller size bothers us later |

Alternative worth prototyping before final pick: **[BeeWare Briefcase](https://beeware.org/project/projects/tools/briefcase/)** which packages Python GUI apps as platform-native bundles with slightly nicer macOS/Windows ceremony out of the box. Keep as a fallback if PyInstaller’s mac signing proves painful.

---

## 3. Final runtime topology (packaged app)

```
┌──────────────────────────────────────────────────────────────────┐
│  Racetag.app  /  Racetag.exe                                      │
│                                                                    │
│  ┌─────────────────────────────┐     ┌──────────────────────────┐ │
│  │  pywebview window           │     │  uvicorn (thread)         │ │
│  │                             │     │                            │ │
│  │  http://127.0.0.1:<port>/   │◄────┤  FastAPI app:             │ │
│  │  (frontend HTML + JS)       │     │   - /events/tag/batch     │ │
│  │                             │     │   - /classification       │ │
│  └─────────────────────────────┘     │   - /riders, /stream      │ │
│                                      │   - StaticFiles("/")      │ │
│  ┌─────────────────────────────┐     └───────────┬───────────────┘ │
│  │  reader-service (subprocess)│                 │                  │
│  │  spawned by app.py          │─────────────────┘ POST             │
│  └─────────────────────────────┘  events over loopback              │
│                                                                    │
│  data/ (sqlite DB, config.json, spool.jsonl)                      │
└──────────────────────────────────────────────────────────────────┘
          │
          └──── LAN: TCP to Sirit/Invelion reader
```

Key design choices:

- **Loopback port** is picked dynamically at startup (bind to port 0, read the actual port). No port conflict issues across re-runs.
- **Reader service as subprocess, not thread.** PyInstaller + Python threads doing long-lived socket work is workable, but subprocesses sandbox socket errors and allow clean restart when the user changes the reader IP in settings. Subprocess entry: `sys.executable -m racetag_reader_service.cli --ip ... --backend-url http://127.0.0.1:<port>`.
- **Data directory**:
  - macOS: `~/Library/Application Support/Racetag/`
  - Windows: `%APPDATA%\Racetag\`
- **Static frontend assets** are copied into the bundle under `apps/desktop/resources/frontend/` and mounted by FastAPI with `app.mount("/", StaticFiles(directory=..., html=True), name="frontend")`. The two placeholder rewrites (`__RACETAG_FRONTEND_API_KEY__`, `__RACETAG_FRONTEND_BACKEND_URL__`) are done at build time by PyInstaller's post-processing hook or at runtime before the window opens.

---

## 4. File layout for the desktop build

A new `apps/desktop/` subtree inside the `racetag` monorepo (peer of `apps/backend/`, `apps/reader-service/`, `apps/frontend/`):

```
racetag/                                  # monorepo root
├── apps/
│   ├── backend/                          # FastAPI app (source)
│   ├── reader-service/                   # reader daemon (source)
│   ├── frontend/                         # static UI (source)
│   └── desktop/                          # ★ packaging lives here
│       ├── app.py                        # main entry point
│       ├── launcher.py                   # port picker + subprocess manager
│       ├── VERSION                       # single-line version string (e.g. "0.1.0")
│       ├── win_version_info.txt          # generated from VERSION (Windows metadata)
│       ├── resources/
│       │   ├── frontend/                 # copy of apps/frontend/ (index.html, script.js, ...)
│       │   └── reader/                   # copy of apps/reader-service/src/ (bundled)
│       ├── icons/
│       │   ├── racetag-source.(svg|png)  # source artwork (delivered by W-M09)
│       │   ├── racetag.icns              # mac  — delivered by W-M09
│       │   └── racetag.ico               # win  — delivered by W-M09
│       ├── pyinstaller.mac.spec
│       ├── pyinstaller.win.spec
│       └── README.md
└── .github/workflows/release.yml         # CI builds both platforms (W-075)
```

The `resources/frontend/` and `resources/reader/` copies are produced at build time from `apps/frontend/` and `apps/reader-service/src/` respectively — they are not checked in. A small `apps/desktop/prepare_bundle.py` (or a Makefile target) handles the copy + placeholder rewrites before PyInstaller runs.

Skeleton of `app.py` (not to be implemented in this planning phase — illustrative only):

```python
# apps/desktop/app.py — skeleton only, to be written by the backend agent (W-070)
import socket, subprocess, sys, threading, uvicorn, webview
from racetag_backend.app import app

def pick_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]

def start_backend(port):
    uvicorn.run(app, host='127.0.0.1', port=port, log_level='warning')

def start_reader(backend_url, reader_ip):
    return subprocess.Popen([sys.executable, '-m', 'racetag_reader_service.cli',
                             '--ip', reader_ip, '--backend-url', backend_url])

if __name__ == '__main__':
    port = pick_port()
    threading.Thread(target=start_backend, args=(port,), daemon=True).start()
    # ...wait for /docs to answer, then
    # reader_ip is read from data_dir/config.json (written by the settings modal)
    # reader_proc = start_reader(f'http://127.0.0.1:{port}', reader_ip)
    webview.create_window('Racetag', f'http://127.0.0.1:{port}/', width=1100, height=700)
    webview.start()
```

**Branding invariants** (applied everywhere):
- Product name: `Racetag` (never `RaceTag`, `racetag`, or `race-tag` in user-visible strings).
- Version: read from `apps/desktop/VERSION`; CI appends `+<shortsha>` for non-tag builds.
- Icon: delivered by `W-M09` — **sourcing option not yet decided**; blocks `W-071` / `W-072`.

---

## 5. PyInstaller spec highlights

### 5.1 macOS

```
# run from monorepo root
pyinstaller \
  --name Racetag \
  --windowed \
  --icon apps/desktop/icons/racetag.icns \
  --add-data 'apps/desktop/resources/frontend:frontend' \
  --add-data 'apps/desktop/resources/reader:reader' \
  --osx-bundle-identifier com.racetag.app \
  apps/desktop/app.py
```

Notes:
- `--windowed` (= `--noconsole`) hides the Terminal window.
- The `.app` lives under `dist/Racetag.app`. Zip it for distribution. Notarisation (Apple-signed) is optional for a prototype; the user explicitly said security is not a priority, so an ad-hoc signature is fine.

### 5.2 Windows

```
REM run from monorepo root
pyinstaller ^
  --name Racetag ^
  --windowed ^
  --onefile ^
  --icon apps\desktop\icons\racetag.ico ^
  --version-file apps\desktop\win_version_info.txt ^
  --add-data "apps\desktop\resources\frontend;frontend" ^
  --add-data "apps\desktop\resources\reader;reader" ^
  apps\desktop\app.py
```

Notes:
- `--onefile` produces `Racetag.exe` ~30 MB. First launch unpacks to a temp dir, so add a splash screen (PyInstaller supports it) if boot is slow.
- On Windows, the reader-service subprocess re-invokes `sys.executable` — which under `--onefile` is the Racetag.exe itself. Use PyInstaller’s `multiprocessing.freeze_support()` pattern and a CLI dispatcher so that `Racetag.exe --reader-service ...` runs the reader module, while `Racetag.exe` (no flags) runs the UI. This avoids shipping a second interpreter.

### 5.3 CI

GitHub Actions matrix with `macos-latest` and `windows-latest`, workflow at `.github/workflows/release.yml` in the monorepo root. See work item `W-075` in `PLAN.md`.

---

## 6. Hardware-access considerations

The reader is reached via TCP (`CONTROL` 50007, `EVENT` 50008). No USB / serial drivers; no permission prompts on either OS. Firewall:
- macOS: the first time uvicorn binds to 127.0.0.1, macOS does **not** prompt. The reader subprocess opening outbound TCP also does not prompt. Good.
- Windows: Defender Firewall may prompt once for outbound TCP from `Racetag.exe`. Document it in `OPERATOR_GUIDE.md`.

No elevated privileges required. The ICS bridge flow for connecting a reader directly to the laptop Ethernet (from the current README) still applies verbatim.

---

## 7. Distribution artefacts

- **macOS:** `Racetag-<version>-mac.zip` containing `Racetag.app`.
- **Windows:** `Racetag-<version>-win.zip` containing `Racetag.exe` **and** a short `README.txt` with ICS bridging steps.

Both are attached automatically to a GitHub Release by `W-075`.

---

## 8. Size / performance budget

Rough expected sizes on first build (unoptimised):

- macOS `.app`: ~80–120 MB (includes Python stdlib + FastAPI + pydantic + uvicorn + pywebview).
- Windows `.exe`: ~30–50 MB (PyInstaller is more aggressive on Windows).

Startup:
- Cold launch target: < 3 s to window visible, < 1 s to backend ready (local loopback, SQLite open).

If size is a problem, switch to `--onedir` on Windows (smaller per-file, directory format) or explore Nuitka.

---

## 9. Risks specific to packaging

| Risk | Detection | Mitigation |
| --- | --- | --- |
| `pywebview` + PyInstaller fails on macOS Apple Silicon | `pyinstaller ... && open dist/Racetag.app` on both arm64 and x86_64 runners | Pin `pywebview>=5` which has ARM-native wheels; build native on each arch. |
| Subprocess `sys.executable` differs under `--onefile` | First smoke test attempts to spawn and fails | Use dispatch flag inside `Racetag.exe` (see 5.2) or ship reader-service as a second tiny exe. |
| Static file path broken in frozen bundle | 404 on `/` in the window | Use `sys._MEIPASS` pattern to resolve `apps/desktop/resources/frontend`. |
| WebView2 not installed on old Windows | pywebview crash on start | Bundle the Evergreen WebView2 bootstrapper installer and invoke it once per user if missing. |
| Reader IP configuration baked into build | Operator changes networks | IP is read from `data_dir/config.json` and editable via the settings modal (W-074). Default is the previous value. |

---

## 10. Ordered work list (mirror of `PLAN.md` Phase 4)

0. **W-M00 … W-M08** — monorepo consolidation (must complete before any packaging code is written).
1. **W-M09** — source / design the Racetag icon set (blocks W-071 and W-072).
2. **W-070** — wire `apps/desktop/app.py` that starts uvicorn + opens pywebview window. Validate on dev machine.
3. **W-071** — mac PyInstaller spec + produce first `.app` (branded "Racetag", versioned, iconed).
4. **W-072** — win PyInstaller spec + produce first `.exe` (branded "Racetag", versioned, iconed).
5. **W-073** — embed the reader service as subprocess; dispatch flag.
6. **W-074** — settings modal so the IP is configurable from the UI.
7. **W-075** — CI pipeline cuts releases.
8. **W-082** — operator guide (docs).

Phase 4 depends on Phases M, 1, 2, 3 being solid; do not ship a desktop build that still multi-counts laps (P0-1), or before the monorepo is the canonical source of truth.

---

## 11. Sources consulted

- [pywebview documentation](https://pywebview.flowrl.com/)
- [zy7y/fastapi-desktop — pywebview + FastAPI reference app](https://github.com/zy7y/fastapi-desktop)
- [r0x0r/pywebview issues on PyInstaller bundling](https://github.com/r0x0r/pywebview/issues/1245)
- [Panel HoloViz — pywebview + PyInstaller how-to](https://panel.holoviz.org/how_to/desktop_or_mobile/pywebview.html)
