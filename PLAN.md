# Racetag — Implementation Plan

_Date: 2026-04-15_
_Prepared by: Agent 1 — The Architect_

This plan takes the issues catalogued in `ISSUES.md` and turns them into atomic work items for four specialist agents:

- **BACKEND** — works under `apps/reader-service/` and `apps/backend/` (both Python) inside the unified `racetag` monorepo.
- **FRONTEND** — works under `apps/frontend/` (HTML/CSS/JS) inside the monorepo.
- **QA** — writes tests and verification scripts under `apps/*/tests/` inside the monorepo.
- **DOCS** — keeps READMEs and this folder's MD files in sync.

> **Monorepo note (decided 2026-04-15):** All three historical repos (`racetag-reader-service`, `racetag-backend`, `racetag-frontend`) are consolidated into a single repo named `racetag`, with history preserved via `git subtree add`. File paths below reference the **monorepo layout** (`apps/reader-service/`, `apps/backend/`, `apps/frontend/`, `apps/desktop/`). See Phase M below. Any work item referencing "legacy repo path" gives the pre-merge path in brackets for traceability.

The legend at the top of every work item:

- **ID** — stable identifier for cross-referencing.
- **Role** — which agent does it.
- **Priority** — P0 blocker / P1 important / P2 nice-to-have.
- **Depends on** — IDs of prerequisite work items.

The numbering follows a rough execution order but is not strictly sequential — items without dependencies can run in parallel.

---

## Phase M — Monorepo consolidation (prerequisite for all code-touching work)

_All code-change work items (W-001 onwards) are **blocked** on this phase completing. Run these strictly sequentially in the order listed._

### W-M00 Collect GitHub username from user (needs-input)
- **ID:** `W-M00`
- **Role:** DOCS (coordination)
- **Priority:** P0 (blocker)
- **Goal:** Obtain the GitHub account under which the new `racetag` monorepo will be created. We deliberately do **not** guess this; the three source repos live under `paclema`, but the user (email `jk.jan.knoblich@gmail.com`) has not confirmed whether the new repo will be on that account, on a personal `jknoblich`/`jk-knoblich`-style account, or on an organisation.
- **Acceptance criteria:** A file `/Users/jan/Documents/git/racetag/MONOREPO_TARGET.md` records `github_username:` and `repo_visibility:` (public/private) — written only after the user supplies them. No subsequent Phase M work item may begin until that file exists.
- **Dependencies:** none (strictly user input).

### W-M01 Decide monorepo layout and merge strategy
- **ID:** `W-M01`
- **Role:** DOCS (architecture)
- **Priority:** P0
- **Goal:** Lock the canonical monorepo layout and the history-preservation strategy before any merge operations run.
- **Decided layout (this plan):**
  ```
  racetag/                          # new monorepo root
  ├── apps/
  │   ├── reader-service/           # was: paclema/racetag-reader-service
  │   ├── backend/                  # was: paclema/racetag-backend
  │   ├── frontend/                 # was: paclema/racetag-frontend
  │   └── desktop/                  # new (PyInstaller + pywebview shell, Phase 4)
  ├── docs/                         # cross-cutting docs (architecture, packaging, operator guide)
  ├── .github/workflows/            # unified CI (tests + release)
  ├── docker-compose.yml            # workspace dev orchestration
  ├── README.md
  └── LICENSE
  ```
  Rationale: `apps/<name>/` is the de-facto convention (Turborepo, Nx, Vercel templates) and cleanly separates deployable artefacts from shared `docs/` and CI; `desktop/` slots in as a peer rather than nesting under `backend/` as originally proposed in `PACKAGING.md`.
- **Decided merge strategy:** `git subtree add --prefix=apps/<name> <remote> main` for each of the three source repos, one commit per source. Rejected alternatives:
  - `git filter-repo --to-subdirectory-filter`: rewrites SHAs, so issue/PR cross-refs break; heavier. Only warranted if we want each source repo's history *linearised* with its new path as if it had always been there — not needed for this prototype.
  - `git submodule`: does not actually merge history; operator still clones three things. Rejected.
- **Acceptance criteria:** This work item's design is captured in a short `docs/MONOREPO.md` (≤1 page) that names the three subtree commands with placeholders for the source URLs.
- **Dependencies:** `W-M00`.

### W-M02 Create empty `racetag` monorepo and push to GitHub
- **ID:** `W-M02`
- **Role:** BACKEND
- **Priority:** P0
- **Goal:** Initialise the monorepo with `README.md`, `.gitignore`, `LICENSE`, and an empty `apps/` directory; push to `github.com/<github_username>/racetag`.
- **Repo/files:** new path `/Users/jan/Documents/git/racetag/racetag/` (local clone). Initial commit sets up the scaffold only — no application code yet.
- **Acceptance criteria:** `git clone` of the new remote succeeds; `ls apps/` shows an empty directory (or a `.gitkeep`).
- **Dependencies:** `W-M00`, `W-M01`.

### W-M03 Subtree-merge `racetag-reader-service`
- **ID:** `W-M03`
- **Role:** BACKEND
- **Priority:** P0
- **Goal:** Import the reader-service repo into `apps/reader-service/` preserving full commit history.
- **Command (for reference, not to execute now):**
  ```
  git remote add reader-service https://github.com/paclema/racetag-reader-service.git
  git fetch reader-service
  git subtree add --prefix=apps/reader-service reader-service main
  git remote remove reader-service
  ```
- **Acceptance criteria:** `git log apps/reader-service/` shows the complete pre-merge history; a representative old commit SHA from the source repo is reachable in the new monorepo.
- **Dependencies:** `W-M02`.

