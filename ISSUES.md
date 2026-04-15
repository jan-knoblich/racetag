# Racetag — Catalogued Issues

_Date: 2026-04-15_
_Prepared by: Agent 1 — The Architect_

Each issue has a **severity** (P0 blocker, P1 important, P2 nice-to-have), a **where** (exact file:line), a **root cause**, and a **proposed fix**. Fixes are restated as atomic work items in `PLAN.md`.

---

## P0-1 — Multi-antenna double (triple, quadruple) lap counting

**User report (German):** _„oder die mehreren antennen haben dann mehrere runden pro lesung gezählt“_ — when multiple antennas are in use, a single rider passing is counted as several laps.

**Where:** `racetag-reader-service/src/tag_tracker.py` + `racetag-reader-service/src/sirit_client.py:159-175`.

**Root cause (step-by-step):**

The Sirit reader emits one `event.tag.arrive` **per antenna-first-sight** and one `event.tag.depart` **per antenna after `depart_time` ms of silence on that antenna** (see `docs/Sirit INfinity 510/Useful_commands.md` and the Protocol Reference). So when a rider with tag `T` crosses a line covered by antennas 1 and 2:

```
event.tag.arrive tag_id=T, antenna=1  (at t=0)
event.tag.arrive tag_id=T, antenna=2  (at t=60 ms)
event.tag.depart tag_id=T, antenna=1  (at t=410 ms)  <-- after 300 ms quiet
event.tag.depart tag_id=T, antenna=2  (at t=470 ms)
```

Look at how the service gates this in `sirit_client.py`:

```python
if "event.tag.arrive" in low:
    ev = self._parse_event_message("arrive", msg)
    if ev and self.tags.mark_present(ev.tag_id):
        ...
        self._emit_event(ev)       # arrive forwarded
    return
if "event.tag.depart" in low:
    ev = self._parse_event_message("depart", msg)
    if ev:
        self.tags.mark_absent(ev.tag_id)
        ...
        self._emit_event(ev)       # depart ALWAYS forwarded
    return
```

and the `TagTracker`:

```python
def mark_present(self, tag_hex: str) -> bool:
    key = tag_hex.upper()
    if key in self.present:
        return False          # already present -> dedup: don't emit another arrive
    self.present.add(key)
    return True

def mark_absent(self, tag_hex: str):
    key = tag_hex.upper()
    if key in self.present:
        self.present.remove(key)
```

For the trace above:

| Event | `mark_present` returns | Sent to backend? |
| --- | --- | --- |
| `arrive ant=1` | `True` (first time) | **YES** (`arrive`) |
| `arrive ant=2` | `False` (already present) | no |
| `depart ant=1` | presence cleared | **YES** (`depart`) — but backend only reacts to arrives |
| `depart ant=2` | nothing to clear | **YES** (`depart`) |

So the first pass looks correct. **However**, on the next pass by the same rider:

- The rider has departed from antenna 1 (presence cleared by `depart ant=1`). Antenna 2 then emits its own `depart ant=2`, again `TagTracker.present` no longer contains the tag — fine.
- On the next lap, antenna 1 emits `arrive` → `mark_present` returns `True` → lap counted.
- Then antenna 2 emits `arrive` → at this point, **because the tag is present from antenna 1**, `mark_present` returns `False`. OK.

So far so good. But there is a race window: **the `depart` from one antenna can fire _before_ the `arrive` on the other** if the rider is fast enough or the antennas are slightly offset. Concretely:

```
arrive  ant=1  (tag enters presence)
depart  ant=1  (tag leaves presence — removed from set)
arrive  ant=2  (tag re-enters presence — LAP COUNTED AGAIN!)
depart  ant=2
```

This is the exact bug the user describes. It happens whenever the depart from the early antenna arrives before the arrive from the late antenna — i.e. any time the rider is moving quickly or the two antennas are read in non-overlapping windows. The likelihood is strongly dependent on `tag.reporting.depart_time = 300` (ms) and reader scheduling. Because the reader fires one depart **per antenna** and the tracker only has one global presence flag, the tracker conflates “left antenna 1” with “left the timing line entirely”, and the next arrive on antenna 2 is mis-classified as a new pass.

A second, simpler contributing factor: a rider who comes past very slowly on the start of their next lap may overlap the previous depart cooldown, producing double-counts too. There is no **time-based** cooldown, only a presence-flag toggle.

**Proposed fix (de-duplication policy):**

