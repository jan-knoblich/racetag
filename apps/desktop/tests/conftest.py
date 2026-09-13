"""
Ensure the desktop app module is importable from tests.

The apps/desktop/pyproject.toml sets pythonpath=["."], which adds apps/desktop
to sys.path so that `import app` resolves to apps/desktop/app.py.

We also isolate RACETAG_DATA_DIR for every test so that _bootstrap_env() and
_build_combined_app() never touch a real database directory.  The isolation
must be applied via os.environ BEFORE any test imports the backend app module,
since the backend's app.py runs Storage(...) at module level.
"""
import logging
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


@pytest.fixture(autouse=True)
def _no_native_dialogs(monkeypatch):
    """A real MessageBoxW/osascript dialog would block a headless test run."""
    monkeypatch.setenv("RACETAG_NO_DIALOGS", "1")


@pytest.fixture(autouse=True)
def _reset_desktop_logging():
    """Remove file handlers installed by desktop_logging.setup_logging().

    Left in place they would keep files in deleted tmp dirs open (Windows
    cannot delete those) and swallow log records other tests expect.
    """
    yield
    desktop_logging = sys.modules.get("desktop_logging")
    if desktop_logging is None:
        return
    desktop_logging._remove_own_handlers(logging.getLogger())
    for name in desktop_logging.BACKEND_LOGGERS:
        logger = logging.getLogger(name)
        desktop_logging._remove_own_handlers(logger)
        logger.propagate = True
        logger.setLevel(logging.NOTSET)
    logging.getLogger().setLevel(logging.WARNING)
