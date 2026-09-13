# apps/reader-service — Sirit INfinity 510 TCP client

Connects to the Sirit INfinity 510 RFID reader over two TCP sockets (CONTROL on port 50007, EVENT on port 50008), applies init commands, tracks per-antenna tag presence to suppress multi-antenna double-counts, and forwards accepted tag arrive events to the backend via HTTP.

## Role in the monorepo

The reader-service is the edge component. It runs on a machine that has network access to the physical reader. It is the **only** service that needs host networking; all others use Docker's bridge network.

## Run it

### Docker Compose (recommended)

Run from the **monorepo root**:

```bash
# Copy and edit the reader env file
cp apps/reader-service/.env.example apps/reader-service/.env
# Set BACKEND_URL (and READER_IP unless the reader should be discovered) in apps/reader-service/.env

docker compose up --build racetag-reader-service
```

The reader-service container uses `network_mode: host` so it can reach the Sirit reader on the LAN.

### Native (development)

```bash
cd apps/reader-service
python3.11 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Minimal invocation
python src/racetag_reader_service.py --ip 192.168.1.130 --backend-url http://localhost:8600

# Without an IP: the reader is searched on the local network
python src/racetag_reader_service.py --backend-url http://localhost:8600

# With debug logging (console and ~/.racetag/logs/reader.log)
python src/racetag_reader_service.py --ip 192.168.1.130 --backend-url http://localhost:8600 --debug

# Enable a reader-side min-lap cooldown (default 0 = backend decides)
python src/racetag_reader_service.py --ip 192.168.1.130 --backend-url http://localhost:8600 \
  --min-lap-interval 8

# Use mock transport (no backend needed — useful for hardware-only testing)
python src/racetag_reader_service.py --ip 192.168.1.130 --backend-transport mock
```

Press `Ctrl-C` to stop (or close stdin when started with `--stop-on-stdin-eof`). The service sets the reader to standby on exit, on every stop path (signal, stdin EOF, direct `stop()`).

## CLI flags and environment variables

| Flag | Env var | Default | Purpose |
| --- | --- | --- | --- |
| `--ip` | `READER_IP` | _(none)_ | IPv4 address of the Sirit reader. Optional: without it the service starts in `searching` and finds the reader on the network |
| `--control-port` | `CONTROL_PORT` | `50007` | Reader CONTROL socket port |
| `--event-port` | `EVENT_PORT` | `50008` | Reader EVENT socket port |
| `--backend-url` | `BACKEND_URL` | _(required for http transport)_ | Backend base URL (events and heartbeat) |
| `--backend-transport` | `BACKEND_TRANSPORT` | `http` | `http` or `mock` (mock: no backend, heartbeat disabled) |
| `--backend-token` | `BACKEND_TOKEN` | _(none)_ | `X-API-Key` value sent to backend |
| `--min-lap-interval` | `MIN_LAP_INTERVAL_S` | `0` | Reader-side seconds between two forwarded arrives for the same tag; `0` = presence union only, the backend owns the lap cooldown |
| `--antenna-power` | `ANTENNA_POWER` | `300` | Conducted power in 0.1 dBm for connected ports, applied on every connect |
| `--antenna-ports` | `ANTENNA_PORTS` | `1 2` | Fallback ports; always powered, auto-detection can only add ports |
| `--init_commands_file` | `INIT_COMMANDS_FILE` | `init_commands` | Path to the reader init commands file |
| `--heartbeat-interval` | `HEARTBEAT_INTERVAL_S` | `2.0` | Seconds between `POST {backend}/reader/status`; `0` disables |
| `--discover-after-failures` | `DISCOVER_AFTER_FAILURES` | `6` | Consecutive connect failures before the network is searched |
| `--discover-interval` | `DISCOVER_INTERVAL_S` | `60` | Minimum seconds between two automatic discovery runs |
| `--no-discover` | `NO_DISCOVER` | `false` | Disable automatic discovery (backend `discover` commands still work) |
| `--stop-on-stdin-eof` | `STOP_ON_STDIN_EOF` | `false` | Stop gracefully when stdin is closed (desktop shell); not with `--interactive` |
| `--clock-resync-interval` | `CLOCK_RESYNC_INTERVAL_S` | `1800` | Seconds between re-pushing the host UTC clock while connected; `0` disables |
| `--debug` | `RACETAG_DEBUG` | `false` | DEBUG level for console and `reader.log` |
| `--interactive` | `INTERACTIVE` | `false` | Allow typing CONTROL commands on stdin |
| `--raw` | `RAW` | `false` | Print raw socket data |
| `--no-color` | `NO_COLOR` | `false` | Disable ANSI colours in console output |

Other environment variables: `RACETAG_LOG_DIR` (log and spool directory, default `~/.racetag/logs`), `RACETAG_FILE_LOG=0` (disable `reader.log`), `RACETAG_VERSION` (version reported in the heartbeat; set by the desktop shell).

Exit codes: `0` after a requested stop (signal, stdin EOF, parent gone), `1` for a configuration error (e.g. http transport without backend URL), `2` for invalid arguments. An unreachable reader is **not** an error: the service keeps retrying.

## Connection lifecycle

`SiritClient.start()` returns immediately; a connection thread owns the reader sockets:

| State | Meaning |
| --- | --- |
| `searching` | No target IP. Discovery runs (at most every `--discover-interval`), a single result becomes the target |
| `connecting` | TCP connect to CONTROL then EVENT (3 s timeout each), also while waiting between failed attempts |
| `configuring` | Sockets up: waiting for `event.connection id` (10 s), then bind, UTC clock push, init commands, antenna detection |
| `active` | Bound and configured, events flow |
| `lost` | The connection dropped after `configuring`/`active`; waiting to reconnect |
| `stopped` | Final state sent during shutdown |