Do both of the following — they are independent layers of defence:

1. **Per-tag cooldown in the reader service.** After a tag passes (i.e. the service decides this is a real new pass and forwards an `arrive`), ignore any further `arrive` for that tag for `N` seconds. For a closed-loop bike course, a cooldown of `max(lap_time / 3, 10 s)` is safe — a rider cannot complete a lap in under 10 s on any realistic circuit. Expose this as env var `MIN_LAP_INTERVAL_S` (default 10.0 s, tunable per race). Implementation lives in `TagTracker.mark_present` — track `last_emitted_at[tag]` in addition to the presence set.
2. **Presence tracked per-antenna, not globally.** Maintain `present[tag_id]: set[int]` of antennas currently showing the tag. Only emit `arrive` when the set goes from `{}` → non-empty; only emit `depart` when it returns to `{}`. This removes the race window described above.

The cooldown (1) is the pragmatic fix that will eliminate duplicates even on older reader firmware / across different reader models. The per-antenna presence set (2) is the correct fix at the sensor-fusion layer. Ship both.

**Also consider:** moving de-duplication to the backend as a defence in depth. Since `RaceState.add_lap` accepts any `arrive`, a malfunctioning or restarted reader service can still corrupt the race. Mitigation: add a `min_pass_interval_s` to `RaceState` and reject lap increments that arrive within that window of `last_pass_time`.

---

## P0-2 — No way to couple a tag to a rider name without manual CSV editing

**User report:** the operator wants to hold a tag near the antenna, have the system read it, and immediately be prompted to register a rider name for that tag.

**Where:** the capability simply does not exist. Evidence:

- `racetag-frontend/script.js:40` — `tagData` is built only from CSV in the browser.
- `racetag-backend/openapi.yaml` — there is no `/participants`, `/riders`, or `/tags` endpoint.
- `racetag-backend/domain/race.py` — `Participant` has `tag_id` but no `name`/`bib`.

**Proposed fix (happy-path UX, full sketch):**

Backend:
- Add a `Rider` entity `{tag_id (PK, hex uppercase), bib, name, created_at}`. In-memory dict keyed by `tag_id` is fine for MVP — same storage class as `RaceState`.
- New endpoints (OpenAPI-first — edit `openapi.yaml` then regenerate models):
  - `POST /riders` — body `{tag_id, bib, name}` → upsert.
  - `GET  /riders` → list.
  - `DELETE /riders/{tag_id}`.
  - `GET  /riders/recent-reads?since=ISO8601&limit=10` → returns the last unknown tags seen (i.e. `arrive` events for tag_ids that have no registered rider). This is what drives the “hold tag near antenna” UX.
- SSE: add a new event type `{"type":"unknown_tag", "tag_id": "...", "timestamp": "...", "antenna": 1, "rssi": -40}` broadcast every time an `arrive` arrives for a tag that is not in the `riders` map. The existing `standings` broadcast already includes `tag_id` for known tags, so the frontend can fan out on arrival.
- Change: `ParticipantDTO` should carry `bib` and `name` (joined from the rider map at standings-time). Frontend then stops needing CSV for display.

Frontend:
- New **Registration mode** panel. Two UI states:
  1. _Idle_: a big “Register a new rider” button + pending-read indicator: “Hold a tag in front of the antenna.”
  2. _Read captured_: modal with tag id pre-filled, two empty fields (bib, name), `Save` / `Cancel`.
- Wiring:
  - When the user clicks “Register a new rider”, the panel sets a `state.awaitingRead = true` flag.
  - The SSE handler, when it receives `{"type":"unknown_tag", ...}` AND `state.awaitingRead`, opens the modal with that tag id and sets the flag to false.
  - On Save, `POST /riders`; on 200, close modal, toast “Registered bib 42 – John Doe”.
  - The standings table immediately re-renders with the new name (since the next `standings` broadcast will include it).
- Retain the CSV import flow for bulk pre-registration — but make it POST to `/riders` in a loop rather than only living in browser memory.

Acceptance: operator starts the app cold with no riders, stands at the antenna, holds a tag, sees the modal pop in ≤1 s, types `42 / John Doe`, hits Enter, and a lap registered 3 s later already shows as `42 John Doe` in the standings.

---

## P1-1 — Reader-minted timestamps are thrown away; wall-clock of the service is used

**Where:** `racetag-reader-service/src/sirit_client.py:282` — `fields["timestamp"] = self._now_iso()`.

