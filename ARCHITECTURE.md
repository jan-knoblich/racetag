# Racetag — Current System Architecture

_Date: 2026-04-15_
_Prepared by: Agent 1 — The Architect_

This document describes the **current** state of the Racetag proof-of-concept: three independent applications that together detect RFID/race-tag passings on a closed-loop bicycle race course, compute live standings, and display them in a browser UI.

> **Monorepo transition (decided 2026-04-15):** The three source repos under `paclema` are being consolidated into a single `racetag` monorepo owned by the user, with history preserved via `git subtree add`. The canonical layout going forward is:
> ```
> racetag/
> ├── apps/
> │   ├── reader-service/   # ← was paclema/racetag-reader-service
> │   ├── backend/          # ← was paclema/racetag-backend
> │   ├── frontend/         # ← was paclema/racetag-frontend
> │   └── desktop/          # ← new (PyInstaller + pywebview shell)
> ├── docs/                 # ARCHITECTURE.md, PLAN.md, PACKAGING.md, etc.
> ├── .github/workflows/
> ├── docker-compose.yml
> └── README.md
> ```
> Every path reference in the rest of this document should be read as `apps/<name>/…` inside the monorepo. The legacy three-repo path is preserved in square brackets `[…]` after each such reference for traceability during the migration window. The consolidation steps are `W-M00 … W-M08` in `PLAN.md`.

---

## 1. Executive summary

Racetag originated as three Git repositories owned by the GitHub user `paclema`; it is being consolidated into a single monorepo (see the banner above):