- **Reconnect**: EOF or a socket error on either channel, a failed send, a failed configuration, or 3 unanswered liveness probes in a row (`info.time` every 15 s with a 5 s reply timeout, only when no other CONTROL query is running) close both sockets and reconnect after a back-off of 1, 2, 2… s. When an automatic discovery run sees the reader at its target IP, the next attempt starts at once. A single unanswered probe does not count as a loss: during a short outage (cable wobble, switch port renegotiating) the reader keeps the passes it emits in TCP and delivers them once the link is back, which only works while the sockets stay open. So a silent link is torn down 35-50 s after it went quiet; a rebooted reader is still noticed as soon as it answers a probe with a reset. TCP keepalive (idle 15 s, interval 5 s, 8 probes; on Windows also a 60 s retransmission limit via `TCP_MAXRT`) turns a dead peer into a socket error after 40-55 s.
- **Robust event parsing**: a reader line that cannot be processed is logged and skipped; non-integer `antenna`/`rssi` values drop only that field, never the pass. If a receive thread still ends unexpectedly, the connection is treated as lost and reconnected.
- **Per connection** the session id, bind flag, reader serial, antenna list, read counters and tag presence are reset, so a tag that was in the field during an outage is counted again. Seen-tag history, the reader-side cooldown timestamps and the backend client (with its spool) survive reconnects.
- **W-061**: a new `event.connection id` on a live connection re-binds and re-configures.
- **Clock**: the host UTC clock is pushed at every bind (`info.time_zone=UTC` first, then `info.time=`) and again every `--clock-resync-interval` seconds; the 510 has no RTC and drifts.
- **Discovery** (`src/discovery.py`, stdlib only): ARP table entries with the Sirit OUI `00:17:9e`, then a TCP sweep of port 50007 over each local /24, then the factory address `169.254.1.2`; every hit is verified with `info.serial_number`. It runs when there is no target, after `--discover-after-failures` failed attempts (a single found reader that differs from the target becomes the new target and is reported as `discovered_ip`), and on a backend `discover` command (results only; a configured target is never replaced by a manual search). A `discover` command that arrives while a run is already in progress is answered with that run's result instead of a second sweep. The ARP table is read with `arp -a` on Windows and `arp -an` elsewhere (macOS/net-tools `arp -a` resolves hostnames and outlasts the 3 s tool timeout); Windows `arp -a` interface headers only add a /24 for addresses `ipconfig` did not list.

## Reader status heartbeat

With the http transport the service POSTs its status to `{backend-url}/reader/status` every `--heartbeat-interval` seconds and immediately on every state change (1 s timeout, no retry, no spool; `src/status_reporter.py`). The body always contains every key: `state`, `ip`, `target_source`, `serial`, `antennas`, `antenna_power`, `antenna_reads` (raw arrives per antenna since the current connection, before presence gating), `last_event_at`, `connected_since`, `error`, `consecutive_failures`, `next_retry_s`, `candidates`, `discovered_ip`, `discovery`, `reader_service_version`, `pid`.

The reply `{"config": {"reader_ip", "antenna_power"}, "command": null | {"id", "type": "discover" | "reconnect"}}` is how config changes reach the service without a restart: a different `reader_ip` switches the target and reconnects, a different `antenna_power` reconnects with the new power, `null` values are ignored. See `docs/PLAN-WINDOWS-NONTECHIE-CONTRACT.md` §2 for the full protocol.

## Key files

| File | Purpose |
| --- | --- |
| `src/racetag_reader_service.py` | Entry point — argument parsing, signal handling, stdin-EOF watcher, wires components |
| `src/sirit_client.py` | `SiritClient` — connection thread, CONTROL + EVENT receive loops, bind/config, event parsing, `TagTracker` integration |
| `src/status_reporter.py` | `ReaderStatusReporter` — heartbeat POST and reply delivery |
| `src/discovery.py` | Reader discovery (ARP, subnet sweep, factory IP, serial probe) |
| `src/tag_tracker.py` | `TagTracker` — per-antenna presence set + per-tag cooldown timer |
| `src/backend_client/http.py` | `HttpBackendClient` — batch POST with retry and JSONL spool |
| `src/utils.py` | Logging setup, connect with timeout + keepalive, timestamp helpers |
| `src/init_commands` | Plain-text init commands sent to the reader after session bind (includes UTC timezone, depart time, active mode) |

## Logs and spool

All loggers (`reader.*`) share one parent logger with:
- a console handler (only when the process has a stdout; a windowed Windows exe has none),
- an always-on rotating file `reader.log` (2 MB × 5) in `RACETAG_LOG_DIR` or `~/.racetag/logs`; `RACETAG_FILE_LOG=0` disables it.

Level is INFO, DEBUG with `--debug` / `RACETAG_DEBUG=true`.

If the backend is unreachable, failed batches are appended to `spool.jsonl` in the same directory (one JSON line per batch). When the backend becomes reachable again, spooled batches are delivered in order before new events are forwarded.

## init_commands file

The file at `src/init_commands` is sent to the reader after the session bind. It configures:
- UTC timezone on the reader clock (`info.time_zone=UTC`, redundant with the service's own clock push)
- Tag depart time (300 ms of radio silence triggers a depart event)
- Active operating mode

One command per line; blank lines, lines starting with `#` and inline `# comments` are stripped before sending. Override the path with `--init_commands_file` or `INIT_COMMANDS_FILE`.

See `apps/reader-service/docs/Sirit INfinity 510/` for reader protocol documentation.

## Tests

```bash
cd apps/reader-service
source .venv/bin/activate
pytest
```

Tests live in `tests/`. CI runs them with Python 3.11 on every push (see `.github/workflows/ci.yml`).