### W-M04 Subtree-merge `racetag-backend`
- **ID:** `W-M04`
- **Role:** BACKEND
- **Priority:** P0
- **Goal:** Import the backend repo into `apps/backend/`.
- **Acceptance criteria:** as W-M03, for backend; `apps/backend/racetag-backend/app.py` is present.
- **Dependencies:** `W-M02` (independent of W-M03 — can run in parallel with it, but sequence them for deterministic merge-commit history).

### W-M05 Subtree-merge `racetag-frontend`
- **ID:** `W-M05`
- **Role:** BACKEND
- **Priority:** P0
- **Goal:** Import the frontend repo into `apps/frontend/`.
- **Acceptance criteria:** as W-M03, for frontend; `apps/frontend/index.html` is present.
- **Dependencies:** `W-M02`.

### W-M06 Lift cross-cutting docs and compose into monorepo root
- **ID:** `W-M06`
- **Role:** DOCS
- **Priority:** P0
- **Goal:** Move `ARCHITECTURE.md`, `ISSUES.md`, `PLAN.md`, `PACKAGING.md` from `/Users/jan/Documents/git/racetag/` into the new monorepo under `docs/`. Seed a monorepo-root `docker-compose.yml` that builds each app from its `apps/*/` subdirectory (supersedes the three per-repo compose files).
- **Repo/files:**
  - `docs/ARCHITECTURE.md`, `docs/ISSUES.md`, `docs/PLAN.md`, `docs/PACKAGING.md`, `docs/MONOREPO.md`.
  - `docker-compose.yml` (root) with three services: `reader-service` (context `./apps/reader-service`), `backend` (`./apps/backend`), `frontend` (`./apps/frontend`).
- **Acceptance criteria:** `docker compose up --build` from monorepo root starts all three services; docs render from their new location.
- **Dependencies:** `W-M03`, `W-M04`, `W-M05`.

### W-M07 Update pre-existing per-repo `docker-compose.yml` references
- **ID:** `W-M07`
- **Role:** BACKEND + DOCS
- **Priority:** P1
- **Goal:** Collapse the three per-repo compose files (inherited from the source repos) into the single root compose file, adjusting `build.context` paths accordingly. Each legacy per-repo compose file is replaced with a one-line stub pointing up to the root.
- **Acceptance criteria:** No duplicate service definitions; `docker compose config` from root validates.
- **Dependencies:** `W-M06`.

### W-M08 Archive the three source repos as read-only
- **ID:** `W-M08`
- **Role:** DOCS
- **Priority:** P2
- **Goal:** Once the monorepo is authoritative, mark the three `paclema/racetag-*` repos as archived on GitHub (or add a banner README pointing to the new repo). Prevents drift.
- **Acceptance criteria:** Each source repo's README has a top-of-file "moved to <monorepo url>" banner. GitHub archived-flag is set if the user owns them (otherwise, note open.)
- **Dependencies:** `W-M06`.

> **From here on, all "Repo/files" references below mean paths inside the monorepo at `/Users/jan/Documents/git/racetag/racetag/`.** The legacy three-repo path is preserved in square brackets after each such reference for traceability during the migration window.

---

## Phase 0 — Workspace preparation

### W-000 Top-level workspace
- **ID:** `W-000`
- **Role:** DOCS
- **Priority:** P1
- **Goal:** Provide a single top-level `README.md` and `docker-compose.yml` at the monorepo root (`racetag/`) that orchestrate all three apps for development.
- **Repo/files (monorepo):**
  - create `racetag/README.md` — lists the three apps under `apps/` with a paragraph each, points at `docs/ARCHITECTURE.md`.
  - create `racetag/docker-compose.yml` that declares three services built from `./apps/backend`, `./apps/reader-service`, `./apps/frontend`.
- **Acceptance criteria:** `docker compose up --build` from the monorepo root starts all three containers; the frontend loads and fetches the backend.
- **Dependencies:** `W-M06` (monorepo and initial docker-compose scaffold must already exist).

---

## Phase 1 — Fix the correctness bugs (P0)

### W-001 Per-antenna presence set in reader service
- **ID:** `W-001`
- **Role:** BACKEND
- **Priority:** P0
- **Goal:** Track tag presence per-antenna so that depart-from-one-antenna does not clear presence when another antenna still sees the tag.
- **Repo/files (monorepo):**
  - `apps/reader-service/src/tag_tracker.py` [was `racetag-reader-service/src/tag_tracker.py`] — change `present: set[str]` to `present: dict[str, set[int]]`. Replace `mark_present(tag)` with `mark_present(tag, antenna)` → returns `True` iff the per-tag antenna set transitioned from empty to non-empty. Replace `mark_absent(tag)` with `mark_absent(tag, antenna)` → removes the antenna from the set; returns `True` iff the set became empty.
  - `apps/reader-service/src/sirit_client.py:159-175` [was `racetag-reader-service/src/sirit_client.py:159-175`] — pass `ev.antenna` (default `0` when unknown) into the tracker calls, use the boolean returns to decide whether to forward `arrive` / `depart`.
- **Acceptance criteria:**
  - Unit test `test_tag_tracker_multi_antenna` passes (see `W-020`): feeding `arrive ant=1, arrive ant=2, depart ant=1, depart ant=2` results in exactly one emitted `arrive` and one emitted `depart`.
  - Unit test `test_tag_tracker_overlap_race` (the exact scenario from ISSUES P0-1): `arrive ant=1, depart ant=1, arrive ant=2, depart ant=2` where the events interleave such that the middle depart fires before the late arrive — still exactly one emitted `arrive`.
- **Dependencies:** `W-M06` (monorepo must exist).

