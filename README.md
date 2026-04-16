# Racetag

RFID lap-timing for bicycle round-course races. Detects tag passings via a Sirit INfinity 510 reader, computes live standings, and displays them in a browser UI.

## Monorepo layout

| Path | Role | Language / stack |
| --- | --- | --- |
| [`apps/reader-service/`](apps/reader-service/) | TCP client for the Sirit INfinity 510 — normalises tag events and forwards them to the backend | Python 3.11 |
| [`apps/backend/`](apps/backend/) | FastAPI service — race state, rider registry, lap counting, SSE fan-out, SQLite persistence | Python 3.13 / FastAPI |
| [`apps/frontend/`](apps/frontend/) | Static UI — live standings, rider registration, settings, diagnostics | HTML / CSS / vanilla JS |
| [`apps/desktop/`](apps/desktop/) | pywebview shell that bundles all three into `Racetag.app` / `Racetag.exe` | Python + PyInstaller |

Each `apps/<name>/` directory was merged in via `git subtree add`; full pre-merge history is preserved (`git log apps/backend/`).

---

## Quickstart

### 1. Desktop app (recommended for end users)

1. Download `Racetag-<version>-mac.zip` or `Racetag-<version>-win.zip` from [GitHub Releases](../../releases).
2. Unzip and double-click `Racetag.app` (macOS) or `Racetag.exe` (Windows).
3. On first run, Racetag creates `~/.racetag/data/` and opens the UI in a native window.
4. Open Settings (gear icon) and set the reader IP address.

No Docker, no Python install required.

### 2. Docker Compose (LAN deployment with a real reader)

```bash
# Clone the repo
git clone <repo-url> racetag && cd racetag

# Optional: customise env vars (see apps/*/.env.example)
cp apps/backend/.env.example apps/backend/.env
cp apps/reader-service/.env.example apps/reader-service/.env
# Edit apps/reader-service/.env and set READER_IP=<your reader IP>

# Start all three services
docker compose up --build
```

- Backend: http://localhost:8600
- Frontend: http://localhost:8680

The reader-service container uses `network_mode: host` so it can reach the Sirit reader on the LAN.
See each app's README for the full env var list:
[reader-service](apps/reader-service/README.md) · [backend](apps/backend/README.md) · [frontend](apps/frontend/README.md)

### 3. Native dev (for contributors)

```bash
# backend
cd apps/backend
python3.13 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
uvicorn --app-dir racetag-backend app:app --reload --host 0.0.0.0 --port 8600

# reader-service (separate shell)
cd apps/reader-service
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python src/racetag_reader_service.py --ip 192.168.1.130 --backend-url http://localhost:8600

# frontend (separate shell)
cd apps/frontend && python3 serve.py --host 127.0.0.1 --port 8680
```

See [docs/TESTING.md](docs/TESTING.md) for test setup and CI details.

---

## Key features

- Live lap timing with RFID tags (Sirit INfinity 510, UHF passive)
- Multi-antenna sensor fusion — per-antenna presence tracking and per-tag cooldown eliminate double-counts
- Fast rider coupling — scan a tag from the timing line to trigger the register-rider modal
- SQLite persistence (`journal_mode=WAL`, `synchronous=FULL`) — race and rider data survive restarts
- Browser-local timestamp display — reader captures UTC; frontend renders in the visitor's timezone
- Antenna diagnostics panel — per-antenna read counts for the last configurable window
- Settings UI — reader IP, lap count, and cooldown configurable without editing env files

---

## API surface

Full spec: [`apps/backend/openapi.yaml`](apps/backend/openapi.yaml)

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/events/tag/batch` | Ingest a batch of tag events from the reader-service |
| `GET` | `/classification` | Standings snapshot (ordered) |
| `GET` | `/race` | Race metadata + participant list |
| `PATCH` | `/race` | Update `total_laps` |
| `POST` | `/race/reset` | Clear lap data; preserve riders |
| `GET` | `/config` | Effective config (env defaults merged with persisted overrides) |
| `PATCH` | `/config` | Update `reader_ip`, `min_lap_interval_s`, or `total_laps` |
| `GET` | `/diagnostics/antennas` | Per-antenna read counts (`?window_s=60`) |
| `GET` | `/stream` | SSE stream: `lap`, `standings`, `unknown_tag`, `race_reset`, `race_updated` |
| `POST` | `/riders` | Register or update a rider (upsert by `tag_id`) |
| `GET` | `/riders` | List all registered riders |
| `GET` | `/riders/recent-reads` | Recent unregistered tag passings (ring buffer) |
| `GET` | `/riders/{tag_id}` | Look up a rider by tag |
| `DELETE` | `/riders/{tag_id}` | Remove a rider |

Authentication is via `X-API-Key` header. The key is **off by default** in the packaged build; set `RACETAG_API_KEY` to enable it.

---

## Build and release

To cut a release, push a version tag:

```bash
git tag v0.1.0
git push origin v0.1.0
```

The CI workflow (`.github/workflows/release.yml`) builds `Racetag-<version>-mac.zip` and `Racetag-<version>-win.zip` on macOS and Windows runners and attaches them to the GitHub Release automatically.

See [PACKAGING.md](PACKAGING.md) for the pywebview + PyInstaller design rationale and build details.

To build locally:

```bash
pip install pyinstaller
cd apps/desktop && pyinstaller pyinstaller.mac.spec --clean   # macOS
cd apps/desktop && pyinstaller pyinstaller.win.spec --clean   # Windows
```

---

## Testing

See [docs/TESTING.md](docs/TESTING.md). The CI workflow (`.github/workflows/ci.yml`) runs 74 tests across reader-service, backend, and desktop on every push.

---

## Related docs

- [ARCHITECTURE.md](ARCHITECTURE.md) — component diagram, data flow, tech stack
- [docs/OPERATOR_GUIDE.md](docs/OPERATOR_GUIDE.md) — field setup guide for race marshals
- [PACKAGING.md](PACKAGING.md) — pywebview + PyInstaller desktop-build strategy
- [ISSUES.md](ISSUES.md) — catalogued bugs with `file:line` references
- [PLAN.md](PLAN.md) — phased work breakdown

---

## License

Inherited per-subdirectory from the upstream source repos; see each `apps/*/LICENSE` where present.
