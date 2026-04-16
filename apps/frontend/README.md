# apps/frontend — static race standings UI

A zero-dependency static HTML/CSS/JavaScript UI that displays live lap standings, supports rider registration (including bulk CSV import), and exposes settings and diagnostics panels. It consumes the backend's REST API and SSE stream.

## Role in the monorepo

The frontend is a static web app. In Docker it is served by nginx; in development by the bundled `serve.py`; in the desktop build it is mounted directly by the FastAPI backend via `StaticFiles` and rendered inside a pywebview window.

## Run it

### Docker Compose (recommended)

Run from the **monorepo root**:

```bash
docker compose up --build racetag-frontend
```

Frontend: http://localhost:8680

### Native (development)

```bash
cd apps/frontend
python3 serve.py --host 127.0.0.1 --port 8680
```

Open http://localhost:8680. The app auto-connects to the backend URL stored in `localStorage` (defaults to `http://localhost:8600`).

### Desktop build

In the packaged app, the frontend files are bundled as static assets inside the `Racetag.app` / `Racetag.exe` binary and served by the embedded uvicorn process. No separate frontend server is needed.

## Environment variables

These are only relevant when running via Docker (the `docker-entrypoint.sh` script injects them as runtime placeholder replacements in `script.js` and `api.js`).

| Variable | Default | Purpose |
| --- | --- | --- |
| `RACETAG_FRONTEND_PORT` | `8680` | Host port the nginx container listens on |
| `RACETAG_FRONTEND_BACKEND_URL` | `http://localhost:8600` | Backend URL injected into JS at container startup |
| `RACETAG_FRONTEND_API_KEY` | _(empty)_ | API key injected into JS; leave empty if the backend has no key set |

At runtime the browser also reads and writes the backend URL from `localStorage` (`racetag.backend` key), so it can be changed without restarting the container.

## Key files

| File | Purpose |
| --- | --- |
| `index.html` | App entry point |
| `script.js` | State, rendering, modal logic, CSV parsing, SSE event handling |
| `api.js` | `getApiHeaders()` and a `fetch`-based SSE reader (native `EventSource` cannot send `X-API-Key` headers) |

## Features

**Standings table** — live-updated via SSE `standings` frames. Shows bib, name, laps, gap to leader, and last-pass time. Timestamps are rendered in the browser's local timezone via `formatTimestampForDisplay` (UTC stored at source, displayed locally).

**Register-rider modal** — opens automatically when an `unknown_tag` SSE event arrives. Pre-fills the tag ID from the recent-reads ring buffer. Submits a `POST /riders` request to the backend and persists the mapping in SQLite.

**Bulk CSV import** — the Settings modal includes a CSV import flow. The CSV must have columns `tag_id`, `bib`, `name`. Records are posted to `POST /riders` individually; existing riders are updated (upsert).

**Settings modal** (gear icon) — configures reader IP, total laps, and lap cooldown via `PATCH /config`. Changes persist across restarts.

**Diagnostics panel** — shows per-antenna read counts (calls `GET /diagnostics/antennas`). Useful for confirming all antennas are active before a race.

**SSE auto-reconnect** — the fetch-based SSE reader retries with exponential backoff when the connection drops or the backend restarts.

## Wire protocol

The frontend speaks only to the backend (default `http://localhost:8600`). It uses:
- `GET /classification` for the initial standings snapshot on load.
- `GET /stream` (SSE, `text/event-stream`) for live updates.
- `GET /riders/recent-reads` to populate the register-rider modal.
- `POST /riders`, `GET /riders`, `DELETE /riders/{tag_id}` for rider management.
- `GET /config`, `PATCH /config` for settings.
- `GET /diagnostics/antennas` for the diagnostics panel.
- `POST /race/reset`, `PATCH /race` for race control.

## Tests

The frontend has no automated unit tests. Manual smoke tests are described in `tests/manual/` in the repo root.
