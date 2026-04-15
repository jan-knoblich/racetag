# Racetag

RFID lap-timing proof-of-concept for bicycle round-course races.

This is a **monorepo** consolidating three formerly-separate applications:

| Path | Role | Language / framework | Upstream |
| --- | --- | --- | --- |
| [`apps/reader-service/`](apps/reader-service/) | TCP client for the Sirit INfinity 510 RFID reader; normalises tag events and forwards them to the backend | Python 3.11 | [`paclema/racetag-reader-service`](https://github.com/paclema/racetag-reader-service) |
| [`apps/backend/`](apps/backend/) | FastAPI service — race state, rider registry, lap counting, SSE fan-out | Python 3.13 / FastAPI | [`paclema/racetag-backend`](https://github.com/paclema/racetag-backend) |
| [`apps/frontend/`](apps/frontend/) | Static UI — live standings, rider registration | HTML / CSS / vanilla JS | [`paclema/racetag-frontend`](https://github.com/paclema/racetag-frontend) |
| [`apps/desktop/`](apps/desktop/) | pywebview wrapper that bundles all three into a single `.app` / `.exe` | Python + PyInstaller | — (new) |

Each `apps/<name>/` subdirectory was merged in via `git subtree add`, so full pre-merge commit history is preserved. Run `git log apps/backend/` to see it.

## Planning documents

- [ARCHITECTURE.md](ARCHITECTURE.md) — component diagram, data flow, tech stack
- [ISSUES.md](ISSUES.md) — catalogued bugs with `file:line` references
- [PLAN.md](PLAN.md) — phased work breakdown (backend / frontend / QA / docs)
- [PACKAGING.md](PACKAGING.md) — pywebview + PyInstaller desktop-build strategy

## Quickstart (Docker)

```bash
docker compose up --build
```

Backend: http://localhost:8600 · Frontend: http://localhost:8080

## Quickstart (native, for development)

```bash
# backend
cd apps/backend && python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && python -m racetag-backend.app

# reader-service (in another shell)
cd apps/reader-service && python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && python src/racetag_reader_service.py

# frontend (static; any server works)
cd apps/frontend && python3 -m http.server 8080
```

## Desktop app (planned)

A bundled `Racetag.app` (macOS) / `Racetag.exe` (Windows) is tracked in [PACKAGING.md](PACKAGING.md) and work items `W-070` / `W-071` / `W-072` in [PLAN.md](PLAN.md).

## License

Inherited per-subdirectory from the source repos; see each `apps/*/LICENSE` (where present).
