# apps/backend — FastAPI race-state service

Ingests tag events from the reader-service, applies lap-counting and double-count defence, manages the rider registry, streams live standings via Server-Sent Events, and persists all state to SQLite.

## Role in the monorepo

The backend is the single source of truth for race state, rider data, and configuration. It is the only service that writes to the SQLite database. All other services and the browser UI talk to it over HTTP.

## Run it

### Docker Compose (recommended)

Run from the **monorepo root**:

```bash
docker compose up --build racetag-backend
```

Backend: http://localhost:8600

### Native (development)

```bash
cd apps/backend
python3.13 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt

uvicorn --app-dir racetag-backend app:app --reload --host 0.0.0.0 --port 8600
```

## Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `RACETAG_PORT` | `8600` | Port uvicorn listens on |
| `RACETAG_API_KEY` | _(unset)_ | When set, all requests must include `X-API-Key: <value>`. **Off by default in the packaged build** — set only if the backend is exposed beyond localhost. |
| `RACE_TOTAL_LAPS` | `5` | Initial target laps; can also be changed at runtime via `PATCH /config` or `PATCH /race`. |
| `RACE_MIN_PASS_INTERVAL_S` | `8` | Backend-side cooldown (seconds) — secondary defence against double-counts. Set lower than `MIN_LAP_INTERVAL_S` in the reader-service so the reader is the primary gate. |
| `RACETAG_DATA_DIR` | `./data` | Directory where `racetag.db` is stored. Use an absolute path in production so the DB survives container restarts with a mounted volume. |
| `READER_IP` | _(unset)_ | Fallback reader IP when none is persisted; returned by `GET /config` and in heartbeat replies. |
| `MIN_LAP_INTERVAL_S` | `8` | Fallback for `min_lap_interval_s` when none is persisted. |
| `RACETAG_VERSION` | _(unset)_ | App version reported as `version` in `GET /config`. Set by the desktop shell. |

## SQLite persistence

The database (`racetag.db` inside `RACETAG_DATA_DIR`) is opened with:

```sql
PRAGMA journal_mode=WAL;
PRAGMA synchronous=FULL;
```

WAL mode allows concurrent reads while a write is in progress. `synchronous=FULL` flushes to disk on every write, eliminating data loss on power failure.

A `meta` table stores persistent configuration overrides (reader IP, antenna power, lap count, cooldown, first-run assistant done). On startup the backend merges these with env defaults so settings survive restarts. The reader status (below) is kept in memory only.

## Endpoints

