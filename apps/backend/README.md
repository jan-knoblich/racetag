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

## SQLite persistence

The database (`racetag.db` inside `RACETAG_DATA_DIR`) is opened with:

```sql
PRAGMA journal_mode=WAL;
PRAGMA synchronous=FULL;
```

WAL mode allows concurrent reads while a write is in progress. `synchronous=FULL` flushes to disk on every write, eliminating data loss on power failure. See [ARCHITECTURE.md](../../ARCHITECTURE.md) for the durability rationale.

A `meta` table stores persistent configuration overrides (reader IP, lap count, cooldown). On startup the backend merges these with env defaults so settings survive restarts.

## Endpoints

Full spec: [`openapi.yaml`](openapi.yaml)

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/events/tag/batch` | Ingest tag events; triggers lap counting and SSE fan-out |
| `GET` | `/classification` | Standings snapshot, ordered by laps then last-pass time |
| `GET` | `/race` | Race metadata (total_laps, start_time, participants) |
| `PATCH` | `/race` | Update `total_laps` |
| `POST` | `/race/reset` | Clear lap data; preserve rider registrations |
| `GET` | `/config` | Effective config (env defaults merged with persisted overrides) |
| `PATCH` | `/config` | Update `reader_ip`, `min_lap_interval_s`, or `total_laps` |
| `GET` | `/diagnostics/antennas` | Per-antenna read counts for `?window_s=60` (default) |
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
| `race_reset` | After `POST /race/reset` | `{type}` |
| `race_updated` | After `PATCH /race` or `PATCH /config` (total_laps) | `{type, total_laps}` |

The stream uses `asyncio.Queue` per subscriber (one queue per SSE connection). A heartbeat comment (`: heartbeat`) is emitted every 15 seconds to keep the connection alive through proxies.

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
| `openapi.yaml` | API contract (source of truth for models and clients) |

## Tests

```bash
cd apps/backend
source .venv/bin/activate
pytest
```

Tests live in `tests/`. CI runs 49 tests with Python 3.13 on every push (see `.github/workflows/ci.yml`).
