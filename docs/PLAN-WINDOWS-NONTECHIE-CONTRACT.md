# Interface contract for PLAN-WINDOWS-NONTECHIE

_Date: 2026-09-13_
_Companion to [PLAN-WINDOWS-NONTECHIE.md](PLAN-WINDOWS-NONTECHIE.md). The plan says **what**; this file fixes the **interfaces** between reader-service, backend, desktop shell, frontend and packaging so they can be built in parallel. When the plan and this contract disagree, this contract wins._

## 0. Deliberate deviations from the plan

1. **Config changes reach the reader-service through the heartbeat reply, not through a controller restart.** `PATCH /config` only persists. The reader-service sees the new `reader_ip` / `antenna_power` in the reply to its next status POST (≤ 2 s) and reconnects by itself. This works identically in the desktop build and in Docker. The desktop supervisor is only used for crash restarts and the explicit "Reader neu verbinden" button.
2. **Discovery runs inside the reader-service only.** The backend never imports reader-service code. `POST /reader/discover` queues a command that is delivered in the heartbeat reply; the reader-service runs discovery and posts the result back. The endpoint waits for the result.
3. **Parent-death detection on Windows** uses both a Job Object (kill on shell exit) and a stdin pipe (`--stop-on-stdin-eof`), which also lets the shell stop the reader-service gracefully on Windows, where `terminate()` is a hard kill and would skip the spool flush.
4. The first-run assistant is shown when `GET /config` returns `assistant_done: false` **and** there are no real races (none, or only the untouched bootstrap race), in the desktop build only. The flag lives in the backend database (meta key `assistant_done`), not in `localStorage`, because the pywebview window runs with `private_mode=True` on a random port and loses browser storage on every launch (amended 2026-09-13). `localStorage["racetag.assistantDone"]` is read only as a fallback in browser mode when `/config` has no `assistant_done` field (older backend).

## 1. Global rules for every builder

- **Language.** Every string an operator can see (UI, tooltips, toasts, confirm dialogs, native message boxes, installer pages) is **German**. Code, identifiers, log lines, API field names and API error `detail` strings stay **English**.
- **Reader-service must stay Python 3.11 compatible** (CI and Docker use 3.11). No PEP 695, no PEP 701 f-string quote reuse, no `itertools.batched`. Keep `from __future__ import annotations`.
- **Stdlib only** for new reader-service and desktop modules (plus already-present deps: `requests`, `fastapi`, `uvicorn`, `pywebview`).
- **Never import `webview` at module level** in any desktop module; tests must not need a GUI.
- **Never `import app` from backend modules** (the desktop shell loads the backend as `racetag_backend_app`).
- **Windows subprocesses** (arp, ipconfig, reader-service child) are started with `creationflags=subprocess.CREATE_NO_WINDOW` on `win32`; otherwise a console window flashes.
- **`sys.stdout` / `sys.stderr` may be `None`** (windowed exe). Guard every `isatty()` and stream handler.
- **Testing.** Test interpreter: `/private/tmp/claude-501/-Users-jan-Documents-git-racetag/76877fbf-bea9-4aff-984f-c5409feae957/scratchpad/venv/bin/python` (Python 3.13, all app requirements installed). Run `-m pytest -q -p no:cacheprovider` from the app directory. No test may sleep for real longer than ~2 s in total; inject clocks and back-off tuples. Every thread a test starts must be stopped in `finally`.
- **Shell caveat.** `git status` and repo-wide `find` are very slow in this environment. Do not run them. No git commits.

## 2. Reader status protocol (reader-service ⇄ backend)

### 2.1 `POST /reader/status` (sent by the reader-service)

Sent every `heartbeat_interval_s` (default 2.0) **and** immediately on every state change. Timeout 1.0 s, no retry, no spool, latest state wins. Header `X-API-Key` when a token is configured.

Request body (all keys always present; `null` when unknown):

```json
{
  "state": "searching | connecting | configuring | active | lost | stopped",
  "ip": "192.168.178.22",
  "target_source": "cli | config | discovery | null",
  "serial": "00179E123456",
  "antennas": [1, 2],
  "antenna_power": 250,
  "antenna_reads": {"1": 17, "2": 9},
  "last_event_at": "2026-09-13T10:00:00.000Z",
  "connected_since": "2026-09-13T09:58:00.000Z",
  "error": "CONTROL connect timeout",
  "consecutive_failures": 0,
  "next_retry_s": null,
  "candidates": [{"ip": "192.168.178.22", "serial": "00179E123456", "source": "arp"}],
  "discovered_ip": null,
  "discovery": null,
  "reader_service_version": "0.2.0",
  "pid": 12345
}
```