### W-002 Per-tag minimum lap-interval cooldown in reader service
- **ID:** `W-002`
- **Role:** BACKEND
- **Priority:** P0
- **Goal:** Even with correct per-antenna gating, enforce a hard minimum time between consecutive forwarded `arrive` events for the same tag.
- **Repo/files (monorepo):**
  - `apps/reader-service/src/tag_tracker.py` — add `last_emitted_at: dict[str, float]` and a constructor arg `min_lap_interval_s: float`. `mark_present` returns `False` if `time.monotonic() - last_emitted_at[tag] < min_lap_interval_s`.
  - `apps/reader-service/src/racetag_reader_service.py` — new CLI flag `--min-lap-interval` (env `MIN_LAP_INTERVAL_S`, default `10.0`).
  - `apps/reader-service/src/sirit_client.py` — pass the value through to `TagTracker(min_lap_interval_s=...)`.
  - `apps/reader-service/.env.example` — add `MIN_LAP_INTERVAL_S=10`.
- **Acceptance criteria:** Unit test feeding two arrives 3 s apart (with `min_lap_interval_s=10`) yields exactly one emitted arrive.
- **Dependencies:** `W-001`.

### W-003 Defence-in-depth: minimum-interval check in backend
- **ID:** `W-003`
- **Role:** BACKEND
- **Priority:** P0
- **Goal:** Even if a misbehaving reader service forwards duplicates, the backend must not count two laps inside `min_pass_interval_s`.
- **Repo/files (monorepo):**
  - `apps/backend/racetag-backend/domain/race.py` — add `min_pass_interval_s: float = 8.0` to `RaceState.__init__`; modify `add_lap` to early-return the unchanged `Participant` if the new `pass_time_iso` is within `min_pass_interval_s` of `p.last_pass_time`.
  - `apps/backend/racetag-backend/app.py` — env var `RACE_MIN_PASS_INTERVAL_S`, default `8.0`, wired to `RaceState(...)`.
  - `apps/backend/.env.example` — document it.
- **Acceptance criteria:** Domain test: posting two arrives for the same tag 3 s apart results in `laps == 1`. Posting them 12 s apart results in `laps == 2`.
- **Dependencies:** `W-M06` (can run in parallel with W-001/W-002 once monorepo exists).

---

## Phase 2 — Tag-to-rider coupling (P0 UX)

### W-010 Rider domain + storage
- **ID:** `W-010`
- **Role:** BACKEND
- **Priority:** P0
- **Goal:** Introduce a `Rider` entity so tags can be coupled to bib+name and the association survives restart.
- **Repo/files (monorepo):**
  - `apps/backend/racetag-backend/domain/riders.py` (new) — `Rider` pydantic model `{tag_id, bib, name, created_at}` + `RiderStore` with `upsert/get/list/delete`. For MVP the store is in-memory; swap for SQLite in `W-050`.
  - `apps/backend/openapi.yaml` — add `RiderDTO`, `RiderCreateDTO`, and endpoints:
    - `POST /riders` (request: `RiderCreateDTO`, response: `RiderDTO`).
    - `GET /riders` (response: `{count, items: RiderDTO[]}`).
    - `DELETE /riders/{tag_id}`.
  - `apps/backend/racetag-backend/models_api.py` — regenerate via `datamodel-codegen`.
  - `apps/backend/racetag-backend/app.py` — wire the new endpoints to `RiderStore`.
  - Extend `ParticipantDTO` to include `bib?: string`, `name?: string`; populate from `RiderStore` in `RaceState.standings()` (pass the store in) or at the DTO-mapping layer in `app.py`.
- **Acceptance criteria:** OpenAPI validates, CRUD works via `curl`, standings returned from `GET /classification` include `bib` and `name` when a rider is registered.
- **Dependencies:** `W-M06`.

### W-011 Unknown-tag live stream event
- **ID:** `W-011`
- **Role:** BACKEND
- **Priority:** P0
- **Goal:** When an `arrive` is received for a `tag_id` that has no `Rider` registered, broadcast a new SSE event so the frontend can offer immediate registration.
- **Repo/files (monorepo):**
  - `apps/backend/racetag-backend/app.py:71-105` — after pushing `lap` and `standings`, if `tag_id not in rider_store`, also push `{"type":"unknown_tag","tag_id":...,"timestamp":...,"antenna":...,"rssi":...}`.
  - Include a small ring-buffer (`recent_unknown_tags`, max 50) so a late-joining frontend can `GET /riders/recent-reads?limit=10`.
  - Add endpoint `GET /riders/recent-reads?limit=10` to expose it.
- **Acceptance criteria:**
  - Sending an arrive for an unregistered tag produces an `unknown_tag` SSE frame (asserted via `TestClient` or curl).
  - `GET /riders/recent-reads` returns the last 10 unknown tag reads in reverse-chronological order.
- **Dependencies:** `W-010`.

### W-012 Frontend: “Register rider for last read” UX
- **ID:** `W-012`
- **Role:** FRONTEND
- **Priority:** P0
- **Goal:** Build the fast-register flow described in `ISSUES.md` P0-2.
- **Repo/files (monorepo):**
  - `apps/frontend/index.html` — add a "Register rider" section in the header: a button labelled **"Couple tag → rider"**, and a modal (hidden by default) with `tag_id` (readonly, pre-filled), `bib` (number), `name` (text), `Save` / `Cancel`.
  - `apps/frontend/script.js`:
    - New state flags: `state.awaitingRead: bool`, `state.lastUnknownTag: {tag_id, timestamp} | null`.
    - Button handler: set `state.awaitingRead = true`; status indicator says “Hold a tag near the antenna…”. If a recent unknown tag already exists in the ring buffer, open the modal immediately.
    - In the SSE message handler, add a new branch: `if data.type === 'unknown_tag'` and `state.awaitingRead`, open the modal with `data.tag_id`.
    - On Save: `POST ${state.backend}/riders` with body `{tag_id, bib, name}`. On success: close modal, toast (a small inline div at the bottom) “Registered bib {bib} – {name}”, clear `awaitingRead`.
    - On Cancel: close modal, clear `awaitingRead`.
  - `apps/frontend/styles.css` — modal styling (centred, backdrop, 320 px wide, consistent with existing dark theme).
