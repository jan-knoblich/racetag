# Testing guide

## Prerequisites

Python must be installed. Each app manages its own virtual environment and
dependency set; there is no shared root environment.

---

## Running tests locally

### reader-service (Python 3.11)

```bash
cd apps/reader-service
python3.11 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt
pytest
```

`pytest` discovers tests under `apps/reader-service/tests/` and adds
`apps/reader-service/src/` to `sys.path` automatically (configured in
`pyproject.toml`).

### backend (Python 3.13)

```bash
cd apps/backend
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
pytest
```

Tests live under `apps/backend/tests/`. The `racetag-backend/` package is on
`sys.path` via `pyproject.toml`, so `import app`, `import domain.race`, etc.
work without install.

---

## Adding a new test

1. Drop a file named `test_<something>.py` inside `apps/<app>/tests/`.
2. Name every test function `test_<description>`.
3. No `__init__.py` is required in `tests/`; pytest collects tests by file name.
4. Use only the packages already in `requirements.txt` / `requirements-dev.txt`.
   If you need a new test-only package, add it to `requirements-dev.txt` — not
   `requirements.txt`.

---

## CI

The workflow at `.github/workflows/ci.yml` runs on every push and pull request.
It has two independent jobs:

| Job | Python | App |
|-----|--------|-----|
| `reader-service-tests` | 3.11 | `apps/reader-service/` |
| `backend-tests` | 3.13 | `apps/backend/` |

Each job:
1. Checks out the repo.
2. Sets up the correct Python version with `actions/setup-python@v5`.
3. Installs `requirements.txt` + `requirements-dev.txt` for that app.
4. Runs `pytest` from the app's directory.

Both jobs run unconditionally on every push/PR so you always get a full green
signal before merging.

---

## Troubleshooting

**`ModuleNotFoundError` when running pytest locally**
Make sure you activated the virtualenv for the right app and that you ran
`pip install -r requirements.txt -r requirements-dev.txt` inside that app's
directory. Each app has its own `.venv`.

**`ModuleNotFoundError` for the app's own source code**
`pyproject.toml` in each app directory tells pytest to add `src/` (reader-
service) or `racetag-backend/` (backend) to `sys.path`. If pytest cannot find
it, ensure you are running `pytest` from the app directory, not from the
monorepo root.

**Wrong Python version**
Use `python3.11` / `python3.13` explicitly when creating the virtualenv. Running
`python3 -m venv .venv` picks the system default, which may differ.

**CI fails but local passes**
Check that every import used in tests is listed in `requirements.txt` or
`requirements-dev.txt`. CI installs only what is in those files.