**Root cause:** The reader provides `first=2025-09-27T15:15:04.403` (its own clock, possibly local tz) for each arrive, but the service overwrites the `timestamp` field with `datetime.now(UTC)` at the moment the frame reaches Python. Any network or scheduling jitter — and any garbage collection pause — becomes measurement error in the race time. For a 30 km/h rider, 100 ms of jitter is ~83 cm of placement error.

**Proposed fix:** If `first` (arrive) or `last` (depart) is present on the event, parse it and use it as the authoritative event `timestamp`. Fall back to `_now_iso()` only if the reader did not supply one. Document the reader’s time_zone (`info.time_zone=Europe/Berlin` in `init_commands`) and force it to UTC instead to avoid DST surprises.

---

## P1-2 — `RaceState.add_lap` is unconditional; backend has no dedup / min-interval

**Where:** `racetag-backend/racetag-backend/domain/race.py:34`.

**Root cause:** `add_lap` increments `p.laps` for **every** arrive event, with no rate-limit, no idempotency key, no sanity check. If the reader service restarts and re-emits, or the network hiccups and `requests.Session` retries (it currently does not, but adding retries is a legitimate next step), laps will double. This is the defence-in-depth counterpart of P0-1.

**Proposed fix:** introduce `min_pass_interval_s` (default 8 s). `add_lap` ignores passes within `[last_pass_time, last_pass_time + min_pass_interval_s]` for the same tag. Also add an idempotency key — the simplest is `f"{tag_id}:{timestamp}"` or the reader-provided `first`/`last`.

---

## P1-3 — In-memory state lost on restart (no persistence)

**Where:** `racetag-backend/racetag-backend/app.py:58-64` — `race`, `events`, `subscribers` are module-level Python collections. README admits it: _“Storage is in-memory for MVP”_.

**Root cause:** By design. Fine for a demo, unacceptable for an actual race — a backend restart loses the current standings mid-race.

**Proposed fix:** SQLite file at `${RACETAG_DATA_DIR}/race.db`. Two tables: `riders(tag_id, bib, name)` and `tag_events(id, tag_id, event_type, timestamp, antenna, rssi)`. On startup replay `tag_events` into an empty `RaceState`. SQLAlchemy is overkill for this — `sqlite3` stdlib is enough. Packaging: the DB file lives next to the single executable (see `PACKAGING.md`).

---

## P1-4 — SSE implementation uses blocking `time.sleep(1)` in a per-client loop

**Where:** `racetag-backend/racetag-backend/app.py:142-144`.

**Root cause:** Each connected client spawns a generator that busy-waits with a 1-second sleep. Under uvicorn’s default worker model this ties up a thread per client. More importantly, `import time as _t` inside the loop is a code smell and the loop has no actual `asyncio` integration — so the endpoint will not scale and cannot participate in FastAPI’s cooperative shutdown.

**Proposed fix:** Convert `/stream` to `async def` with an `asyncio.Queue` per subscriber. Publishers `await queue.put(...)` (or use `asyncio.Queue.put_nowait` from the sync handler via `run_in_threadpool`), consumer uses `await asyncio.wait_for(queue.get(), timeout=15)` and yields a `: keepalive` on timeout. Drop the `time.sleep` entirely.

---

## P1-5 — SSE subscribers list is not thread-safe

**Where:** `racetag-backend/racetag-backend/app.py:64` and `:92-104`.

**Root cause:** `subscribers: List[List[...]]` is mutated from the request thread handling `POST /events/tag/batch` and iterated / appended-to by each `/stream` generator. Under CPython the GIL hides the breakage most of the time, but a subscriber disconnecting mid-broadcast triggers `ValueError: list.remove(x): x not in list` which the code catches but silently swallows (`pass`). Replace with an `asyncio.Queue` (see P1-4) or at minimum guard with a `threading.Lock`.

---

## P1-6 — `HttpBackendClient` mis-handles timeouts/errors: no retry, events are lost

**Where:** `racetag-reader-service/src/backend_client/http.py:77-97`.

**Root cause:** On POST failure (network, 5xx, or mismatch response) the worker **drops the batch** (the `buf` is cleared by the caller before `_flush`). No DLQ, no disk spool. A 200 ms hiccup in the middle of a race dumps however many events were in flight.

**Proposed fix:** On failure, re-queue the batch at the head of the queue (bounded) or persist to a local append-only file (`reader-service/spool/*.jsonl`) and drain in the background. Add exponential backoff (200 ms → 500 ms → 1 s, cap 3 retries).