- **Acceptance criteria:**
  - Happy path: cold-start the app → click **Couple tag → rider** → hold a tag → modal appears in ≤1 s with the tag id → type bib+name → hit Enter → toast appears → standings table’s row for that tag now shows bib/name without a reload.
  - Cancel path: clicking Cancel closes the modal, no POST fires, no further modals open until the button is pressed again.
  - Backend auth misconfig: if the POST returns 401, an error toast is shown and the modal stays open with a retry button.
- **Dependencies:** `W-010`, `W-011`.

### W-013 Bulk CSV import uses the /riders API
- **ID:** `W-013`
- **Role:** FRONTEND
- **Priority:** P1
- **Goal:** Replace the browser-only CSV map with server-side persistence via `POST /riders`.
- **Repo/files (monorepo):** `apps/frontend/script.js:24-70` — after parsing the CSV, POST each row to `/riders`. Show a progress line "Imported k/N riders". On failure, accumulate errors and show a summary.
- **Acceptance criteria:** Importing the sample `apps/frontend/docs/tags_example.csv` (or shared `docs/tags_example.csv` after W-M06) registers four riders in the backend, visible at `GET /riders`.
- **Dependencies:** `W-010`.

---

## Phase 3 — Robustness & operational quality (P1)

### W-020 Unit tests for `TagTracker` (the multi-antenna fix)
- **ID:** `W-020`
- **Role:** QA
- **Priority:** P0 (pair with W-001)
- **Goal:** Guard the sensor-fusion logic with tests.
- **Repo/files (monorepo):** create `apps/reader-service/tests/test_tag_tracker.py` using `pytest` + `freezegun` (or a fake clock injected via constructor).
- **Tests (minimum):**
  - `test_single_antenna_single_pass` — one arrive, one depart → one arrive emitted, one depart emitted.
  - `test_multi_antenna_single_pass` — two arrives + two departs across two antennas → one arrive, one depart.
  - `test_multi_antenna_overlap_race_condition` — depart from early antenna arrives before arrive from late antenna — exactly one arrive.
  - `test_min_lap_interval_blocks_second_pass` — second arrive within cooldown is swallowed.
  - `test_min_lap_interval_allows_after_cooldown` — second arrive after cooldown is emitted.
- **Acceptance criteria:** All tests pass; CI runs them on every PR. Target: a GitHub Action at `.github/workflows/reader-tests.yml` (at monorepo root, scoped to `apps/reader-service/**` path-filter).
- **Dependencies:** `W-001`, `W-002`.

### W-021 Domain tests for `RaceState.add_lap`
- **ID:** `W-021`
- **Role:** QA
- **Priority:** P1
- **Goal:** Lock down lap-counting, finishing, and ordering.
- **Repo/files (monorepo):** `apps/backend/tests/test_race.py`.
- **Tests:** finish on Nth lap exactly; same-lap ordering by earlier last_pass_time; gap_ms for same-lap; min_pass_interval swallows duplicates (W-003); standings stable across many participants.
- **Acceptance criteria:** All tests pass under `pytest`.
- **Dependencies:** `W-003`.

### W-022 API tests with FastAPI TestClient
- **ID:** `W-022`
- **Role:** QA
- **Priority:** P1
- **Goal:** Contract tests for each endpoint including SSE.
- **Repo/files (monorepo):** `apps/backend/tests/test_api.py`.
- **Tests:** `POST /events/tag/batch` round-trips the `events_processed` counter; `GET /classification` reflects posted events; `POST /riders` followed by `GET /riders/{tag_id}` returns the created rider; unknown-tag SSE fires.
- **Acceptance criteria:** Passes under `pytest`; a session log fixture (`apps/reader-service/logs/session.log`) can be replayed and standings produced match a known snapshot.
- **Dependencies:** `W-010`, `W-011`.

### W-023 Replay-the-log contract test
- **ID:** `W-023`
- **Role:** QA
- **Priority:** P1
- **Goal:** Use the captured session log (`apps/reader-service/logs/session.log`) as a test fixture: replay its arrive/depart sequence through the reader service → backend and assert a known standings snapshot.
- **Repo/files (monorepo):** `apps/reader-service/tests/test_replay.py` — parse the log, feed it through `SiritClient._handle_message` with a `MockBackendClient`, then check the `events` list.
- **Acceptance criteria:** Passes; acts as regression for P0-1 and P1-1 combined.
- **Dependencies:** `W-001`, `W-002`, `W-011`.

### W-030 Reader-supplied timestamps are authoritative; UTC end-to-end, local-at-display
- **ID:** `W-030`
- **Role:** BACKEND + FRONTEND
- **Priority:** P1
- **Goal:** Use `first`/`last` from the reader as the event `timestamp` instead of Python wall clock, and enforce the project-wide timezone policy.
- **Timezone policy (decided 2026-04-15):**
  - **Reader service** converts incoming reader timestamps to UTC **at the source** (the reader itself is reconfigured to run in UTC so no conversion is strictly needed; any `first`/`last` that still arrive in a non-UTC form are normalised to UTC before emission).
  - **Backend** stores and emits only UTC ISO-8601 timestamps (`Z`-suffixed). No timezone arithmetic in the backend.
  - **Frontend** converts UTC to the **browser's local timezone** for display. The default display zone is `Europe/Berlin`, but the actual zone is driven by the browser's `Intl.DateTimeFormat().resolvedOptions().timeZone` — no hard-coding.
