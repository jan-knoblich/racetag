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
# Set at least READER_IP in apps/reader-service/.env

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

# With debug logging (writes logs/reader.log in addition to stderr)
python src/racetag_reader_service.py --ip 192.168.1.130 --backend-url http://localhost:8600 --debug

# Override min-lap cooldown (default 10 s)
python src/racetag_reader_service.py --ip 192.168.1.130 --backend-url http://localhost:8600 \
  --min-lap-interval 8

# Use mock transport (no backend needed — useful for hardware-only testing)
python src/racetag_reader_service.py --ip 192.168.1.130 --backend-transport mock
```

Press `Ctrl-C` to stop. The service sets the reader to standby on exit.

## CLI flags and environment variables

| Flag | Env var | Default | Purpose |
| --- | --- | --- | --- |
| `--ip` | `READER_IP` | _(required)_ | IPv4 address of the Sirit reader |
| `--control-port` | `CONTROL_PORT` | `50007` | Reader CONTROL socket port |
| `--event-port` | `EVENT_PORT` | `50008` | Reader EVENT socket port |
| `--backend-url` | `BACKEND_URL` | _(required for http transport)_ | Backend base URL |
| `--backend-transport` | `BACKEND_TRANSPORT` | `http` | `http` or `mock` |
| `--backend-token` | `BACKEND_TOKEN` | _(none)_ | `X-API-Key` value sent to backend |
| `--min-lap-interval` | `MIN_LAP_INTERVAL_S` | `10.0` | Minimum seconds between two forwarded arrive events for the same tag — primary double-count gate |
| `--init_commands_file` | `INIT_COMMANDS_FILE` | `src/init_commands` | Path to the reader init commands file |
| `--debug` | `RACETAG_DEBUG` | `false` | Enable debug logging; also writes `logs/reader.log` |
| `--interactive` | `INTERACTIVE` | `false` | Allow typing CONTROL commands on stdin |
| `--raw` | `RAW` | `false` | Print raw socket data |
| `--no-color` | `NO_COLOR` | `false` | Disable ANSI colours in console output |

## Key files

| File | Purpose |
| --- | --- |
| `src/racetag_reader_service.py` | Entry point — argument parsing, signal handling, wires components |
| `src/sirit_client.py` | `SiritClient` — CONTROL + EVENT TCP loops, event parsing, `TagTracker` integration |
| `src/tag_tracker.py` | `TagTracker` — per-antenna presence set + per-tag cooldown timer |
| `src/backend_client/http.py` | `HttpBackendClient` — batch POST with retry and JSONL spool |
| `src/init_commands` | Plain-text init commands sent to the reader after session bind (includes UTC timezone, depart time, active mode) |

## Debug output and spool

When `--debug` is set (or `RACETAG_DEBUG=true`):
- Structured log lines are written to `logs/reader.log` in the working directory.
- Console output is also elevated to DEBUG level.

If the backend is unreachable, failed batches are appended to `logs/spool.jsonl` (one JSON line per batch). When the backend becomes reachable again, spooled batches are delivered in order before new events are forwarded.

## init_commands file

The file at `src/init_commands` is sent to the reader after the session bind. It configures:
- UTC timezone on the reader clock (`setup.time.timezone = 0`)
- Tag depart time (300 ms of radio silence triggers a depart event)
- Active operating mode

One command per line; blank lines and lines starting with `#` are ignored. Override the path with `--init_commands_file` or `INIT_COMMANDS_FILE`.

See `apps/reader-service/docs/Sirit INfinity 510/` for reader protocol documentation.

## Tests

```bash
cd apps/reader-service
source .venv/bin/activate
pytest
```

Tests live in `tests/`. CI runs them with Python 3.11 on every push (see `.github/workflows/ci.yml`).
