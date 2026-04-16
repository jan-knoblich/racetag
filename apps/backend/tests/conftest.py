"""
Ensure the backend package directory is on sys.path so tests can import
production modules when pytest is invoked from outside the apps/backend/
directory (e.g. from the monorepo root).

The apps/backend/pyproject.toml already sets pythonpath=["racetag-backend"]
for runs rooted inside that directory; this file is the fallback for
monorepo-root invocations.
"""
import sys
from pathlib import Path

_pkg = str(Path(__file__).parent.parent / "racetag-backend")
if _pkg not in sys.path:
    sys.path.insert(0, _pkg)