Full spec: [`openapi.yaml`](openapi.yaml)

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/events/tag/batch` | Ingest tag events; triggers lap counting and SSE fan-out |
| `GET` | `/classification` | Standings snapshot, ordered by laps then last-pass time |
| `GET` | `/race` | Race metadata (total_laps, start_time, participants) |
| `PATCH` | `/race` | Update `total_laps` |
| `POST` | `/race/reset` | Clear lap data; preserve rider registrations |
| `GET` | `/config` | Effective config (env defaults merged with persisted overrides) plus read-only `desktop` and `version` |
| `PATCH` | `/config` | Update `reader_ip` (`null` clears it), `antenna_power`, `min_lap_interval_s`, `total_laps`, or `assistant_done` (strict boolean; first-run assistant finished, survives restarts). Never restarts anything; the reader-service picks up reader changes from its next heartbeat reply. |
| `GET` | `/diagnostics/antennas` | Per-antenna read counts for `?window_s=60` (default) |
| `POST` | `/reader/status` | Reader-service heartbeat; reply carries reader config and an optional command |
| `GET` | `/reader/status` | Last reader status (`unknown` before the first / after 6 s without a heartbeat) plus supervisor status |
| `POST` | `/reader/discover` | Ask the reader-service to search the network; waits up to 15 s for the candidates |
| `POST` | `/reader/restart` | Reconnect the reader (supervisor restart in the desktop build, else a `reconnect` command); `202` |
| `GET` | `/stream` | SSE stream (see below) |
| `POST` | `/riders` | Register or update a rider — upsert by `tag_id` |
| `GET` | `/riders` | List all registered riders |
| `GET` | `/riders/recent-reads` | Last N unknown-tag arrive events (ring buffer, default 10) |
| `GET` | `/riders/{tag_id}` | Look up a rider by tag |
| `DELETE` | `/riders/{tag_id}` | Remove a rider |

## SSE stream frame types

The `/stream` endpoint emits newline-delimited `data:` frames. Known `type` values:

| Type | When emitted | Payload |
| --- | --- | --- |
| `lap` | On every accepted lap | `{type, tag_id, laps, finished, last_pass_time}` |
| `standings` | After every accepted lap | Full `ClassificationDTO` |
| `unknown_tag` | Arrive for an unregistered tag | `{type, tag_id, timestamp, antenna, rssi}` |
| `tag_seen` | Arrive for any tag, throttled to one per tag every 2 s | `{type, tag_id, timestamp, antenna, rssi, registered, bib, name}` |
| `race_started` / `race_ended` / `race_reopened` / `active_race_changed` | Race lifecycle changes | race-specific |
| `race_reset` | After `POST /race/reset` | `{type}` |
| `race_updated` | After `PATCH /race` or `PATCH /config` (total_laps, min_lap_interval_s) | `{type, total_laps}` or `{type, min_lap_interval_s}` |
| `reader_status` | Reader status changed, at most every 5 s otherwise, and once when the heartbeat goes stale | `{type, ...GET /reader/status}` |

The stream uses one `asyncio.Queue` per SSE connection, bound to the event loop that serves it. `_publish` may be called from any thread (sync routes run in a worker pool, the staleness check on its own thread) and always hands payloads over with `loop.call_soon_threadsafe`, so a waiting client is woken immediately. A keepalive comment (`: keepalive <time>`) is emitted after 15 idle seconds to keep the connection alive through proxies.

## Reader status protocol

The reader-service reports its connection to the reader via `POST /reader/status` every 2 s and on every state change (`searching`, `connecting`, `configuring`, `active`, `lost`, `stopped`). The binding interface is [`docs/PLAN-WINDOWS-NONTECHIE-CONTRACT.md`](../../docs/PLAN-WINDOWS-NONTECHIE-CONTRACT.md) §2; the implementation lives in `racetag-backend/reader_status_hub.py`.

- **Reply.** `{"config": {"reader_ip", "antenna_power"}, "command": null | {"id", "type": "discover" | "reconnect"}}`. Changing the reader IP or antenna power in Settings therefore reaches the reader-service within one heartbeat, in the desktop build and in Docker alike. Each command is delivered once; a `stopped` heartbeat never receives one.
- **Validation.** Only `state` is strict (unknown values → `422`). All other fields are sanitised (invalid IPs, non-numeric ports, bad candidate entries are dropped) so a malformed heartbeat never breaks the endpoint.
- **Discovery.** When the reader-service switched to a discovered reader it sends `discovered_ip`; the backend persists it as `reader_ip` before replying and logs `reader_ip changed via discovery: old -> new` on logger `racetag.backend`. Each discovery is persisted once: repeats of the same `discovered_ip` from the same reader-service `pid` are ignored until a heartbeat without it, so they cannot overwrite a `reader_ip` the operator saved in the meantime. `POST /reader/discover` queues a `discover` command and waits for the matching `discovery.request_id` in a later heartbeat. It answers `reader_service_unavailable` immediately when the status is `unknown`, except before the first heartbeat while the desktop supervisor reports the reader-service as running (still starting): then the command waits for that first heartbeat.
- **Staleness.** Without a heartbeat for 6 s the state becomes `unknown` (checked every second by a daemon thread started in the FastAPI `startup` hook and stopped on `shutdown`; tests call `_check_reader_stale()` and patch `_monotonic`).
- **Desktop.** The desktop shell registers its reader-service supervisor as `app.state.reader_controller`. It feeds `supervisor` in `GET /reader/status`, `desktop: true` in `GET /config`, and the supervisor path of `POST /reader/restart`.

## OpenAPI-first workflow

The spec at `openapi.yaml` is the contract. Pydantic models in `racetag-backend/models_api.py` are generated from it:

```bash
pip install datamodel-code-generator
datamodel-codegen --input openapi.yaml --input-file-type openapi \
  --output racetag-backend/models_api.py
```

Keep business logic in `racetag-backend/domain/`; keep API wiring in `racetag-backend/app.py`.

## Key files

| File | Purpose |
| --- | --- |
| `racetag-backend/app.py` | FastAPI app instance, all route handlers |
| `racetag-backend/domain/race.py` | `RaceState`, `Participant` — lap-counting domain logic |
| `racetag-backend/domain/riders.py` | `RiderRegistry` — rider CRUD and recent-reads ring buffer |
| `racetag-backend/storage.py` | `Storage` — SQLite wrapper (`WAL`, `synchronous=FULL`, meta table) |
| `racetag-backend/reader_status_hub.py` | Reader heartbeat validation, status, SSE publishing, staleness, command queue |
| `openapi.yaml` | API contract (source of truth for models and clients) |

## Tests

```bash
cd apps/backend
source .venv/bin/activate
pytest
```

Tests live in `tests/`. CI runs them with Python 3.13 on every push (see `.github/workflows/ci.yml`).