---

## P1-7 — Backend `X-API-Key` auth is globally enforced but the frontend serves an empty key by default

**Where:** `racetag-frontend/docker-entrypoint.sh` and `api.js:5`.

**Root cause:** The placeholder `__RACETAG_FRONTEND_API_KEY__` is only rewritten when `RACETAG_FRONTEND_API_KEY` is set. Users who forget to set it ship the literal placeholder into production, which the `getApiHeaders` guard converts to “no header sent”. If the backend then has `RACETAG_API_KEY` set, **every request 401s** with no obvious error. The fact that the user said “software security is not a priority” is not an excuse for a silent auth misconfig. Since security isn’t a priority, the simplest resolution is **do not enable API key auth at all in the packaged app** — remove the `X-API-Key` plumbing from the frontend/backend for the desktop build. Track it in `PLAN.md` as a deliberate simplification.

---

## P1-8 — `HttpBackendClient` bug: `Exception` replaces specific errors and silently re-raises same-thread

**Where:** `racetag-reader-service/src/backend_client/http.py:85-95`.

```python
try:
    data = resp.json()
except Exception as e:
    raise RuntimeError(...)      # raised inside _flush
if not isinstance(data, dict) ...:
    raise RuntimeError(...)
...
except Exception as e:
    print(...)                    # outer except catches the above, just prints
```

So the `raise RuntimeError` is immediately caught and logged, not propagated. The code reads as defensive but is effectively `print` + drop-batch. Minor but worth cleaning up.

---

## P1-9 — `openapi.yaml` is out of sync with the real wire format (reader_serial required but sometimes absent)

**Where:** `racetag-backend/openapi.yaml` lines 67-100 vs `racetag-reader-service/src/sirit_client.py:282-288`.

**Root cause:** `TagEventDTO.reader_serial` is `required: true`. But the reader service only populates `fields["reader_serial"]` if `self.reader_serial is not None`. If the `info.serial_number` response is slow, lost, or the reader model is the YR9700 (which has different protocol), events fire before the serial is known and the POST fails pydantic validation at the backend → **entire batch rejected**. The reader service then interprets that as a mismatch and prints an error, and the events are gone.

**Proposed fix:** make `reader_serial` optional in `openapi.yaml`, regenerate `models_api.py`. Or: pre-probe the reader and block start-up until `info.serial_number` replies (worse — slows boot).

---

## P1-10 — Frontend polyfill SSE reader never reconnects

**Where:** `racetag-frontend/api.js:13-53`.

**Root cause:** `connectSSEWithHeaders` calls `onError` on any network hiccup but does nothing else; the UI shows “Connection error” indefinitely until the user clicks Connect again. Native `EventSource` auto-reconnects; the polyfill does not.

**Proposed fix:** on error, schedule a reconnect with exponential backoff (1 s, 2 s, 5 s, capped at 15 s). Surface the reconnect state in the `#status` indicator.

---

## P1-11 — Frontend: `parseCSV` does not handle quoted commas, BOM, CRLF, or trimming

**Where:** `racetag-frontend/script.js:24-52`.

**Root cause:** `line.split(',')` breaks on any name containing a comma (“Doe, John”). A leading BOM from Excel-exported CSVs will prepend `\ufeff` to the first column header, so the first row’s `tag_id` never matches. CRLF line endings mean `trim()` has to work — it does — but the parser is still brittle.

**Proposed fix:** replace with a proper CSV tokenizer (regex, or the tiny `Papa Parse` dep if a dep is OK). Strip BOM explicitly.

---

## P1-12 — Reader-service treats antenna as "Optional[int]" but always sends it; backend ignores it entirely

**Where:** `racetag-backend/racetag-backend/app.py:77-105`.

**Root cause:** `TagEventDTO.antenna` is transmitted on the wire but the backend never reads it. Consequences: (a) we cannot do antenna-based re-dedup at the backend; (b) operators cannot see which antenna failed; (c) the field is wasted bandwidth if we never use it.

**Proposed fix:** persist `antenna` into `tag_events` (once P1-3 adds persistence). Surface a diagnostic endpoint `GET /diagnostics/antennas` that shows reads per antenna over the last 60 s — essential for the operator setting up the reader on a new course.

---

## P2-1 — `race.add_lap` marks `finished=True` on the pass **into** the final lap, not on crossing the line after the final lap

