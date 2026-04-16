"""
Ensure the reader-service src directory is on sys.path so tests can import
production modules when pytest is invoked from outside the apps/reader-service/
directory (e.g. from the monorepo root).

The apps/reader-service/pyproject.toml already sets pythonpath=["src"] for
runs rooted inside that directory; this file is the fallback for monorepo-root
invocations.
"""
import sys
from pathlib import Path

_src = str(Path(__file__).parent.parent / "src")
if _src not in sys.path:
    sys.path.insert(0, _src)
