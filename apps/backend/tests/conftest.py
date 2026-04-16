"""
Ensure the backend package directory is on sys.path so tests can import
production modules when pytest is invoked from outside the apps/backend/
directory (e.g. from the monorepo root).

The apps/backend/pyproject.toml already sets pythonpath=["racetag-backend"]
for runs rooted inside that directory; this file is the fallback for
monorepo-root invocations.

W-050: We also redirect RACETAG_DATA_DIR to a per-test tmp directory so that
the module-level `storage = Storage(...)` call in app.py never touches the
real ./data/racetag.db during the test suite.  The redirect happens via an
autouse session-scoped fixture that sets the env var BEFORE any test module
imports app.py (importlib.reload() in each test picks up the env var fresh).
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

_pkg = str(Path(__file__).parent.parent / "racetag-backend")
if _pkg not in sys.path:
    sys.path.insert(0, _pkg)


@pytest.fixture(autouse=True)
def _isolate_data_dir(tmp_path, monkeypatch):
    """Redirect RACETAG_DATA_DIR to a fresh tmp dir for every test.

    This prevents the module-level Storage(...) in app.py from writing to
    ./data/racetag.db when tests reload the app module via importlib.reload().
    Tests that want a specific data dir (test_persistence.py) override this
    env var themselves BEFORE reloading the app module.
    """
    monkeypatch.setenv("RACETAG_DATA_DIR", str(tmp_path))
