"""
Ensure the desktop app module is importable from tests.

The apps/desktop/pyproject.toml sets pythonpath=["."], which adds apps/desktop
to sys.path so that `import app` resolves to apps/desktop/app.py.

We also isolate RACETAG_DATA_DIR for every test so that _bootstrap_env() and
_build_combined_app() never touch a real database directory.  The isolation
must be applied via os.environ BEFORE any test imports the backend app module,
since the backend's app.py runs Storage(...) at module level.
"""
import os
import sys
from pathlib import Path

import pytest

# Ensure apps/desktop is importable (belt-and-suspenders; pyproject.toml
# handles this for pytest runs, but explicit is better than implicit).
_desktop_dir = str(Path(__file__).resolve().parent.parent)
if _desktop_dir not in sys.path:
    sys.path.insert(0, _desktop_dir)


@pytest.fixture(autouse=True)
def _isolate_data_dir(tmp_path, monkeypatch):
    """Redirect RACETAG_DATA_DIR to an isolated tmp dir for every test.

    Prevents _build_combined_app() from writing SQLite to the real data dir
    and ensures each test starts clean.
    """
    monkeypatch.setenv("RACETAG_DATA_DIR", str(tmp_path / "data"))
