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

On load the page asks its own origin for `GET /config`. When that answers `desktop: true` the page switches to **desktop mode**: it always talks to the same origin (a stored Backend URL is ignored), hides the Backend URL field and the Connect button, moves the tag-column toggle into Settings → Erweitert, shows the desktop-only support buttons, and offers the first-run assistant. Otherwise (Docker, `serve.py`) the page behaves as before.

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
| `index.html` | App entry point. Scripts load in this order: `strings.js`, `api.js`, `script.js`, `tooltips.js` |
| `strings.js` | `window.RT`: German string table `RT.S`, `RT.fmt(key, vars)` for `{placeholder}` texts, `RT.apiError(status, bodyText, fallbackKey)` which turns backend error bodies into German messages |
| `api.js` | `getApiHeaders()` and a `fetch`-based SSE reader (native `EventSource` cannot send `X-API-Key` headers); reports `connecting` / `live` / `reconnecting` via `onStateChange` |
| `script.js` | State, rendering, modal logic, CSV parsing, SSE event handling, status bar, reader discovery, first-run assistant |
| `tooltips.js` | `window.RT_TIPS` (tips keyed by element id) and `RT.applyTips(root)`; one `position: fixed` tooltip bubble for `data-tip` / `title` (hover, keyboard focus, `.rt-info` icons on touch) |

All new globals live under `window.RT` / `window.RT_TIPS`; the classic scripts share one global scope, so never redeclare an existing top-level name.

## Language and tooltips

The operator UI is German only (`<html lang="de">`). Code, identifiers, console logs and the API stay English.

- **Static text** (labels, headings, buttons, placeholders) is written directly in `index.html`.
- **Dynamic text** (toasts, confirm dialogs, status texts, race banner, standings cells, CSV import messages) comes from `RT.S` in `strings.js`, filled via `RT.fmt(key, vars)`. Do not put literal operator text into `script.js`.
- **Backend errors** are never shown raw. Use `RT.apiError(status, bodyText, fallbackKey)` (or the `rtResponseError(res, fallbackKey)` helper in `script.js`): known English `detail` texts are translated, FastAPI validation errors name the affected fields in German, everything else becomes a German status-code message. For network failures pass status `0`.
- **Tooltips** live in `window.RT_TIPS` in `tooltips.js`, keyed by element id — every button, input, select, column header and panel has one. Settings and create-race fields additionally have an info icon (`<button class="rt-info" id="<field>Info">`) whose text starts with the default value (`Standard: …`) and says when to change it. Rows and list items rendered by `script.js` carry their tip as a `title` in the template (texts `RT.S.rowTip*`). Elements whose tip changes at runtime (status pills, update notice) are listed in `RT.DYNAMIC_TIP_IDS`.
- Intentional English leftovers: the product name "Racetag Live", the CSV column names `tag_id`, `bib`, `name`, `verein`, `uci_id` (file format), the result codes DNF / DNS / DSQ, and the example timestamp format in the lap editor.

## Features

**Standings table** — live-updated via SSE `standings` frames. Shows bib, name, laps, gap to leader, and last-pass time. Timestamps are rendered in the browser's local timezone via `formatTimestampForDisplay` (UTC stored at source, displayed locally).

**Register-rider modal** — opens automatically when an `unknown_tag` SSE event arrives. Pre-fills the tag ID from the recent-reads ring buffer. Submits a `POST /riders` request to the backend and persists the mapping in SQLite.

**Bulk CSV import** — the *CSV importieren* header button. The CSV must have columns `tag_id`, `bib`, `name`. Records are posted to `POST /riders` individually; existing riders are updated (upsert).

