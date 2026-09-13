# Testing guide

Each app has its own virtual environment and dependency set; there is no shared root environment. Run `pytest` from the app's directory, not from the monorepo root.

## Current suites

Counts as of 2026-09-13 (branch `feature/windows-nontechie`, local run on macOS with Python 3.13; the reader-service suite was also run on Python 3.11.15 with the same result):

| App | Directory | Tests | Result |
| --- | --- | --- | --- |
| reader-service | `apps/reader-service/tests/` | 148 | 148 passed |
| backend | `apps/backend/tests/` | 242 | 242 passed |
| desktop | `apps/desktop/tests/` | 134 | 127 passed, 7 skipped (Windows-only tests) |
| frontend | `apps/frontend/*.js` | 4 files | `node --check` OK |
| desktop self-test | `python app.py --selftest` | 4 steps | exit 0 in about 1 s |

The Windows-only desktop tests (real Job Object, WebView2 registry lookup, `user32` prototypes, `FindWindowW`, stopping a child via stdin on Windows, known-folder Desktop lookup, real `SetThreadExecutionState`) are skipped on macOS and Linux and run in the `desktop-tests-windows` CI job. None of the suites has been run on a real Windows PC outside CI, and nothing runs against the real Sirit reader; see [PLAN-WINDOWS-NONTECHIE.md §13](PLAN-WINDOWS-NONTECHIE.md#13-implementation-status-2026-09-13).

What the new suites cover, briefly:

- **reader-service**: `tests/fake_sirit.py` is an in-process fake reader (CONTROL + EVENT on ephemeral `127.0.0.1` ports, `event.connection id`, `info.serial_number` → `ok DEADBEEF01`, `antennas.detected` → `ok 1 2`). `test_connection_loop.py` (unreachable at start, reconnect after close, config change via heartbeat reply, discovery target selection), `test_discovery.py` (ARP/`ipconfig`/`ifconfig`/`ip` parsing in English and German, probe, sweep of `127.0.0.0/30`), `test_status_reporter.py`, `test_main_cli.py` (including a real subprocess stopped by closing stdin), `test_logging.py` (`sys.stdout = None`, no ANSI codes in the file).
- **backend**: `test_reader_status.py` (validation, SSE publish rules, 6 s staleness, `discovered_ip` persistence and log line, command queue delivered once, `/reader/discover` timeout/success, `/reader/restart` via supervisor or command, cross-thread SSE delivery).
- **desktop**: `test_reader_supervisor.py` (restart with back-off, requested stop, graceful stdin stop), `test_native.py`, `test_desktop_logging.py`, `test_support_bundle.py`, `test_selftest.py`, `test_process_lifecycle.py`, `test_app.py` (startup order and early exits, watchdog, argv builder, both PyInstaller specs list the required hidden imports and the Windows spec has `upx=False`).

`tests/conftest.py` sets `RACETAG_FILE_LOG=0` (reader-service) and `RACETAG_NO_DIALOGS=1` (desktop), so tests neither write into `~/.racetag` nor open native dialogs.

---

## Running tests locally

### reader-service (Python 3.11)

```bash
cd apps/reader-service
python3.11 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt
pytest
```

The reader-service must stay Python 3.11 compatible (CI and the Docker image use 3.11). `pyproject.toml` puts `src/` on `sys.path`.

### backend (Python 3.13)

```bash
cd apps/backend
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
pytest
```

`pyproject.toml` puts `racetag-backend/` on `sys.path`, so `import app`, `import domain.race` etc. work without install.

### desktop (Python 3.12)

The desktop tests import the backend and parts of the reader-service; the self-test also runs the reader-service with its HTTP heartbeat (`requests`). Install all three requirement sets:

```bash
cd apps/desktop
python3.12 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt \
  -r ../backend/requirements.txt -r ../reader-service/requirements.txt
pytest
```

No test imports `webview`, so no GUI is needed.

### Headless self-test

```bash
cd apps/desktop
python app.py --selftest                    # from source
```

On Windows, for the frozen build (the exe has no console, so read the exit code):

```powershell
$env:RACETAG_LOG_DIR = "$env:TEMP\rt-selftest"; $env:RACETAG_NO_DIALOGS = "1"
(Start-Process dist\Racetag\Racetag.exe -ArgumentList '--selftest' -Wait -PassThru).ExitCode
```

The self-test uses temporary data and log directories (never `~/.racetag`) and no instance lock. It builds the combined app, starts uvicorn on a free port, checks `GET /config` (with `desktop: true` and `version`), `/races`, `/`, `/strings.js` and `/tooltips.js`, starts the reader-service through the supervisor against closed ports with `--no-discover --heartbeat-interval 0.5`, waits up to 15 s for a heartbeat state other than `unknown`, then stops everything. Exit code `0` only if every step passed. Step results go to `selftest.log` in `RACETAG_LOG_DIR` (or a `racetag-selftest-*` temp directory), with log tails appended on failure. `RACETAG_NO_DIALOGS=1` makes a crash before the self-test starts write `crash.log` and exit instead of waiting on a message box.

### Frontend

There is no JavaScript test runner. Syntax-check every file:

```bash
cd apps/frontend
for f in strings.js api.js script.js tooltips.js; do node --check "$f"; done
```

`apps/desktop/tests/test_app.py` checks that the backend serves the frontend (`GET /` returns HTML). The German-text scan and tooltip coverage checks used during development were throwaway scripts and are not part of the repo.

---

## Adding a new test

1. Drop a file named `test_<something>.py` inside `apps/<app>/tests/`.
2. Name every test function `test_<description>`.
3. Use only packages already in `requirements.txt` / `requirements-dev.txt`. A new test-only package goes into `requirements-dev.txt`.
4. Inject clocks and back-off tuples instead of sleeping; keep real sleeping to about 2 s per test file.
5. Stop every thread and close every socket a test starts, in `finally` or a fixture.
6. Windows-only tests: `@pytest.mark.skipif(sys.platform != "win32", reason=...)`; POSIX-only tests (e.g. `fcntl`, signals): `skipif(sys.platform == "win32")`.

---

## CI

### `.github/workflows/ci.yml` (every push and pull request)

| Job | Runner | Python | Installs | Runs |
|-----|--------|--------|----------|------|
| `reader-service-tests` | ubuntu-latest | 3.11 | reader-service requirements + dev | `pytest` in `apps/reader-service` |
| `backend-tests` | ubuntu-latest | 3.13 | backend requirements + dev | `pytest` in `apps/backend` |
| `desktop-tests` | ubuntu-latest | 3.12 | desktop requirements + dev, backend requirements | `pytest` in `apps/desktop` |
| `desktop-tests-windows` | windows-latest | 3.12 | desktop + dev, backend and reader-service requirements | `pytest` in `apps/desktop`, then `python app.py --selftest` (3 min timeout, `RACETAG_NO_DIALOGS=1`, prints every file in `RACETAG_LOG_DIR` on failure) |

The jobs are independent; all run on every push.

### `.github/workflows/release.yml` (tag `v*.*.*` or manual `workflow_dispatch`)

- Builds with PyInstaller on `macos-latest` and `windows-latest`.
- **Windows:** runs `Racetag.exe --selftest` on the frozen build via `Start-Process -Wait -PassThru` (3 min timeout, `RACETAG_NO_DIALOGS=1`) and fails on a non-zero or missing exit code, printing all log files either way. Then downloads the WebView2 bootstrapper (Authenticode signature checked), builds `Racetag-Setup-<version>.exe` with Inno Setup and uploads it together with the zip.
- Tag builds are named `Racetag-<version>-{mac,win}.zip` and `Racetag-Setup-<version>.exe` and published as a GitHub Release. Manual runs add `+<sha>` to the names and only keep the files as workflow artefacts.

**Neither workflow has run on GitHub with these changes yet.** Both were checked with `actionlint` and `yaml.safe_load` only.

---

## Troubleshooting

**`ModuleNotFoundError` when running pytest locally**
Activate the virtualenv of the right app and install that app's `requirements.txt` + `requirements-dev.txt`. The desktop app additionally needs the backend and reader-service requirements.

**`ModuleNotFoundError` for the app's own source code**
Run `pytest` from the app directory, where `pyproject.toml` adds `src/` (reader-service), `racetag-backend/` (backend) or `.` (desktop) to `sys.path`.

**Wrong Python version**
Create the virtualenv with `python3.11` / `python3.13` / `python3.12` explicitly; `python3 -m venv` picks the system default.

**Desktop tests fail with import errors from the backend or reader-service**
The desktop tests load the backend from `apps/backend/racetag-backend` and the reader-service from `apps/reader-service/src`. A broken or half-edited file there shows up as a desktop failure.

**Self-test fails or hangs on Windows**
Set `RACETAG_LOG_DIR` and read `selftest.log`, `shell.log`, `reader.log` and `reader-stderr.log` there. A missing hidden import in the frozen build shows up in `crash.log` or `reader-stderr.log`.

**CI fails but local passes**
Check that every import used in tests is listed in the requirements files CI installs (see the table above).

**Docker backend image**
Independent of the tests: the backend `Dockerfile` regenerates `models_api.py` from `openapi.yaml`, which is missing several DTOs (e.g. `RaceSummaryDTO`), so the backend image fails to start. This predates the Windows work and is not covered by CI.
