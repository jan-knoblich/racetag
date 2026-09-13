"""Support bundle: one zip with everything needed to diagnose a problem (plan D6).

Contents:

- ``logs/<file>``      every file directly in the log directory (rotated
                       backups included),
- ``racetag.db``       a consistent copy made with SQLite ``VACUUM INTO``, which
                       is safe while the backend keeps the database open in WAL
                       mode (a plain file copy could miss the WAL contents),
- ``<name>.json``      one file per ``extra_json`` entry,
- ``notes.txt``        creation time plus anything that could not be included.

The zip is written to a temporary name next to the destination and renamed at
the end, so a failed run never leaves a truncated bundle behind.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import re
import sqlite3
import tempfile
import zipfile
from pathlib import Path
from typing import Dict, List

log = logging.getLogger("racetag.shell.support")

DB_FILENAME = "racetag.db"
_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")


def default_bundle_name(now: _dt.datetime) -> str:
    return f"Racetag-Support-{now:%Y%m%d-%H%M}.zip"


def _vacuum_copy(db_path: Path, dest: Path) -> None:
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    try:
        conn.execute("VACUUM INTO ?", (str(dest),))
    finally:
        conn.close()


def build_support_bundle(
    dest_zip: Path,
    data_dir: Path,
    log_dir: Path,
    extra_json: Dict[str, object],
) -> Path:
    """Write the bundle to ``dest_zip`` and return its path.

    Missing or unreadable parts are recorded in ``notes.txt`` instead of failing
    the whole bundle; only an unwritable destination raises (``OSError``).
    """
    dest_zip = Path(dest_zip)
    data_dir = Path(data_dir)
    log_dir = Path(log_dir)
    dest_zip.parent.mkdir(parents=True, exist_ok=True)
    notes: List[str] = []

    fd, tmp_name = tempfile.mkstemp(prefix=".racetag-support-", suffix=".zip.part", dir=dest_zip.parent)
    os.close(fd)
    tmp_zip = Path(tmp_name)
    try:
        with zipfile.ZipFile(tmp_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            if log_dir.is_dir():
                for entry in sorted(log_dir.iterdir()):
                    if not entry.is_file() or entry.resolve() in (dest_zip.resolve(), tmp_zip.resolve()):
                        continue
                    try:
                        zf.write(entry, arcname=f"logs/{entry.name}")
                    except OSError as exc:
                        notes.append(f"log file {entry.name} skipped: {exc}")
            else:
                notes.append(f"log directory {log_dir} does not exist")

            db_path = data_dir / DB_FILENAME
            if db_path.is_file():
                with tempfile.TemporaryDirectory(prefix="racetag-support-db-") as tmp_dir:
                    copy_path = Path(tmp_dir) / DB_FILENAME
                    try:
                        _vacuum_copy(db_path, copy_path)
                        zf.write(copy_path, arcname=DB_FILENAME)
                    except (sqlite3.Error, OSError) as exc:
                        log.warning("database copy for support bundle failed: %s", exc)
                        notes.append(f"database copy failed: {exc}")
            else:
                notes.append(f"database {db_path} not found; skipped")

            for name, value in extra_json.items():
                safe = _SAFE_NAME.sub("_", str(name)) or "extra"
                zf.writestr(
                    f"{safe}.json",
                    json.dumps(value, indent=2, ensure_ascii=False, default=str),
                )

            created = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
            lines = [f"Racetag support bundle created {created}", ""]
            lines += notes if notes else ["All parts included."]
            zf.writestr("notes.txt", "\n".join(lines) + "\n")

        os.replace(tmp_zip, dest_zip)
    except BaseException:
        tmp_zip.unlink(missing_ok=True)
        raise
    log.info("support bundle written to %s (%d note(s))", dest_zip, len(notes))
    return dest_zip