Field semantics:

| Field | Meaning |
| --- | --- |
| `state` | `searching`: no usable target, discovery running or waiting. `connecting`: TCP connect attempts to `ip` (also between retries). `configuring`: sockets up, bind/clock/init/antenna config in progress. `active`: bound and configured, events flow. `lost`: was `configuring`/`active`, connection dropped, waiting to reconnect. `stopped`: final POST during shutdown. |
| `ip` | Current target IP (may be `null` in `searching`). |
| `target_source` | Where `ip` came from. |
| `antennas` | Powered ports after antenna configuration; `[]` before. |
| `antenna_reads` | Raw `event.tag.arrive` count per antenna (string keys) **before** presence gating, since the current connection was established. Reset to `{}` on every new connection. |
| `last_event_at` | Server-side UTC time the reader-service last received any `event.tag.*` line. |
| `consecutive_failures` | Failed connect/configure attempts since the last `active`. |
| `next_retry_s` | Seconds until the next attempt while waiting, else `null`. |
| `candidates` | Result of the most recent discovery run (any trigger), `[]` if none ran. |
| `discovered_ip` | Set **only** in the status POSTs after the reader-service switched its target because of discovery (until the backend's reply echoes that IP as `config.reader_ip`). |
| `discovery` | `null`, or `{"request_id": 7, "candidates": [...], "error": null}` exactly once in reply to a `discover` command. |

Response `200`:

```json
{
  "config": {"reader_ip": "192.168.178.22", "antenna_power": 250},
  "command": null
}
```

`command` is `null` or `{"id": 7, "type": "discover" | "reconnect"}`. A command is delivered **once** (backend clears it after putting it into a reply).

### 2.2 Backend rules

1. If `discovered_ip` is a valid IPv4 and differs from the persisted `reader_ip`, **persist it first**, log `reader_ip changed via discovery: old -> new` on logger `racetag.backend`, then build the reply. The reply therefore already echoes the new IP. Each discovery is taken over **once** (amended 2026-09-13): the backend remembers the last taken-over `(pid, discovered_ip)` (also when it equals the saved IP) and ignores heartbeats that repeat it, so a `reader_ip` PATCHed in the meantime is not overwritten and is echoed instead. A heartbeat with `discovered_ip: null`, or one from a different `pid`, ends that discovery.
2. Keep the last status in memory with `received_at` (monotonic, injectable `_monotonic`) and `updated_at` (UTC ISO).
3. Publish SSE `{"type": "reader_status", ...status}` (same shape as §2.3) when `state`, `ip`, `serial`, `antennas`, `error`, `candidates` or `consecutive_failures` changed, **and** at most every 5 s otherwise (so `antenna_reads` / `last_event_at` refresh in the UI).
4. Staleness: if no POST for **6 s**, the effective state becomes `unknown`; publish one `reader_status` with `state: "unknown"` when that transition happens. Implement `_check_reader_stale()` (callable from tests) and run it every 1 s from a daemon thread started in the `startup` hook (never at import) and stopped in `shutdown`.
5. If `discovery.request_id` matches a waiting `POST /reader/discover`, wake it.
6. **SSE cross-thread fix.** `_publish` must deliver to asyncio subscriber queues via `loop.call_soon_threadsafe` using the loop captured in `stream_events` (`asyncio.get_running_loop()`), whichever thread calls `_publish`. The legacy list-subscriber shim used by tests keeps working.

### 2.3 `GET /reader/status`

```json
{
  "state": "active | ... | unknown",
  "...all fields of 2.1 except discovery...": "",
  "updated_at": "2026-09-13T10:00:02.000Z",
  "age_s": 0.8,
  "supervisor": {"running": true, "pid": 12345, "restart_count": 0, "last_exit_code": null, "restart_pending": false}
}
```

Before any heartbeat: `state: "unknown"`, all other status fields `null`/empty, `age_s: null`. `supervisor` is `app.state.reader_controller.status()` when a controller is registered, else `null`.

### 2.4 `POST /reader/discover`

Queues `{"type": "discover"}` (monotonic id), waits up to **15 s** for the matching `discovery` result. Responses (always `200`):

```json
{"candidates": [{"ip": "...", "serial": "...", "source": "arp | sweep | linklocal | connected"}], "error": null}
```

`error` is `null`, `"timeout"` (no result in 15 s) or `"reader_service_unavailable"` (status is `unknown` at request time; returns immediately). Exception (amended 2026-09-13): before the first heartbeat, while a registered controller's `status()` reports `running: true` (the child is still booting), the command is queued and waits for the first heartbeat within the same 15 s budget; without a heartbeat it ends with `"timeout"` and the undelivered command is dropped. The candidate list is also published via the next `reader_status` SSE.

### 2.5 `POST /reader/restart`

`202 {"accepted": true, "via": "supervisor" | "command"}`. With a registered controller call `controller.restart()` (must return immediately). Without one, queue `{"type": "reconnect"}`.

### 2.6 `GET /config` / `PATCH /config`

`Config` gains `desktop: bool` (true iff `app.state.reader_controller` is registered) and `version: str | null` (from env `RACETAG_VERSION`, set by the shell). `PATCH /config` behaviour is unchanged apart from comments/docs: no restart call. `reader_ip: null` in PATCH clears the persisted IP (existing behaviour, keep).

`Config` also gains `assistant_done: bool` (amended 2026-09-13, see §0.4): persisted in the `meta` table under key `assistant_done`, default `false` on a fresh database. `PATCH /config` accepts `assistant_done: bool` and persists it; a non-boolean value (for example `1` or `"true"`) is rejected with `422` (strict boolean), and `null` is ignored like for the other fields except `reader_ip`. Setting it back to `false` makes the assistant show again on the next start while no real race exists. The field is independent of `desktop` and returned in both desktop and Docker mode.

### 2.7 Controller protocol (registered by the desktop shell)

```python
class ReaderController(Protocol):
    def restart(self) -> None: ...        # non-blocking; reads fresh config itself
    def status(self) -> dict: ...         # {"running", "pid", "restart_count", "last_exit_code", "restart_pending"}
```

`restart_pending` (amended 2026-09-13) is `true` from an accepted `restart()` until the replacement child is spawned (additive key, passed through unchanged as `supervisor`); the frontend shows the old child's final `stopped` heartbeat as amber "wird neu gestartet…" while it is set or the child runs.

Registered as `backend_app.state.reader_controller` in the shell right after `_build_combined_app()` and before the server thread starts. Always read with `getattr(app.state, "reader_controller", None)`.

### 2.8 Documentation

Add the four `/reader/*` paths, the `reader_status` SSE type and the new `Config` fields to `apps/backend/openapi.yaml` and `apps/backend/README.md`. DTOs may live inline in `app.py` or in `models_api.py`.

## 3. Reader-service

### 3.1 CLI flags (`racetag_reader_service.py`)

| Flag | Env | Default | Notes |
| --- | --- | --- | --- |
| `--ip` | `READER_IP` | none | **Now optional.** Without it the client starts in `searching`. |
| `--heartbeat-interval` | `HEARTBEAT_INTERVAL_S` | `2.0` | `0` disables the reporter. |
| `--discover-after-failures` | `DISCOVER_AFTER_FAILURES` | `6` | |
| `--discover-interval` | `DISCOVER_INTERVAL_S` | `60` | Minimum seconds between automatic discovery runs. |
| `--no-discover` | `NO_DISCOVER` | off | Disables automatic discovery (commands still work). |
| `--stop-on-stdin-eof` | `STOP_ON_STDIN_EOF` | off | Watch stdin; EOF → `request_stop()`. Mutually exclusive with `--interactive`. |
| `--clock-resync-interval` | `CLOCK_RESYNC_INTERVAL_S` | `1800` | B4. |

Heartbeat URL is `{backend_url}/reader/status`. With `--backend-transport mock` the reporter is a no-op. `main()` returns `0` on a requested stop and no longer returns `1` just because the reader is unreachable.

### 3.2 `SiritClient` behaviour

- `start()` starts the backend client, the status reporter and a **connection thread**, then returns. It raises only for configuration errors (missing backend URL).
- Connection thread loop, until stop:
  1. No target → `searching`, run discovery (unless disabled), pick target (§3.4), else wait `discover_interval_s` (interruptible).
  2. `connecting`: connect CONTROL then EVENT, **connect timeout 3 s**, then TCP keepalive (idle 15 s, interval 5 s, count 8, i.e. 40–55 s; Windows `SIO_KEEPALIVE_VALS` plus best-effort `TCP_MAXRT` = 60 s so Windows' ~20 s retransmission abort does not undercut it; macOS `TCP_KEEPALIVE`; guard every option with `getattr`) (amended 2026-09-13): a short outage must not close the sockets, or the reader's queued passes are lost.
  3. On success reset per-connection state: `session.id=None`, `session.bound=False`, `tags.reset_presence()` (new method clearing `present` only), `reader_serial=None`, query slot cleared, `antenna_reads={}`. **Do not** reset `tags.seen`, `tags.last_emitted_at`, the log throttle or the backend client.
  4. `configuring`: wait for `event.connection id` (timeout 10 s → failure), bind, clock push, init commands, antenna configuration. `_send_control` must report failure (return `False` or raise) so a dead socket fails the configuration instead of marking it bound.
  5. `active`: wait until the connection is lost (recv EOF/error on either socket, keepalive error, a failed send) or a reconnect is requested. Every 15 s send `info.time` as a liveness probe **only when no CONTROL query is pending**; every `clock_resync_interval_s` re-push `info.time_zone=UTC` + `info.time=`. (amended 2026-09-13): a single unanswered probe or resync (5 s reply timeout) is not a loss; the connection is marked lost only after **3 consecutive** unanswered probes/resyncs (reason `no reply to 3 liveness probes in a row (5s each)`), so a silent link is torn down after 35–50 s. EOF, RST, recv errors and failed sends are still an immediate loss. A missed resync is retried after the probe interval. An unexpected exception while handling one received line is logged and skipped; an exception that ends a receive thread marks the connection lost.
  6. Loss → `lost` (if it had been `configuring`/`active`) or stay `connecting`; close both sockets, join old recv threads (timeout 2 s), back-off `self._backoff_s = (1, 2)` (last value repeats; (amended 2026-09-13), was `(1, 2, 5, 10)`) using `_stop_event.wait()`; increment `consecutive_failures`. After `discover_after_failures` failures run discovery at most every `discover_interval_s`; when that run sees the reader at the current target IP, the next connect starts without a back-off.
- A **connection generation** counter tags recv threads and bind work; anything carrying an old generation is ignored. Bind/config is protected by a lock so it never runs twice for one generation. A new `event.connection id` inside the same generation re-binds (W-061 behaviour kept).
- `stop()` is idempotent: standby command, close sockets, stop reporter (sends final `stopped`), stop backend client (spool flush). (amended 2026-09-13): the connection thread also sends the standby command (at most once per client) on every requested stop (`request_stop()` via signal or stdin EOF), not only `stop()`.
- The POSIX parent-liveness check in `run_forever` stays.

### 3.2a `apps/reader-service/src/status_reporter.py`

`class ReaderStatusReporter(url: str, token: Optional[str], interval_s: float = 2.0, on_reply: Optional[Callable[[dict], None]] = None, session_factory=requests.Session)`: `start()`, `update(**fields)` (merge under a lock; wakes the sender immediately when `state` changed), `snapshot() -> dict`, `stop()` (final best-effort POST with `state="stopped"`, join ≤ 2 s). Own daemon thread and own session; 1 s timeout; errors logged at most once per 60 s; never shares the event batch worker. `on_reply` receives the parsed JSON reply on the reporter thread and must not block (hand work to the connection thread).

### 3.3 `apps/reader-service/src/discovery.py`

```python
SIRIT_OUI = "00:17:9e"
FACTORY_IP = "169.254.1.2"

@dataclass(frozen=True)
class Candidate:
    ip: str
    serial: Optional[str]
    source: str            # "arp" | "sweep" | "linklocal" | "connected"

def parse_arp_output(text: str) -> List[Tuple[str, str]]   # [(ip, "00:17:9e:xx:xx:xx")] normalised lower-case colon MACs; handles Windows ("00-17-9e-..", English and German headers) and macOS ("0:17:9e:..") formats
def local_ipv4_networks() -> List[ipaddress.IPv4Network]    # non-loopback interface /24s (cap at /24); Windows: parse `arp -a` "Interface:/Schnittstelle:" lines and `ipconfig` IPv4 lines (English + German); macOS/Linux: `ifconfig`/`ip -4 addr`; plus the UDP-connect trick for the default route; deduplicated
def probe(ip: str, port: int = 50007, timeout_s: float = 0.3, read_timeout_s: float = 1.0) -> Optional[str]
    # TCP connect; send "info.serial_number\r\n"; read until "\r\n\r\n" or timeout; return the hex serial, "" if the reply starts with ok/error but has no serial, None if not a Sirit
def discover(port: int = 50007, networks: Optional[List[ipaddress.IPv4Network]] = None, include_arp: bool = True, extra_ips: Sequence[str] = (FACTORY_IP,), skip_ips: Sequence[str] = (), timeout_s: float = 0.3, max_workers: int = 64) -> List[Candidate]
    # ARP (Sirit OUI) first, then sweep, then extra_ips; probe each IP once; skip_ips are not probed; sorted: arp, sweep, linklocal; serial "" normalised to None
```

Runs `arp`/`ipconfig`/`ifconfig` with a 3 s timeout and `CREATE_NO_WINDOW` on Windows. Never raises; logs and returns `[]` on failure.

### 3.4 Target selection

- A `discover` command while connected passes `skip_ips=[current ip]` and prepends `Candidate(current_ip, current_serial, "connected")`. (amended 2026-09-13): `discover` commands that arrive while a discovery run is in progress are answered with that run's result (`discovery.request_id` = newest queued id); a batch of queued `discover` commands shares one sweep. On macOS/Linux the ARP stage runs `arp -an` (no reverse DNS).
- Automatic selection: if the configured/current IP is among the candidates, keep it. Else if exactly one candidate → switch, `target_source="discovery"`, set `discovered_ip`. Else (0 or ≥ 2) → keep the current target (or stay `searching` without one) and set `error` to `"no reader found"` / `"multiple readers found"`.
- Heartbeat reply handling: `config.reader_ip` valid and different from the current target → switch target (`target_source="config"`), reset failures, reconnect now. `config.antenna_power` different → adopt and reconnect. `null` values are ignored. Once the reply echoes `discovered_ip`, clear `discovered_ip`.

### 3.5 Logging (`utils.py`)

- Console handler only when `sys.stdout` is not `None`; `isatty()` guarded.
- **Always-on** file logging: one shared `RotatingFileHandler` on the parent logger `"reader"` → `resolve_log_dir()/reader.log`, 2 MB × 5, INFO (DEBUG with `RACETAG_DEBUG`). Disable with `RACETAG_FILE_LOG=0`. Child loggers must not add their own file handlers. `--debug` must also raise the level of already-created `reader.*` loggers.

### 3.6 Tests to add

Fake Sirit server helper (`127.0.0.1`, ephemeral CONTROL + EVENT ports, `event.connection id = N\r\n\r\n` on EVENT accept, answers `info.serial_number` → `ok DEADBEEF01`, `antennas.detected` → `ok 1 2`, records commands). Cover: unreachable at start then server appears → `active`; server closes connection → `lost` → reconnect → re-bind with fresh presence; configured IP changed via heartbeat reply → reconnects to the new target; discovery single/multiple/zero candidate selection; `parse_arp_output` on captured Windows (English and German) and macOS samples; `probe` against the fake server; `discover(networks=[127.0.0.0/30], include_arp=False, extra_ips=())`; reporter posts on state change and handles reply commands (mock `requests.Session`); stdin-EOF stop; logger with `sys.stdout=None`.

## 4. Desktop shell (`apps/desktop`)

### 4.1 New modules

| Module | Public API |
| --- | --- |
| `desktop_logging.py` | `setup_logging(log_dir: Path) -> None`: root → `shell.log`; loggers `racetag.backend`, `racetag.snapshots`, `uvicorn`, `uvicorn.error`, `uvicorn.access` → `backend.log`; 2 MB × 5; console handler only if `sys.stdout` exists. `uvicorn_log_config() -> None` (callers pass `log_config=None` to uvicorn so it never touches `isatty`). |
| `native.py` | `message_box(title, text, kind="error"\|"warning"\|"info") -> None` (Windows `MessageBoxW` with `MB_TOPMOST`; macOS `osascript`; else log); `ask_yes_no(title, text) -> bool`; `open_path(path) -> bool` (`os.startfile` / `open` / `xdg-open`); `open_url(url) -> bool`; `install_excepthook(log_dir) -> None` (writes `crash.log`, shows German dialog with the path, also sets `threading.excepthook`); `webview2_version() -> Optional[str]` (Windows registry `{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}` under HKCU and HKLM `WOW6432Node`; returns `None` if missing; returns `"n/a"` on non-Windows); `free_disk_bytes(path) -> int`; `focus_existing_window(title="Racetag") -> bool` (Windows `FindWindowW` + `ShowWindow(SW_RESTORE)` + `SetForegroundWindow`; `False` when the only matching window is hidden, e.g. a closing instance; else `False`). (amended 2026-09-13): `desktop_dir() -> Path` (known folder `FOLDERID_Desktop`, then registry `User Shell Folders`, then `~/Desktop`, `USERPROFILE`, home; never empty, never raises) and `keep_system_awake(enabled) -> bool` (Windows `SetThreadExecutionState(ES_CONTINUOUS\|ES_SYSTEM_REQUIRED\|ES_DISPLAY_REQUIRED)`; macOS `caffeinate -d -i -w <pid>`; else no-op; never raises). Use explicit `argtypes`/`restype` for every ctypes call. |
| `reader_supervisor.py` | `class ReaderSupervisor` implementing §2.7: `__init__(argv_builder: Callable[[], Tuple[List[str], Dict[str, str]]], pid_file: Path, backoff_s=(1, 2, 5, 10, 30), on_event: Optional[Callable[[str, dict], None]] = None)`, `start()`, `stop(timeout_s=5.0)`, `restart()`, `status()`, `is_running()`. Spawns with `stdin=PIPE`; stop = close stdin → wait → `terminate()` → `kill()`. Watcher thread restarts on unexpected exit with back-off; back-off resets after 60 s of uptime. Windows: Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, `CREATE_NO_WINDOW`. Writes/removes the PID file. |
| `support_bundle.py` | `build_support_bundle(dest_zip: Path, data_dir: Path, log_dir: Path, extra_json: Dict[str, object]) -> Path`: zips `logs/*`, a `VACUUM INTO` copy of `data_dir/racetag.db` (skipped with a note if missing), and each `extra_json` entry as `<name>.json`. `default_bundle_name(now: datetime) -> str` → `Racetag-Support-YYYYMMDD-HHMM.zip`. |
| `selftest.py` | `run_selftest() -> int` (see §4.3). |

### 4.2 `app.py` changes

- `main()` order: `--reader-service` dispatch → `--selftest` dispatch → `_bootstrap_env()` (also sets `RACETAG_VERSION` from the bundled `VERSION` file) → `setup_logging` → `install_excepthook` → single-instance lock (fix: `seek(0)` before `msvcrt.locking`; on failure `focus_existing_window()` then German message box, exit 1) → data dir writable + ≥ 200 MB free check (German dialog, exit 1) → Windows only: `webview2_version()` is `None` → German dialog offering to open `https://go.microsoft.com/fwlink/p/?LinkId=2124703`, exit 1 → stale reader reap → build app → register supervisor as `reader_controller` → server thread (`log_config=None`) → wait ready (German dialog on timeout) → `supervisor.start()` → webview window (`gui="edgechromium"` on Windows) → `finally`: supervisor stop, server stop.
- Amendments (amended 2026-09-13):
  - Lock failure: `focus_existing_window()`; on Windows without a visible window retry the lock every 0.25 s for up to 20 s (a previous instance may still be shutting down), then start normally, or show "Racetag läuft bereits" / "Racetag wird gerade noch beendet oder gestartet".
  - `create_window(..., confirm_close=True)` and `webview.start(..., localization=<German strings>)`: X / Alt+F4 asks "Racetag wirklich beenden? Solange Racetag geschlossen ist, werden keine Durchfahrten erfasst."
  - `native.keep_system_awake(True)` right before `webview.start()`, `False` at the very end of `finally`.
  - `_bootstrap_env()` and the reader child env add `127.0.0.1,localhost` to `NO_PROXY`; the shell's own local HTTP calls use a proxy-free `urllib` opener.
  - The log-dir fallback is exported as `RACETAG_LOG_DIR` so the child follows it. The `--reader-service` role sets `RACETAG_NO_DIALOGS=1` (a crashing child never shows a dialog).
  - `save_csv` returns `True`/`False` (`False` = cancelled); on a write error it shows a German dialog and raises, so the JS promise rejects. The support-bundle save dialog opens in `native.desktop_dir()`.
- Reader argv builder reads config **in-process** from the loaded backend module (`racetag_backend_app._effective_config()`), never over HTTP. It passes `--ip` only when a reader IP is known, always passes `--stop-on-stdin-eof`, keeps the other existing flags, uses `os.pathsep` for `PYTHONPATH`.
- Backend watchdog (B5): a daemon thread checks the server thread every 5 s; if it died unexpectedly, `ask_yes_no` "Racetag neu starten?" → re-exec `sys.executable` with the same argv, else exit.
- `_RacetagApi` moves to module level (testable), gains:
  - `save_csv(csv_text, default_filename)` (existing, switch to `webview.FileDialog.SAVE`)
  - `open_data_folder() -> bool` (opens `~/.racetag`)
  - `create_support_bundle() -> {"ok": bool, "path": str | None, "error": str | None}` (native save dialog, default name from `default_bundle_name`, extra JSON: `config`, `reader_status`, `app_info`)
  - `app_info() -> {"version", "data_dir", "log_dir", "platform"}`
- Keep test-facing names working or update the tests deliberately: `_try_lock_file`, `_READER_PID_FILE`, `_kill_stale_reader_service`, `_reader_service_entry`, `_pick_free_port`, `_bootstrap_env`, `_build_combined_app`. POSIX-only tests get `skipif(sys.platform == "win32")`; `test_bootstrap_env_uses_default_when_unset` must also set `USERPROFILE`.
- `requirements.txt`: `pywebview>=6.0,<7`.

### 4.3 Self-test (`Racetag.exe --selftest`, `python app.py --selftest`)

Exit `0` only if all steps pass; each step logged to `<RACETAG_LOG_DIR or temp>/selftest.log` and printed when stdout exists:

1. Temp data and log dirs (never touches `~/.racetag`), no instance lock.
2. Build the combined app, start uvicorn on a free port (`log_config=None`).
3. `GET /config` (has `desktop` and `version`), `GET /races`, `GET /` (HTML), `GET /strings.js`, `GET /tooltips.js` → all 200.
4. Start the supervisor with a reader argv pointing at `--ip 127.0.0.1 --control-port <closed port> --event-port <closed port> --no-discover --heartbeat-interval 0.5`.
5. Within 15 s `GET /reader/status` reports a state other than `unknown` (proves the frozen reader-service boots, imports, logs and heartbeats).
6. Stop supervisor and server. Total runtime < 30 s.

## 5. Frontend (`apps/frontend`)

- New globals only via `window.RT` (in `strings.js`: `RT.S` string table, `RT.fmt(key, vars)`, `RT.apiError(status, bodyText, fallbackKey)` → German message from status code / known English detail substrings) and `window.RT_TIPS` + `RT.applyTips(root)` (in `tooltips.js`). Load order: `strings.js`, `api.js`, `script.js`, `tooltips.js`. Never redeclare existing top-level names.
- `index.html` text translated in place, `lang="de"`. Dynamic strings come from `RT.S`.
- **Status bar (D1)** replaces `#status` with three pills: Reader (from `reader_status` SSE + initial `GET /reader/status`), Antennen (`antennas` + `antenna_reads` deltas + `/diagnostics/antennas` poll every 10 s), Verbindung (SSE state). Colours: green active, amber connecting/lost/searching/configuring, red unknown/stopped or SSE down. Hover shows details and the recovery text. (amended 2026-09-13): `stopped` with `supervisor.restart_pending` or `supervisor.running` true is amber "wird neu gestartet…"; `unknown` before the first heartbeat (`updated_at` null) with `supervisor.running` true is amber "startet…" for up to 30 s after page load. Antennen is amber "1 OK, 2 liest nichts" while the race runs and a powered port has 0 reads in 60 s while the other ports have ≥ 5; the frontend also renders "… nicht erkannt" from an optional `antennas_detected` status field, which no component sends yet. The fetch-SSE client has a 40 s no-data watchdog (keepalive comments count as data). Reader restart, applying a discovered IP, and saving a changed `reader_ip`/`antenna_power` ask for confirmation while a race runs with the reader active.
- **Desktop mode (D4)**: when `GET /config` returns `desktop: true`, hide Backend URL + Connect, always use same origin, move "Tag anzeigen" into an "Erweitert" section.
- **Settings (C4, D6)**: "Reader suchen" (POST `/reader/discover`, list candidates with IP + serial + source, one click writes the IP via `PATCH /config`), "Reader neu verbinden" (POST `/reader/restart`), remove every "applies on next app restart" text, show version. Desktop only: "Datenordner öffnen", "Support-Paket erstellen" via `window.pywebview.api`.
- **First-run assistant (D5)**: 3 steps (Reader finden → Antennentest using `antenna_reads` from `reader_status` → Erstes Rennen anlegen via existing create-race endpoint). Skippable. Shown only when `/config.assistant_done` is `false` (§0.4); finishing or skipping sends `PATCH /config {"assistant_done": true}`. If `/config` has no `assistant_done` field (browser mode against an older backend), fall back to `localStorage["racetag.assistantDone"]`. The re-open button in Settings opens it regardless of the flag. All `localStorage` access in try/catch. (amended 2026-09-13): the assistant does not auto-open when the bootstrap race already has riders; step 3 offers to keep a not-started active race that has riders (`PATCH /races/{id}` name + `total_laps`) instead of POST + activate, asks before activating a new race while one is running, and ends with a toast and a pulsing "Rennen starten" button.
- **Update notice (D7)**: once per start, `fetch("https://api.github.com/repos/jan-knoblich/racetag/releases/latest")` with 3 s timeout; if `tag_name` (without `v`) is newer than `version`, show a small link; ignore every error.
- **Tooltips (D2)**: every button, settings field (plus ⓘ with longer text and default), table header, race selector, Koppel-Modus control, status pill. Single `position: fixed` bubble, delegated `mouseover`/`focusin`, `title` kept as fallback. Dynamically rendered rows get tips in their templates.
- Check every JS file with `node --check`.

## 6. Packaging and CI

- Specs: hiddenimports add `desktop_logging`, `native`, `reader_supervisor`, `support_bundle`, `selftest`, `discovery`, `status_reporter` (both specs, keep in sync); datas add `apps/desktop/VERSION` → `.`; Windows spec `upx=False` in `EXE` and `COLLECT`.
- `apps/frontend/Dockerfile`: copy `strings.js` and `tooltips.js`.
- `apps/desktop/installer/racetag.iss` (Inno Setup 6): per-user (`PrivilegesRequired=lowest`), `{localappdata}\Programs\Racetag`, German language only (`compiler:Languages\German.isl`), desktop icon task checked, autostart task unchecked (`HKCU\Software\Microsoft\Windows\CurrentVersion\Run`), WebView2 check + silent bootstrapper install from `installer\MicrosoftEdgeWebview2Setup.exe` when the registry key is missing, launch at end, welcome text explaining the SmartScreen "Weitere Informationen → Trotzdem ausführen" step. Version via `/DMyAppVersion=`. The bootstrapper is downloaded in CI and gitignored.
- `release.yml`: `workflow_dispatch` trigger; Windows: build → self-test the built exe (`Start-Process -Wait -PassThru` exit code) → download bootstrapper → `iscc` → upload `Racetag-Setup-<version>.exe` + zip; drop `+sha` from tagged builds (keep it for dispatch builds).
- `ci.yml`: add `desktop-tests-windows` (windows-latest, Python 3.12, desktop + backend + reader-service requirements, `pytest`, then `python app.py --selftest`).
- Release gates (amended 2026-09-13):
  1. A tag build fails when the tag without its `-rc`/`-beta` suffix differs from `v<VERSION>`, checked in both the build job (before anything is installed) and the release job (before publishing).
  2. All pip installs run in one call under `shell: bash`, so a failed install fails the step on Windows too.
  3. The Windows job imports the GUI stack (`clr`, `webview.platforms.winforms`, `webview.platforms.edgechromium`) before the build and checks after the build that every DLL shipped by pythonnet, clr_loader and pywebview `webview/lib` is present in `dist/Racetag`; `ci.yml` `desktop-tests-windows` runs the same import check.
  4. The build fails on `ERROR: Hidden import` log lines (PyInstaller itself exits 0).
  5. Both specs also list `reader_status_hub`; the Windows spec adds `webview.platforms.winforms`, `clr`, `pythonnet`, `clr_loader`; the non-modules `racetag_backend_app` and `multiprocessing.freeze_support` are gone. `RACETAG_NO_DIALOGS=1` is set for the CI and release self-tests.