- **Repo/files (monorepo):**
  - `apps/reader-service/src/sirit_client.py:280-298` — if `kv["first"]` (arrive) or `kv["last"]` (depart) is present, parse it (supporting both naive and `Z`-suffixed forms, assume configured timezone → UTC), use as `timestamp`. Fall back to `_now_iso()` (UTC) if missing.
  - `apps/reader-service/src/init_commands:5` — change `info.time_zone=Europe/Berlin` to `info.time_zone=UTC`.
  - `apps/reader-service/src/utils.py` — add `parse_reader_time(s: str) -> str` returning ISO8601 UTC.
  - `apps/frontend/script.js` — introduce a single `formatTimestampForDisplay(iso_utc)` helper using `new Date(iso_utc).toLocaleTimeString(undefined, { hour12: false, timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone })`. All table renderings of `last_pass_time` go through it.
- **Acceptance criteria:**
  - Fed a reader line with `first=2026-04-15T15:15:04.403`, the resulting `TagEvent.timestamp` equals `2026-04-15T15:15:04.403Z` (after UTC reader config).
  - Backend persistence and every SSE payload emits UTC-only timestamps (`Z`-suffixed) — verified by a test that asserts every string with format `/\d{4}-\d{2}-\d{2}T/` in a sample API response ends with `Z` or `+00:00`.
  - Frontend rendered in a browser set to `Europe/Berlin` shows `17:15:04` (DST) / `16:15:04` (winter) for the above UTC timestamp; switching the browser/OS timezone to `America/Los_Angeles` and refreshing shows the correct LA-local equivalent without any backend change.
- **Dependencies:** `W-M06`.

### W-031 Reconnect / retry in HTTP backend client
- **ID:** `W-031`
- **Role:** BACKEND
- **Priority:** P1
- **Goal:** Stop dropping events on the first POST failure.
- **Repo/files (monorepo):** `apps/reader-service/src/backend_client/http.py:50-97` — on POST failure put the batch back at the head of an internal retry queue; exponential backoff (200, 500, 1000 ms) up to 3 attempts; after that, append to a local JSONL spool at `logs/spool.jsonl`. On startup, drain the spool first.
- **Acceptance criteria:** With backend down, 10 events fired are not lost; once the backend returns, they are delivered in order.
- **Dependencies:** none.

### W-032 Async SSE with asyncio.Queue
- **ID:** `W-032`
- **Role:** BACKEND
- **Priority:** P1
- **Goal:** Replace the blocking `time.sleep(1)` per-client generator with a proper async consumer.
- **Repo/files (monorepo):** `apps/backend/racetag-backend/app.py:64-151`.
- **Acceptance criteria:** Connecting two SSE clients + posting 100 events delivers all 100 to both clients with no perceptible lag; `/stream` endpoint is `async def`; load test with `wrk` against `/stream` no longer starves other endpoints.
- **Dependencies:** none.

### W-033 `reader_serial` optional in OpenAPI + regenerate models
- **ID:** `W-033`
- **Role:** BACKEND
- **Priority:** P1
- **Goal:** Stop rejecting early events that arrive before `info.serial_number` replies.
- **Repo/files (monorepo):** `apps/backend/openapi.yaml` line 67 — remove `reader_serial` from `required`; regenerate `models_api.py`.
- **Acceptance criteria:** Posting a batch without `reader_serial` returns 200.
- **Dependencies:** none.

### W-034 Frontend SSE auto-reconnect
- **ID:** `W-034`
- **Role:** FRONTEND
- **Priority:** P1
- **Goal:** The polyfill reconnects with exponential backoff on error.
- **Repo/files (monorepo):** `apps/frontend/api.js:13-53` — wrap the connect logic in a `scheduleReconnect(delayMs)`; on open reset delay to 1 s, on error double up to 15 s. Surface state (`Reconnecting in 2 s…`) in the `#status` div.
- **Acceptance criteria:** Kill the backend, the frontend shows `Reconnecting…`; restart it, the frontend reconnects within 15 s without a page refresh.
- **Dependencies:** none.

### W-035 Robust CSV parsing in frontend
- **ID:** `W-035`
- **Role:** FRONTEND
- **Priority:** P2
- **Goal:** Handle BOM, quoted commas, CRLF.
- **Repo/files (monorepo):** `apps/frontend/script.js:24-52` — minimal in-place CSV tokenizer, no dependency.
- **Acceptance criteria:** `docs/tags_example.csv` works; synthetic CSVs with BOM, quoted commas, and CRLF line endings also work.
- **Dependencies:** `W-013` (to avoid churn).

### W-036 Frontend: race reset and total-laps controls
- **ID:** `W-036`
- **Role:** FRONTEND + BACKEND
- **Priority:** P2
- **Goal:** UI controls for “Reset race” and “Set total laps”.
- **Repo/files (monorepo):**
  - Backend (`apps/backend/racetag-backend/app.py`): `POST /race/reset` (empties `RaceState.participants` and clears persistence), `PATCH /race` body `{total_laps}`.
  - Frontend (`apps/frontend/index.html` + `script.js`): header buttons + confirm dialog for reset; numeric input + Apply for total laps.
- **Acceptance criteria:** Clicking Reset empties the standings table; changing total laps updates both the backend DTO and the “Finished” threshold immediately.
- **Dependencies:** `W-050`.