**Status bar** — three pills under the header controls:
- *Reader* — state from the reader-service heartbeat (`GET /reader/status` on load and after every stream reconnect, then SSE `reader_status`): green `active`, amber `connecting` / `configuring` / `searching` / `lost`, red `unknown` / `stopped`. Desktop build: `stopped` while `supervisor.restart_pending` or `supervisor.running` is true is amber "wird neu gestartet…" (the old child's last heartbeat during "Reader neu verbinden"), and `unknown` before the first heartbeat (`updated_at` null) while `supervisor.running` is true is amber "startet…" for up to 30 s after page load. The tooltip lists IP, serial, antennas, age of the last read and the recovery hint. Click opens Settings.
- *Antennen* — powered ports from the heartbeat, raw reads per port in the last 60 s (from `antenna_reads` deltas) and stored passes per port (`GET /diagnostics/antennas`, polled every 10 s). Green `1, 2 OK` means the ports are powered, not that a cable is plugged in. Amber `… nicht erkannt` when the heartbeat carries `antennas_detected` and a powered port is missing from it and has not read a tag on this connection; amber `… liest nichts` while a race runs and one port read nothing in 60 s although the others read at least 5 tags. Click opens the diagnostics panel.
- *Verbindung* — state of the live SSE stream; red after three failed attempts. Click reconnects immediately.

While the stream is down the Reader and Antennen pills keep their last text but turn grey. Reader connection loss, recovery (with outage duration), a discovered new reader IP and a silent reader-service raise amber/green/red toasts. `setStatus(text)` still exists for transient messages (CSV import progress) and writes to a small message area in the same bar.

**Settings modal** (gear icon) — reader section with live status, *Reader suchen* (`POST /reader/discover`, candidates with IP, serial and source; *Übernehmen* writes the IP via `PATCH /config`), *Reader neu verbinden* (`POST /reader/restart`), reader IP and antenna power; race section with lap cooldown, total laps and the active race's snapshot interval. A changed reader IP or antenna power is picked up by the reader-service within about 2 s — no app restart, but the reader session is rebuilt. While a race runs and the reader is connected, *Reader neu verbinden*, *Übernehmen* of another IP and saving a changed IP or antenna power ask for confirmation first, because passes in the reconnect gap are not recorded. *Hilfe & Support* opens the assistant again and shows the version; in the desktop build it also offers *Datenordner öffnen* and *Support-Paket erstellen* through `window.pywebview.api` (`open_data_folder`, `create_support_bundle`, `app_info`). Escape closes any open modal.

**First-run assistant** (desktop build) — shown when `GET /config` reports `assistant_done: false` (fallback for a backend without the field: `localStorage["racetag.assistantDone"]` unset), no race exists apart from the untouched `Default race` the backend creates on a fresh database, and that race has no riders yet. Steps: find the reader, antenna test (tiles turn green as `antenna_reads` grow), create the first race (`POST /races`, then activate). If the active race is not started and already has riders, step 3 offers to keep it (`PATCH /races/{id}` with name and laps, default) or to create a new empty race; creating one while a race runs asks first. Finishing shows a toast and highlights *Rennen starten*, because laps only count after the start. Skip, Escape or finishing sends `PATCH /config {"assistant_done": true}` (and sets the localStorage flag); the flag survives app restarts. *Einrichtungs-Assistent* in Settings opens it again at any time.

**Update notice** — once per start, if `/config` reports a `version`, the page asks the GitHub releases API (3 s timeout) and shows a small link when a newer tag exists. Every error is ignored.

**Diagnostics panel** — shows per-antenna read counts (calls `GET /diagnostics/antennas`). Useful for confirming all antennas are active before a race.

**SSE auto-reconnect** — the fetch-based SSE reader retries with exponential backoff when the connection drops or the backend restarts. A stall watchdog aborts and reopens the stream when no byte (keepalive comments included) arrived for 40 s — the backend sends a keepalive every 15 s and `reader_status` every 5 s — so a half-open connection turns the Verbindung pill amber instead of staying green.

## Wire protocol

The frontend speaks only to the backend (default `http://localhost:8600`). It uses:
- `GET /classification` for the initial standings snapshot on load.
- `GET /stream` (SSE, `text/event-stream`) for live updates.
- `GET /riders/recent-reads` to populate the register-rider modal.
- `POST /riders`, `GET /riders`, `DELETE /riders/{tag_id}` for rider management.
- `GET /config` (incl. `desktop`, `version`, `assistant_done`), `PATCH /config` for settings and the assistant's done flag.
- `GET /reader/status`, SSE `reader_status`, `POST /reader/discover`, `POST /reader/restart` for the status bar, discovery and reconnect.
- `GET /diagnostics/antennas` for the diagnostics panel and the Antennen pill.
- `GET /races`, `POST /races`, `PATCH /races/{id}`, `POST /races/{id}/activate` for the race selector and the assistant.
- `https://api.github.com/repos/jan-knoblich/racetag/releases/latest` for the optional update notice.
- `POST /race/reset`, `PATCH /race` for race control.

## Tests

The frontend has no automated unit tests. Check every JS file with `node --check <file>`; a syntax error in one classic script stops the whole page. When adding a control, add its `RT_TIPS` entry; when adding a dynamic text, add its `RT.S` key (an unknown key renders as the bare key name). Manual smoke tests are described in `tests/manual/` in the repo root.
