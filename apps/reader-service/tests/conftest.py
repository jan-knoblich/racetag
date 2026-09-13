"""
Ensure the reader-service src directory is on sys.path so tests can import
production modules when pytest is invoked from outside the apps/reader-service/
directory (e.g. from the monorepo root).

The apps/reader-service/pyproject.toml already sets pythonpath=["src"] for
runs rooted inside that directory; this file is the fallback for monorepo-root
invocations.

File logging is always on in production (reader.log under the user's
~/.racetag/logs). The suite must not write there, so it is disabled before
any production module is imported; tests that cover file logging enable it
explicitly with a temporary RACETAG_LOG_DIR. Subprocesses inherit the setting.
"""
import os
import sys
from pathlib import Path

os.environ["RACETAG_FILE_LOG"] = "0"

_src = str(Path(__file__).parent.parent / "src")
if _src not in sys.path:
    sys.path.insert(0, _src)
