"""SQLite persistence layer for Racetag backend (W-050).

Opens the database with WAL mode and synchronous=FULL for crash durability.
A threading.Lock protects all write operations; reads use the same connection
(SQLite WAL allows concurrent reads with a single writer).
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Iterator, List, Optional

if TYPE_CHECKING:
    from domain.riders import Rider
    from models_api import TagEventDTO

# ---- DDL ---------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS riders (
    tag_id     TEXT PRIMARY KEY,
    bib        TEXT NOT NULL,
    name       TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tag_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    tag_id        TEXT    NOT NULL,
    event_type    TEXT    NOT NULL,
    timestamp     TEXT    NOT NULL,
    antenna       INTEGER,
    rssi          REAL,
    reader_serial TEXT,
    received_at   TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_tag_events_time ON tag_events(timestamp);
"""


class Storage:
    """Thin SQLite wrapper.

    All write paths go through a threading.Lock so callers don't need to
    coordinate.  The connection is opened in autocommit mode
    (isolation_level=None) with check_same_thread=False so the same
    connection can safely be used from multiple threads once the lock is held.
    """

    def __init__(self, db_path: str | Path, *, wal_mode: bool = True) -> None:
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            str(db_path),
            isolation_level=None,   # autocommit; we issue BEGIN/COMMIT explicitly
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row

        # Durability pragmas — order matters: WAL first, then synchronous.
        if wal_mode:
            self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=FULL;")
        self._conn.execute("PRAGMA foreign_keys=ON;")

        # Create tables idempotently.
        with self._lock:
            self._conn.executescript(_DDL)

    # ---- internal helpers -----------------------------------------------

    def _execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """Execute a single statement inside a BEGIN/COMMIT block (under lock)."""
        with self._lock:
            self._conn.execute("BEGIN;")
            try:
                cur = self._conn.execute(sql, params)
                self._conn.execute("COMMIT;")
                return cur
            except Exception:
                self._conn.execute("ROLLBACK;")
                raise

    # ---- Rider CRUD -----------------------------------------------------

    def upsert_rider(self, rider: Rider) -> None:
        """Insert or replace a rider row."""
        created_at_str = (
            rider.created_at.isoformat(timespec="milliseconds").replace("+00:00", "Z")
            if hasattr(rider.created_at, "isoformat")
            else str(rider.created_at)
        )
        self._execute(
            """
            INSERT INTO riders (tag_id, bib, name, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(tag_id) DO UPDATE SET
                bib        = excluded.bib,
                name       = excluded.name,
                created_at = excluded.created_at;
            """,
            (rider.tag_id, rider.bib, rider.name, created_at_str),
        )

    def get_rider(self, tag_id: str) -> Optional[Rider]:
        """Return a Rider for tag_id, or None."""
        row = self._conn.execute(
            "SELECT tag_id, bib, name, created_at FROM riders WHERE tag_id = ?;",
            (tag_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_rider(row)

    def list_riders(self) -> List[Rider]:
        """Return all riders ordered by rowid (insertion order)."""
        rows = self._conn.execute(
            "SELECT tag_id, bib, name, created_at FROM riders ORDER BY rowid;"
        ).fetchall()
        return [self._row_to_rider(r) for r in rows]

    def delete_rider(self, tag_id: str) -> bool:
        """Delete rider by tag_id. Returns True if a row was removed."""
        cur = self._execute(
            "DELETE FROM riders WHERE tag_id = ?;",
            (tag_id,),
        )
        return cur.rowcount > 0

    @staticmethod
    def _row_to_rider(row: sqlite3.Row) -> Rider:
        # Import inside method to avoid circular imports at module load time.
        from datetime import datetime, timezone

        from domain.riders import Rider

        created_at_str: str = row["created_at"]
        if created_at_str.endswith("Z"):
            created_at_str = created_at_str.replace("Z", "+00:00")
        created_at = datetime.fromisoformat(created_at_str)
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        return Rider(
            tag_id=row["tag_id"],
            bib=row["bib"],
            name=row["name"],
            created_at=created_at,
        )

    # ---- Tag-event persistence ------------------------------------------

    def append_event(self, event: TagEventDTO) -> None:
        """Persist a TagEventDTO to tag_events."""
        self._execute(
            """
            INSERT INTO tag_events
                (tag_id, event_type, timestamp, antenna, rssi, reader_serial)
            VALUES (?, ?, ?, ?, ?, ?);
            """,
            (
                event.tag_id,
                event.event_type.value,
                event.timestamp,
                event.antenna,
                event.rssi,
                event.reader_serial,
            ),
        )

    def iter_events(self) -> Iterator[TagEventDTO]:
        """Yield TagEventDTOs in insertion order (by id). Used for replay on startup."""
        from models_api import EventType, TagEventDTO

        rows = self._conn.execute(
            "SELECT tag_id, event_type, timestamp, antenna, rssi, reader_serial "
            "FROM tag_events ORDER BY id;"
        ).fetchall()
        for row in rows:
            yield TagEventDTO(
                source="replay",
                reader_ip="0.0.0.0",
                reader_serial=row["reader_serial"],
                timestamp=row["timestamp"],
                event_type=EventType(row["event_type"]),
                tag_id=row["tag_id"],
                antenna=row["antenna"],
                rssi=row["rssi"],
            )

    def count_events(self) -> int:
        """Return total number of persisted tag events. Used in tests."""
        row = self._conn.execute("SELECT COUNT(*) FROM tag_events;").fetchone()
        return row[0]

    # ---- Lifecycle ------------------------------------------------------

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        self._conn.close()