### W-040 Remove API key auth in the packaged app
- **ID:** `W-040`
- **Role:** BACKEND + FRONTEND
- **Priority:** P1
- **Goal:** Per the user’s direction (“security is not a priority”), eliminate the auth tangle that today silently breaks the frontend.
- **Repo/files (monorepo):**
  - `apps/backend/racetag-backend/app.py` — if `RACETAG_API_KEY` is unset, skip the global dependency (already implemented). Make the default build **not set** the variable.
  - `apps/backend/Dockerfile` — document that `RACETAG_API_KEY` is off by default for the packaged desktop build.
  - `apps/frontend/api.js` and `apps/frontend/script.js` — keep the placeholder plumbing for compatibility but default to no header if the placeholder is not substituted.
  - `apps/frontend/docker-entrypoint.sh` — emit a warning to stderr when `RACETAG_FRONTEND_API_KEY` is empty.
- **Acceptance criteria:** Out-of-the-box packaged app works end-to-end without anyone setting a key; setting the key in an env file still enables it.
- **Dependencies:** none.

### W-050 SQLite persistence for backend (synchronous=FULL durability)
- **ID:** `W-050`
- **Role:** BACKEND
- **Priority:** P1
- **Goal:** Survive restart with full race state and rider registrations, with strict durability suitable for a race-timing workload.
- **Durability policy (decided 2026-04-15):** Open the database with `PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;`. `FULL` (not `NORMAL`) is chosen because in a race context a crash or sudden power loss between a tag passing and the WAL checkpoint would lose the authoritative finish time; the ~1 ms/event fsync cost is well within budget for an event rate dominated by 100–200 ms reader quiet periods and per-lap `min_pass_interval` gating.
- **Repo/files (monorepo):**
  - `apps/backend/racetag-backend/storage.py` (new) — `sqlite3` wrapper. On connection open: execute `PRAGMA journal_mode=WAL;`, `PRAGMA synchronous=FULL;`, `PRAGMA foreign_keys=ON;`. Two tables (`riders`, `tag_events`), idempotent DDL. Every `INSERT`/`UPDATE` runs inside an explicit transaction committed with the default durable commit (no `synchronous=OFF` escape hatches).
  - `RaceState` — on construction, replay `tag_events` via `add_lap`.
  - `RiderStore` — backed by SQLite.
  - `apps/backend/.env.example` — `RACETAG_DATA_DIR` (default `./data`).
- **Acceptance criteria:**
  - Post 10 events, restart, `GET /classification` still returns the same standings. Register 5 riders, restart, `GET /riders` still returns them.
  - `PRAGMA synchronous;` on the live DB returns `2` (= `FULL`).
  - Benchmark note recorded in `docs/ARCHITECTURE.md`: single-event write latency on an SSD-backed host stays under 5 ms p99 (~1 ms of which is the fsync attributable to `synchronous=FULL`). Race event rate is well below the 1 write / 1 ms ceiling this implies, so the trade-off is accepted.
  - Kill-test: `kill -9` of the backend immediately after a `POST /events/tag/batch` 200 response leaves the event present in the DB after restart. (Cannot be automated portably; captured as a manual QA step in `W-023` or a dedicated smoke test.)
- **Dependencies:** `W-010`, `W-M06`.

### W-051 Diagnostics endpoint for antenna reads
- **ID:** `W-051`
- **Role:** BACKEND + FRONTEND
- **Priority:** P2
- **Goal:** Help the operator position antennas on a new course.
- **Repo/files (monorepo):**
  - Backend (`apps/backend/racetag-backend/app.py`): `GET /diagnostics/antennas?window_s=60` → `{antenna_id: reads_count}`.
  - Frontend (`apps/frontend/script.js` + `index.html`): small panel (collapsed by default) showing the counts with a 5 s refresh.
- **Acceptance criteria:** With one tag passing under antenna 1 and none on antenna 2, the panel shows `1: N, 2: 0`.
- **Dependencies:** `W-050` (persistence makes this easy; without it, keep an in-memory ring).

### W-060 Unified logging via `logging` in reader service
- **ID:** `W-060`
- **Role:** BACKEND
- **Priority:** P2
- **Goal:** Replace `print()` with `logging`; TTY-detecting ANSI.
- **Repo/files (monorepo):** touch every file in `apps/reader-service/src/`. One `get_logger()` helper in `utils.py`.
- **Acceptance criteria:** `reader-service` binary logs INFO to stdout and DEBUG to `logs/reader.log` when `--debug` is set.
- **Dependencies:** none.

### W-061 Reset session bind on reader reconnect
- **ID:** `W-061`
- **Role:** BACKEND
- **Priority:** P2
- **Goal:** Handle reader reboots gracefully.
- **Repo/files (monorepo):** `apps/reader-service/src/sirit_client.py:150-157` — when `event.connection id = N` is received and `N != self.session.id`, reset `self.session.id`, `self.session.bound = False`, then run `_maybe_bind_and_config()`.
- **Acceptance criteria:** Rebooting the reader does not require restarting the service.
- **Dependencies:** none.

---

## Phase 4 — Packaging (owned by `PACKAGING.md`)