**Where:** `racetag-backend/racetag-backend/domain/race.py:47-49`.

```python
if not p.finished and p.laps >= self.total_laps:
    p.finished = True
```

**Root cause:** If a race is 5 laps and you set `total_laps=5`, the participant is considered “finished” the moment their **5th pass counter** is incremented — which is the end of their 5th lap (i.e. after completing 5 laps). This is actually correct if you interpret “total_laps = number of complete laps required”, but ambiguous if you interpret it as “number of times the line is crossed” (which is usually `total_laps + 1` because the start counts). Document the semantic or expose it as a config.

---

## P2-2 — `Participant.gap_ms` only populated for leader’s lap; lapped riders always get `None`

**Where:** `racetag-backend/racetag-backend/domain/race.py:85-91`.

**Root cause:** When a rider is 1+ laps down, `gap_ms` is explicitly set to `None`. That’s a rendering decision that belongs in the frontend; the backend could instead surface the time gap on the leader’s current lap reference. Minor.

---

## P2-3 — `session_state` is single-session; a reader disconnect + reconnect gets a new session id but `self.session.bound` stays `True`

**Where:** `racetag-reader-service/src/sirit_client.py:203-228`.

**Root cause:** If the reader reboots while the service is up, the new `event.connection id = N` arrives but `_maybe_bind_and_config` sees `self.session.bound == True` and skips the rebind. Result: silent event loss.

**Proposed fix:** when `event.connection id` is received AND differs from `self.session.id`, reset `bound = False` and re-run the bind+config sequence.

---

## P2-4 — No way to reset / zero a race from the UI

**Where:** n/a — the feature does not exist.

**Root cause:** `POST /race/reset` does not exist. Operators restart the container to clear state; with persistence (P1-3) this will no longer work.

**Proposed fix:** `POST /race/reset` endpoint + button in the UI with a confirm dialog. The `total_laps` should also be editable at runtime (currently it is fixed at backend startup from `RACE_TOTAL_LAPS`).

---

## P2-5 — `serve.py` and the nginx Dockerfile diverge

**Where:** `racetag-frontend/serve.py` vs `racetag-frontend/Dockerfile` + `docker-entrypoint.sh`.

**Root cause:** `serve.py` is a plain `http.server` that does no placeholder substitution. Running the frontend locally without Docker ships the literal `__RACETAG_FRONTEND_API_KEY__` string. For the packaged desktop build we will replace both anyway, but flag it.

---

## P2-6 — Reader service logs everything via `print()`; no log level, no file, no structured output

**Where:** `racetag-reader-service/src/*`.

**Root cause:** `print()` is used for INFO, DEBUG, and ERROR alike, interleaved from multiple threads. The service writes ANSI escape sequences even when stdout is a pipe (`--no-color` flag exists but defaults to on).

**Proposed fix:** introduce `logging` with `RotatingFileHandler` to `logs/reader.log`, keep console output by default. Gate ANSI on `sys.stdout.isatty()`.

---

## P2-7 — `requirements.txt` is unpinned in reader-service but pinned in backend — inconsistent

**Where:** `racetag-reader-service/requirements.txt` vs `racetag-backend/requirements.txt`.

**Root cause:** `requests>=2.28,<3` (unpinned) vs `fastapi==0.119.0` (pinned). Lock both.

---

## P2-8 — No tests anywhere

**Where:** every repo.

**Root cause:** no `tests/` directory in any of the three. Given that we are about to fix sensor-fusion logic (P0-1), *not* adding unit tests would be malpractice. The test pyramid for this system is naturally:

- reader-service: parser tests for `_extract_kv`, state-machine tests for `TagTracker` (present/absent interleavings, cooldown) — **no reader needed**.
- backend: endpoint tests via `fastapi.testclient.TestClient` + domain-level tests for `RaceState.add_lap`/standings — **no network needed**.
- frontend: cypress or a plain headless puppeteer smoke test that loads the page against a stubbed backend.
- cross-component: a contract test that posts the sample events from `logs/session.log` through the real backend and asserts lap counts.

---

## P2-9 — Three separate `docker-compose.yml` files, no top-level compose

**Where:** each repo.

**Root cause:** there is no way to spin up the whole stack with a single command. For the desktop build this is moot, but for dev it is friction.

**Proposed fix:** add `/Users/jan/Documents/git/racetag/docker-compose.yml` at the top level that pulls in all three repos via `build:` paths.