| App (monorepo path)    | Legacy repo                                                                                  | Role                                | Language / stack         | Network position |
| ---------------------- | -------------------------------------------------------------------------------------------- | ----------------------------------- | ------------------------ | ---------------- |
| `apps/reader-service/` | [`paclema/racetag-reader-service`](https://github.com/paclema/racetag-reader-service)        | Talks to the physical RFID reader   | Python 3.11, `requests`  | Edge / on-site   |
| `apps/backend/`        | [`paclema/racetag-backend`](https://github.com/paclema/racetag-backend)                      | Ingests events, computes standings  | Python 3.13, FastAPI     | Server / local   |
| `apps/frontend/`       | [`paclema/racetag-frontend`](https://github.com/paclema/racetag-frontend)                    | Browser UI (standings table, SSE)   | Static HTML/JS + nginx   | Browser          |
| `apps/desktop/`        | _new — added in Phase 4_                                                                     | PyInstaller + pywebview shell       | Python + bundled assets  | Local desktop    |

All four apps are implemented and ship as a single double-clickable binary for macOS and Windows (see `apps/desktop/` and `PACKAGING.md`). The backend persists race state and rider data to SQLite (`journal_mode=WAL`, `synchronous=FULL`; `W-050` complete).

Tech-stack observation: every application is Python-or-browser based. No Go, no Rust, no native code. This simplifies packaging dramatically (see `PACKAGING.md`).

---

## 2. Component diagram

```
┌─────────────────────────┐                          ┌───────────────────────────────┐
│  Sirit INfinity 510     │                          │  apps/reader-service/          │
│  (or Invelion YR9700)   │   CONTROL  tcp 50007     │  (Python, one process)         │
│                         ├─────────────────────────►│                                │
│  physical RFID reader   │   EVENT    tcp 50008     │  • SiritClient                 │
│  (several antennas)     ├─────────────────────────►│  • TagTracker (presence set)   │
└─────────────────────────┘                          │  • HttpBackendClient (batch)   │
                                                     └────────────────┬───────────────┘
                                                                      │ POST /events/tag/batch
                                                                      │ (TagEventBatchDTO, batches ≤10, 50 ms flush)
                                                                      │ X-API-Key header
                                                                      ▼
                                                     ┌───────────────────────────────┐
                                                     │  apps/backend/                 │
                                                     │  (FastAPI on :8600)           │
                                                     │                                │
                                                     │  • POST /events/tag/batch     │
                                                     │  • GET  /classification       │
                                                     │  • GET  /race                 │
                                                     │  • GET  /stream  (SSE)        │
                                                     │                                │
                                                     │  Domain: RaceState, Participant│
                                                     │  Storage: in-memory dicts     │
                                                     │  (→ SQLite w/ synchronous=FULL │
                                                     │   under W-050)                 │
                                                     └────────────────┬───────────────┘
                                                                      │ GET /classification (snapshot, UTC timestamps)
                                                                      │ GET /stream (text/event-stream, UTC timestamps)
                                                                      ▼
                                                     ┌───────────────────────────────┐
                                                     │  apps/frontend/                │
                                                     │  (static HTML/CSS/JS behind    │
                                                     │   nginx, or python serve.py)   │
                                                     │                                │
                                                     │  • renderStandings()          │
                                                     │  • UTC → browser-local TZ     │
                                                     │  • CSV import (tag_id → bib,  │
                                                     │    name)  — browser-side only │
                                                     └───────────────────────────────┘

          (Packaged desktop build, Phase 4) ─────────────────────────────────────────
                                                     ┌───────────────────────────────┐
                                                     │  apps/desktop/                 │
                                                     │  Racetag.app / Racetag.exe    │
                                                     │  (pywebview shell that starts │
                                                     │   backend + reader + frontend)│
                                                     └───────────────────────────────┘
```

**Timezone policy (decided 2026-04-15, enforced in `W-030`):** reader service converts to UTC **at the source**; backend stores and emits only UTC ISO-8601 (`Z`-suffixed); frontend converts to the browser-resolved local timezone for display (default `Europe/Berlin`).

---

## 3. Data flow (one rider completing one lap)

1. Rider with tag `0xAABB…` crosses the finish line.
2. Reader broadcasts over EVENT socket:
   `event.tag.arrive tag_id=0xAABB..., first=..., antenna=1` (CRLFCRLF delimited).
3. `SiritClient._recv_loop` in `apps/reader-service/src/sirit_client.py:115` reads the TCP buffer, splits on `\r\n\r\n`, hands each frame to `_handle_message`.
4. `_handle_message` (sirit_client.py:150) detects `event.tag.arrive`, calls `TagTracker.mark_present(tag_id)`. If the tag is **not** already marked present, it parses the event into a `TagEvent` dataclass and emits it via the backend client. (This is the core “arrive/depart gating” logic.)
5. `HttpBackendClient` enqueues the event (http.py:44). A worker thread batches up to 10 events or waits 50 ms, then POSTs `TagEventBatchDTO` to `/events/tag/batch`.
6. Backend (`apps/backend/racetag-backend/app.py:71`) loops over each event; if `event_type == arrive`, it calls `RaceState.add_lap(tag_id, timestamp)` which unconditionally increments `laps` and bumps `last_pass_time` (`apps/backend/racetag-backend/domain/race.py:34`).
7. Backend then builds a standings snapshot and pushes `{type: "standings", items: [...]}` to every SSE subscriber buffer.
8. Browser’s `connectSSEWithHeaders` polyfill (`api.js:13`) pulls the frame, invokes `renderStandings()`, which fills the table. The browser separately maintains a `tagData` Map populated from CSV, and merges bib/name client-side.
9. When the reader sees no signal for `tag.reporting.depart_time` (300 ms from `init_commands`), it emits `event.tag.depart`. The service `TagTracker.mark_absent` clears the presence flag so the **next** arrive on the next lap will fire again.

---

## 4. Component specifications

### 4.1 `apps/reader-service/` [legacy: `racetag-reader-service`]

- **Entry point:** `apps/reader-service/src/racetag_reader_service.py` (CLI + env var config).
- **Primary class:** `SiritClient` (`apps/reader-service/src/sirit_client.py`).
- **Threads:** CONTROL recv, EVENT recv, optional stdin, HTTP worker. Lifetime managed via `threading.Event`.
- **Reader protocol:** text lines terminated by `\r\n\r\n`. Commands live in `apps/reader-service/src/init_commands`. The reader is switched to `setup.operating_mode=active` at the end of init.
- **Event parser:** regex-extracted key/value pairs (`apps/reader-service/src/sirit_client.py:237`). Fragile but works for current reader firmware.
- **Backend transport:** `HttpBackendClient` (batch POST, retries = none, 2 s timeout) or `MockBackendClient`.
- **Auth:** `X-API-Key` header (despite a legacy comment referencing Bearer).
- **Time:** timestamps sent to the backend are re-minted at the service using `datetime.now(UTC)` — the reader-provided `first`/`last` times are **carried but ignored** for lap timing.
- **Dockerfile:** `python:3.11-slim`, non-root user, `network_mode: host` in compose (needed to reach the reader on LAN).

### 4.2 `apps/backend/` [legacy: `racetag-backend`]

- **Entry point:** `apps/backend/racetag-backend/app.py` (FastAPI / uvicorn, port `RACETAG_PORT` default 8600).
- **Domain:** `apps/backend/racetag-backend/domain/race.py` holds `Participant` and `RaceState`. State is a single module-level instance (`app.py:58`).
- **Generated models:** `apps/backend/racetag-backend/models_api.py` is auto-generated from `openapi.yaml` by `datamodel-code-generator` during Docker build.
- **Endpoints:**
  - `POST /events/tag/batch` — accepts `TagEventBatchDTO`, returns `{events_processed: N}`. For every `arrive`, `RaceState.add_lap` is invoked and two payloads (`lap`, `standings`) are pushed to every SSE subscriber queue.
  - `GET /classification` — ordered snapshot.
  - `GET /race` — race metadata.
  - `GET /stream` — Server-Sent Events; a simple list-based subscriber queue, one `time.sleep(1)` heartbeat loop per client.
- **Auth:** optional `X-API-Key` via env var `RACETAG_API_KEY`. CORS is `*`.
- **Persistence:** SQLite (`racetag.db` in `RACETAG_DATA_DIR`, default `./data`). `Storage` class wraps the connection with `journal_mode=WAL` and `synchronous=FULL`. A `meta` table stores config overrides.
- **Configuration:** `RACE_TOTAL_LAPS` (default 5) governs when a participant is marked `finished`. Overridable at runtime via `PATCH /config` or `PATCH /race`.

#### Persistence durability (W-050)

SQLite is opened with `journal_mode=WAL` (concurrent reads during writes) and `synchronous=FULL` (fdatasync on every commit). Single-event write latency budget: under 5 ms p99 on SSD. Event rate is dominated by the `min_pass_interval_s=8 s` cooldown and reader quiet periods between laps, so the ~1 ms fsync cost per accepted event is well within budget. A `meta` table stores persistent config overrides (reader IP, lap count, cooldown) so settings survive process restarts.

### 4.3 `apps/frontend/` [legacy: `racetag-frontend`]

- **Entry point:** `apps/frontend/index.html` — static. Shipped with nginx in Docker, or via `python3 serve.py` locally.
- **Scripts:**
  - `apps/frontend/script.js` — state, rendering, CSV parsing, event wiring.
  - `apps/frontend/api.js` — `getApiHeaders()` and a hand-rolled SSE reader built on `fetch()` + `ReadableStream` (needed because the native `EventSource` cannot send an `X-API-Key` header).
- **Runtime config injection:** `docker-entrypoint.sh` rewrites `__RACETAG_FRONTEND_API_KEY__` and `__RACETAG_FRONTEND_BACKEND_URL__` placeholders in `script.js` + `api.js` using `sed` on container startup. There is no build step.
- **CSV import:** browser-only mapping `tag_id → {bib, name}`. Never posted back to the backend; a page reload loses it.
- **Backend URL:** stored in `localStorage.racetag.backend`; falls back to `http://localhost:8600`.

---

## 5. Interaction contracts

### 5.1 Event contract (reader-service → backend)

```json
POST /events/tag/batch
{
  "events": [
    {
      "source": "sirit-510",
      "reader_ip": "192.168.1.130",
      "reader_serial": "ABCDEF01",
      "timestamp": "2026-04-15T11:53:32.298Z",
      "event_type": "arrive",
      "tag_id": "C5A1BE1B694E02089950CE2217F46FBA",
      "session_id": 17,
      "antenna": 1,
      "rssi": -47,
      "first": "2026-04-15T11:53:32.200"
    }
  ]
}
```

Response: `{"events_processed": 1}`. The reader service treats any mismatch between `processed` and `len(items)` as a `RuntimeError` (http.py:92).

### 5.2 Standings SSE payload (backend → frontend)

Two event types share the same SSE channel:

```json
{"type":"lap","tag_id":"…","laps":3,"finished":false,"last_pass_time":"…"}
{"type":"standings","items":[{ParticipantDTO…}, …]}
```

The frontend currently only consumes `type === "standings"` (`script.js:148`).

---

## 6. Tech-stack / version inventory

| Component        | Language / runtime     | Key libs / versions                            |
| ---------------- | ---------------------- | ---------------------------------------------- |
| reader-service   | Python 3.11            | `requests>=2.28,<3`; stdlib sockets, threading |
| backend          | Python 3.13            | FastAPI 0.119.0, uvicorn 0.37.0, pydantic 2.12 |
| frontend         | Vanilla HTML/CSS/JS    | no framework, served by nginx 1.29 alpine      |
| container runtime| Docker / docker compose| `network_mode: host` used by reader service    |

There are **no** TypeScript, React, Vue, Go, Rust, C++, or database dependencies anywhere.

---

## 7. Deployment topology today

- _Pre-monorepo (current state of source repos):_ Three docker-compose files, one per repo. Not unified.
- _Post-monorepo (post `W-M06`):_ A single `docker-compose.yml` at the monorepo root builds all three services from `./apps/reader-service`, `./apps/backend`, `./apps/frontend`. `git clone` once, `docker compose up --build -d` once.
- Typical operator flow (post-merge): clone the monorepo, set a single `.env` file at root (or rely on each app's `.env.example`), run `docker compose up --build -d` from the root.
- The reader service relies on host networking to find the physical reader on the LAN (Ethernet bridge on Windows).
- The backend is reached from the browser on `http://localhost:8600`. CORS is permissive.
- Packaged desktop build (`apps/desktop/`, Phase 4) subsumes all of the above into a single double-clickable `Racetag.app` / `Racetag.exe` with a bundled loopback uvicorn and subprocess reader.

---

## 8. Architectural observations (non-exhaustive — see `ISSUES.md` for the full list)

1. **Single-process global race state** in the backend means restart = data loss and no multi-race support.
2. **No persistence at all** for tag⇄name coupling; it lives only in a CSV on someone’s laptop.
3. **Mixed timing sources:** the reader emits `first`/`last` timestamps but the service rewrites them with wall-clock time before posting. Any network jitter becomes measurement error. _Resolved by `W-030`: reader switched to UTC and `first`/`last` becomes authoritative; backend stores UTC; frontend converts to browser-local for display._
4. **Lap de-duplication is done in the service, not the backend.** Every arrive the service forwards is treated as a lap by `RaceState.add_lap`. Consequently the service is the single point of truth for “this is a new lap” — and its current logic (`TagTracker.present`) does not understand antennas.
5. **SSE server uses blocking sleeps** (`app.py:144`). For a couple of dozen clients it is fine; it is not production-shaped.
6. **No tests** in any repo.
7. **No structured logging** — `print()` everywhere.

---

## 9. Glossary

- **Arrive / Depart:** Sirit EPC-gen2 events. “Arrive” fires when a tag first becomes visible; “depart” fires after `tag.reporting.depart_time` ms of silence.
- **Antenna:** One of several physical UHF antennas connected to the same reader; for bike races you typically have 2–4 across the timing line.
- **Session id:** Sirit’s EVENT-socket channel id; the service must bind events to this id before the reader will emit anything.
- **Classification / Standings:** The ordered list of participants by laps then last-pass time.
- **Tag ID / EPC:** The unique hex serial printed on the UHF passive RFID sticker.