### W-070 Unified desktop app skeleton (pywebview + bundled FastAPI + static frontend)
- **ID:** `W-070`
- **Role:** BACKEND
- **Priority:** P0 (this is the user-visible deliverable)
- **Goal:** Produce a single Python entry-point that (a) starts uvicorn serving the FastAPI backend in a thread, (b) serves the static frontend files, and (c) opens a pywebview window pointing at the local URL. See `docs/PACKAGING.md` for full design.
- **Repo/files (monorepo):** new top-level subdir `apps/desktop/`. File: `apps/desktop/app.py` (~80 lines). Resource copies under `apps/desktop/resources/frontend/` and `apps/desktop/resources/reader/` are produced by the spec files (W-071/W-072).
- **Acceptance criteria:** `python apps/desktop/app.py` on macOS or Windows opens a native window titled **"Racetag"** with the frontend served and the backend responding at an internal port.
- **Dependencies:** `W-010`, `W-012`, `W-050`, `W-M06` (so the packaged app is actually usable and the monorepo layout exists).

### W-M09 Source or design the Racetag icon set (prerequisite for packaging)
- **ID:** `W-M09`
- **Role:** DOCS (coordination + design)
- **Priority:** P0 (blocks W-071 and W-072)
- **Goal:** Produce a complete, multi-resolution icon set for the Racetag desktop app on both platforms. The user has **not** provided artwork yet; this work item exists to surface that dependency explicitly.
- **Deliverables:**
  - Source artwork: a square SVG or a ≥1024×1024 PNG with transparent background, named `apps/desktop/icons/racetag-source.(svg|png)`.
  - `apps/desktop/icons/racetag.icns` — macOS icon set containing 16, 32, 64, 128, 256, 512, and 1024 px variants (plus `@2x` where relevant). Generate via `iconutil -c icns` from a `racetag.iconset/` directory of scaled PNGs.
  - `apps/desktop/icons/racetag.ico` — Windows icon containing 16, 32, 48, 64, 128, 256 px variants. Generate with ImageMagick (`magick convert 16.png 32.png 48.png 64.png 128.png 256.png racetag.ico`) or equivalent.
- **Sourcing options (pick one with the user):**
  1. **User supplies artwork.** Simplest — drop it into `apps/desktop/icons/racetag-source.*` and run the conversion scripts.
  2. **In-house draft.** Commission a minimal mark (e.g. a stylised race-tag + flag motif) via a designer or a tool like Figma/Inkscape.
  3. **AI-generated placeholder.** Acceptable for a prototype; replace before any public release.
- **Acceptance criteria:**
  - Both `racetag.icns` and `racetag.ico` exist in the monorepo and are referenced by the PyInstaller specs (`W-071`, `W-072`).
  - Opening `Racetag.app` on macOS shows the icon in the Dock; running `Racetag.exe` on Windows shows the icon in the taskbar and Explorer.
  - A note in `docs/PACKAGING.md` records which sourcing option was chosen and, if placeholder, when it must be replaced.
- **Dependencies:** `W-M02` (needs the monorepo) and a decision from the user on which sourcing option to take — this work item will **stall** until that decision is recorded.

### W-071 macOS `.app` bundle via PyInstaller — branded "Racetag"
- **ID:** `W-071`
- **Role:** BACKEND
- **Priority:** P0
- **Goal:** Produce `Racetag.app` — a double-clickable mac bundle that includes Python, FastAPI, uvicorn, pywebview, the static frontend assets, and a bundled `reader-service` subprocess.
- **Branding (decided 2026-04-15):**
  - **Product name:** `Racetag` (exact case — used in `CFBundleName`, window title, plist, and all user-facing strings).
  - **Bundle identifier:** `com.racetag.app`.
  - **Version stamp:** required on every build. Single source of truth is `apps/desktop/VERSION` (plain text, e.g. `0.1.0`). Read at build time into `CFBundleShortVersionString` / `CFBundleVersion` and at runtime for the "About" dialog. CI embeds the git SHA into the build metadata (`0.1.0+<shortsha>`).
  - **Icon set:** required. See **W-M09** below (icon-sourcing prerequisite). This work item is **blocked** on W-M09 producing the `.icns` file.
- **Repo/files (monorepo):** `apps/desktop/pyinstaller.mac.spec` (mac target), `apps/desktop/VERSION`, `apps/desktop/icons/racetag.icns` (delivered by W-M09).
- **Acceptance criteria:** Unzipping the release artefact on a clean macOS 14+ machine, double-clicking the app, results in a visible window titled **"Racetag"**, with the proper icon in the Dock and Finder "Get Info" showing the correct version string. End-to-end workflow works given a real reader on the LAN.
- **Dependencies:** `W-070`, `W-M09`.

### W-072 Windows single EXE via PyInstaller — branded "Racetag"
- **ID:** `W-072`
- **Role:** BACKEND
- **Priority:** P0
- **Goal:** Produce `Racetag.exe` — one-file or one-directory, signed if possible.
- **Branding:**
  - **Product name:** `Racetag`. Exe internal metadata (`FileDescription`, `ProductName`, `InternalName`) all say `Racetag`.
  - **Version stamp:** embedded via PyInstaller `version_file` pointing at a generated `apps/desktop/win_version_info.txt` built from `apps/desktop/VERSION`.
  - **Icon:** required. See **W-M09** — delivers `apps/desktop/icons/racetag.ico` multi-resolution (16, 32, 48, 64, 128, 256). Blocks this item.
- **Repo/files (monorepo):** `apps/desktop/pyinstaller.win.spec` (win target), `apps/desktop/win_version_info.txt`, `apps/desktop/icons/racetag.ico` (delivered by W-M09), `.github/workflows/release.yml` (at monorepo root).
- **Acceptance criteria:** A Windows 11 machine without Python installed can run the exe; Properties > Details shows `ProductName = Racetag` and the correct version; Explorer shows the taskbar icon; the reader service is bundled inside; ICS bridging instructions from the reader-service README still apply.
- **Dependencies:** `W-070`, `W-M09`.

### W-073 Bundle reader-service as subprocess
- **ID:** `W-073`
- **Role:** BACKEND
- **Priority:** P0
- **Goal:** Integrate the currently-separate reader service into the desktop app so the operator does not need a second process.
- **Repo/files (monorepo):** `apps/desktop/app.py` — on startup, spawn `racetag_reader_service.main(...)` in a subprocess (not a thread — PyInstaller + threads + sockets are tricky) wired to the internal backend URL. Expose a small settings panel in the frontend to set `READER_IP`.
- **Acceptance criteria:** Starting the packaged app is enough to have the reader service running and delivering events.
- **Dependencies:** `W-070`, `W-071`, `W-072`.

### W-074 Settings panel in frontend for reader IP and total laps
- **ID:** `W-074`
- **Role:** FRONTEND
- **Priority:** P1
- **Goal:** Because there is no `.env` in a packaged app, expose `READER_IP`, `MIN_LAP_INTERVAL_S`, `RACE_TOTAL_LAPS` in a settings modal that writes to a config file on disk (via a new `PATCH /config` endpoint).
- **Repo/files (monorepo):**
  - Backend (`apps/backend/racetag-backend/app.py`): `GET/PATCH /config` with a small `Config` model; persisted to `${RACETAG_DATA_DIR}/config.json`.
  - Frontend (`apps/frontend/index.html` + `script.js`): Settings modal + icon in header.
- **Acceptance criteria:** Changing the reader IP in the modal restarts the reader subprocess and the new IP is shown in the connection status.
- **Dependencies:** `W-073`.

### W-075 Release workflow in GitHub Actions
- **ID:** `W-075`
- **Role:** BACKEND (+ docs)
- **Priority:** P1
- **Goal:** On a tagged push, build both platforms and attach the artefacts to the release.
- **Repo/files (monorepo):** `.github/workflows/release.yml` (at monorepo root; macOS + Windows runners), uploads `Racetag-<ver>-mac.zip` and `Racetag-<ver>-win.zip`. Version `<ver>` is read from `apps/desktop/VERSION`.
- **Acceptance criteria:** Pushing `v0.1.0` produces a release page with two downloadable binaries.
- **Dependencies:** `W-071`, `W-072`.

---

## Phase 5 — Documentation (DOCS)

### W-080 Root README update
- **ID:** `W-080`
- **Role:** DOCS
- **Priority:** P1
- **Goal:** Mirror the new architecture, packaging, and workflows at the monorepo root.
- **Repo/files (monorepo):** `README.md` at monorepo root.
- **Acceptance criteria:** A new contributor can bring the dev stack up in ≤5 minutes using the README alone.
- **Dependencies:** `W-000`, `W-M06`.

### W-081 Per-app README refresh
- **ID:** `W-081`
- **Role:** DOCS
- **Priority:** P2
- **Goal:** Reflect the new APIs (`/riders`, `/riders/recent-reads`, `unknown_tag` SSE), the cooldown env var, the persistence directory, the UTC timezone policy, and how the three apps plug together in the desktop build.
- **Repo/files (monorepo):** `apps/reader-service/README.md`, `apps/backend/README.md`, `apps/frontend/README.md`, `apps/desktop/README.md`.
- **Acceptance criteria:** READMEs no longer refer to removed flags, endpoints, or Bearer auth; they reference the monorepo root commands (e.g. `docker compose up` from root, not per-repo).
- **Dependencies:** items in phase 2 and 3 being merged; `W-M06`.

### W-082 Operator guide
- **ID:** `W-082`
- **Role:** DOCS
- **Priority:** P2
- **Goal:** A non-developer field guide: cabling, ICS bridge on Windows, setting reader IP, mounting antennas, starting a race.
- **Repo/files (monorepo):** `docs/OPERATOR_GUIDE.md`.
- **Acceptance criteria:** A race marshal with no prior exposure can set up the system in < 30 minutes.
- **Dependencies:** `W-074`.

---

## Cross-cutting risk register

| Risk | Mitigation |
| --- | --- |
| PyInstaller + pywebview + uvicorn three-way conflict on macOS signing/notarisation | Prototype W-071 **first**, before investing in W-073/W-074. |
| Reader firmware differences (Sirit vs. Invelion) produce different event formats | Keep `_parse_event_message` reader-agnostic; add Invelion adapter later. Not in scope of this plan. |
| Single-process SQLite with multiple subprocess readers could race on writes | The backend is the only writer; reader service talks HTTP. One writer, no contention. |
| Losing the auto-reconnect polyfill behaviour across browsers (W-034) | Use a bare MDN-style SSE reader test page under `tests/manual/`. |

---

## Resolved questions (2026-04-15)

1. **Timezone policy.** ✅ Resolved. Reader → UTC at source; backend stores UTC; frontend converts to browser-local (`Europe/Berlin` by default, but driven by the browser's resolved timezone). Baked into `W-030`.
2. **Desktop app name and icon.** ✅ Partially resolved. Product name is `Racetag`. Version stamp is required (source: `apps/desktop/VERSION`). Icon set is **required but not yet sourced** — captured as the blocker `W-M09`.
3. **Single `racetag` monorepo.** ✅ Resolved — in scope for this plan. Consolidation captured in new **Phase M** (`W-M00` … `W-M08`). All downstream work items retargeted to `apps/<name>/` monorepo paths.
4. **Persistence durability.** ✅ Resolved. `PRAGMA synchronous=FULL` chosen; ~1 ms/event cost accepted. Baked into `W-050`.

## Remaining open questions

- **GitHub username** for the new monorepo — blocks `W-M00` (user to provide).
- **Icon sourcing route** — blocks `W-M09` (user supplies art, commissions design, or accepts AI placeholder).
