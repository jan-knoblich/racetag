"""SQLite persistence layer for Racetag backend.

Originally W-050 (single race). Extended 2026-05-25 to multi-race for the
24.6. two-race event day — see ``docs/issues/TODO-multi-race-events.md``.

Schema overview:
- ``races`` — one row per race (name, schedule, total_laps, started/ended state).
- ``riders`` — race-scoped, primary key is ``(race_id, tag_id)`` so the same
  physical tag can map to different bibs/names in different races.
- ``tag_events`` — race-scoped via a ``race_id`` column; the active race
  receives all incoming events.
- ``meta`` — small k/v store; the **active race id** lives under the key
  ``active_race_id``.

All rider / event accessors accept an optional ``race_id`` parameter that
defaults to the active race. This preserves the pre-multi-race call sites
without rewriting them.

Durability: opened with ``journal_mode=WAL`` + ``synchronous=FULL``. A
``threading.Lock`` serialises all writes.
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Iterator, List, Optional

if TYPE_CHECKING:
    from domain.races import Race
    from domain.riders import Rider
    from models_api import TagEventDTO


# ---------------------------------------------------------------------------
# DDL — idempotent. For databases that already exist with the pre-multi-race
# schema (riders/tag_events without ``race_id``), the migration logic in
# ``_migrate_legacy()`` runs after the DDL and rebuilds those tables.
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS races (
    id                   TEXT PRIMARY KEY,
    name                 TEXT NOT NULL,
    scheduled_at         TEXT,
    total_laps           INTEGER NOT NULL DEFAULT 5,
    started              INTEGER NOT NULL DEFAULT 0,
    started_at           TEXT,
    ended                INTEGER NOT NULL DEFAULT 0,
    ended_at             TEXT,
    created_at           TEXT NOT NULL,
    snapshot_interval_s  INTEGER,
    finish_mode          TEXT NOT NULL DEFAULT 'leader',
    duration_s           INTEGER,
    final_laps           INTEGER
);

CREATE TABLE IF NOT EXISTS riders (
    race_id    TEXT NOT NULL,
    tag_id     TEXT NOT NULL,
    bib        TEXT NOT NULL,
    name       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status     TEXT,
    PRIMARY KEY (race_id, tag_id),
    FOREIGN KEY (race_id) REFERENCES races(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS tag_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    race_id       TEXT NOT NULL,
    tag_id        TEXT NOT NULL,
    event_type    TEXT NOT NULL,
    timestamp     TEXT NOT NULL,
    antenna       INTEGER,
    rssi          REAL,
    reader_serial TEXT,
    received_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (race_id) REFERENCES races(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_tag_events_time ON tag_events(timestamp);
-- idx_tag_events_race is created in _migrate_legacy() AFTER the migration adds
-- the race_id column to legacy tag_events tables (creating the index here would
-- fail on a pre-multi-race DB that doesn't have the column yet).

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


# ---------------------------------------------------------------------------
# Helpers for datetime <-> ISO string round-tripping (used by Race CRUD).
# ---------------------------------------------------------------------------

def _iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    if s.endswith("Z"):
        s = s.replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class Storage:
    """Thin SQLite wrapper with multi-race support."""

    def __init__(self, db_path: str | Path, *, wal_mode: bool = True) -> None:
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            str(db_path),
            isolation_level=None,   # autocommit; explicit BEGIN/COMMIT
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row

        if wal_mode:
            self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=FULL;")
        self._conn.execute("PRAGMA foreign_keys=ON;")

        with self._lock:
            self._conn.executescript(_DDL)

        # Migrate pre-multi-race rows to the new schema, then make sure there
        # is at least one race + an active id set.
        self._migrate_legacy()
        # Add columns introduced after the initial multi-race migration.
        self._ensure_races_snapshot_interval_column()
        self._ensure_races_race_format_columns()
        self._ensure_riders_status_column()
        self._ensure_default_race()
        # Create the race index now that tag_events.race_id is guaranteed to exist.
        with self._lock:
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tag_events_race ON tag_events(race_id);"
            )
        self._ensure_tag_events_unique_index()

    def _ensure_tag_events_unique_index(self) -> None:
        """Create the idempotency index for tag_events (AUDIT-2026-07 H4).

        Duplicate audit rows (same race/tag/type/timestamp) come from
        re-POSTed batches after a crash-during-commit or repeated spool
        drains; on replay each duplicate used to count as a real lap.

        Pre-existing DBs may already CONTAIN duplicates, which would make
        CREATE UNIQUE INDEX fail — so on first migration we delete exact
        duplicates (keeping the lowest id). Guarded by an index-exists check
        so the dedupe scan doesn't run on every startup.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='index' AND name='idx_tag_events_unique';"
            ).fetchone()
            if row is not None:
                return
            self._conn.execute(
                """
                DELETE FROM tag_events WHERE id NOT IN (
                    SELECT MIN(id) FROM tag_events
                    GROUP BY race_id, tag_id, event_type, timestamp
                );
                """
            )
            self._conn.execute(
                "CREATE UNIQUE INDEX idx_tag_events_unique "
                "ON tag_events(race_id, tag_id, event_type, timestamp);"
            )

    # ---- internal helpers -----------------------------------------------

    def _execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """Run a single statement inside BEGIN/COMMIT under the write lock."""
        with self._lock:
            self._conn.execute("BEGIN;")
            try:
                cur = self._conn.execute(sql, params)
                self._conn.execute("COMMIT;")
                return cur
            except Exception:
                self._conn.execute("ROLLBACK;")
                raise

    def _table_columns(self, table: str) -> set[str]:
        rows = self._conn.execute(f"PRAGMA table_info('{table}');").fetchall()
        return {r["name"] for r in rows}

    def _table_exists(self, table: str) -> bool:
        row = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?;",
            (table,),
        ).fetchone()
        return row is not None

    # ---- Bootstrap / migration -----------------------------------------

    def _ensure_races_snapshot_interval_column(self) -> None:
        """Add the `snapshot_interval_s` column to a pre-existing races table.

        Idempotent: if the column already exists (e.g. fresh DB created from
        the current DDL above), this is a no-op. Old DBs from before the
        auto-snapshot feature need the column added so the rest of the code
        can SELECT/UPDATE it.
        """
        cols = self._table_columns("races")
        if "snapshot_interval_s" in cols:
            return
        with self._lock:
            self._conn.execute(
                "ALTER TABLE races ADD COLUMN snapshot_interval_s INTEGER;"
            )

    def _ensure_riders_status_column(self) -> None:
        """Add the riders.status column to a pre-existing riders table
        (AUDIT-2026-07 F3). Idempotent."""
        cols = self._table_columns("riders")
        if "status" in cols:
            return
        with self._lock:
            self._conn.execute("ALTER TABLE riders ADD COLUMN status TEXT;")

    def _ensure_races_race_format_columns(self) -> None:
        """Add the finish_mode / duration_s / final_laps columns to a
        pre-existing races table (AUDIT-2026-07 F1+F2). Idempotent."""
        cols = self._table_columns("races")
        with self._lock:
            if "finish_mode" not in cols:
                self._conn.execute(
                    "ALTER TABLE races ADD COLUMN finish_mode TEXT NOT NULL DEFAULT 'leader';"
                )
            if "duration_s" not in cols:
                self._conn.execute(
                    "ALTER TABLE races ADD COLUMN duration_s INTEGER;"
                )
            if "final_laps" not in cols:
                self._conn.execute(
                    "ALTER TABLE races ADD COLUMN final_laps INTEGER;"
                )

    def _migrate_legacy(self) -> None:
        """Migrate pre-multi-race ``riders`` / ``tag_events`` rows into the new
        race-scoped schema, attributing them to a default race built from the
        old meta keys (``total_laps``, ``race_started_at``).

        Idempotent: if both tables already carry ``race_id``, this is a no-op.
        """
        rider_cols = self._table_columns("riders")
        event_cols = self._table_columns("tag_events")
        needs_rider_migration = rider_cols and "race_id" not in rider_cols
        needs_event_migration = event_cols and "race_id" not in event_cols
        if not (needs_rider_migration or needs_event_migration):
            return

        # Build a default race from any legacy meta we find.
        default_race = self._build_default_race_from_legacy_meta()
        self._insert_race(default_race)
        self._set_active_race_id_locked(default_race.id)
        default_id = default_race.id

        # executescript() and ALTER TABLE both auto-commit on their own, so
        # wrapping them in BEGIN/COMMIT would conflict. Run each statement
        # individually inside the write lock; on a fresh DB this whole block
        # is a no-op anyway.
        with self._lock:
            if needs_rider_migration:
                # Rebuild riders with the new (race_id, tag_id) primary key.
                # Use individual execute() calls (not executescript, which
                # would commit any in-flight transaction halfway through).
                self._conn.execute(
                    """
                    CREATE TABLE riders_new (
                        race_id    TEXT NOT NULL,
                        tag_id     TEXT NOT NULL,
                        bib        TEXT NOT NULL,
                        name       TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        PRIMARY KEY (race_id, tag_id),
                        FOREIGN KEY (race_id) REFERENCES races(id) ON DELETE CASCADE
                    );
                    """
                )
                self._conn.execute(
                    "INSERT INTO riders_new (race_id, tag_id, bib, name, created_at) "
                    "SELECT ?, tag_id, bib, name, created_at FROM riders;",
                    (default_id,),
                )
                self._conn.execute("DROP TABLE riders;")
                self._conn.execute("ALTER TABLE riders_new RENAME TO riders;")
            if needs_event_migration:
                self._conn.execute(
                    f"ALTER TABLE tag_events ADD COLUMN race_id TEXT NOT NULL DEFAULT '{default_id}';"
                )

    def _build_default_race_from_legacy_meta(self) -> "Race":
        """Build a Race object from old single-race meta keys (or sensible defaults)."""
        from domain.races import Race  # local import to avoid cycle

        total_laps_raw = self._get_meta_locked("total_laps")
        try:
            total_laps = int(total_laps_raw) if total_laps_raw else 5
        except ValueError:
            total_laps = 5
        started_at = _parse_iso(self._get_meta_locked("race_started_at"))
        started = started_at is not None
        return Race(
            name="Default race",
            total_laps=total_laps,
            started=started,
            started_at=started_at,
        )

    def _ensure_default_race(self) -> None:
        """If no race rows exist or no active race id is set, create + select one.

        Called on every startup so a brand-new install boots into a usable state.
        """
        active_id = self.get_active_race_id()
        if active_id is not None and self.get_race(active_id) is not None:
            return  # already good
        any_race = self._conn.execute("SELECT id FROM races LIMIT 1;").fetchone()
        if any_race is None:
            from domain.races import Race  # local import
            default_race = Race(name="Default race")
            self._insert_race(default_race)
            self._set_active_race_id_locked(default_race.id)
        else:
            # Races exist but no active id → pick the first.
            self._set_active_race_id_locked(any_race["id"])

    # ---- Race CRUD ------------------------------------------------------

    _RACE_INSERT_SQL = """
        INSERT INTO races
            (id, name, scheduled_at, total_laps, started, started_at, ended,
             ended_at, created_at, snapshot_interval_s, finish_mode,
             duration_s, final_laps)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
    """

    @staticmethod
    def _race_insert_params(race: "Race") -> tuple:
        return (
            race.id,
            race.name,
            _iso(race.scheduled_at),
            race.total_laps,
            1 if race.started else 0,
            _iso(race.started_at),
            1 if race.ended else 0,
            _iso(race.ended_at),
            _iso(race.created_at),
            race.snapshot_interval_s,
            race.finish_mode,
            race.duration_s,
            race.final_laps,
        )

    def _insert_race(self, race: "Race") -> None:
        """Insert without locking (used inside migration which already holds the lock)."""
        self._conn.execute(self._RACE_INSERT_SQL, self._race_insert_params(race))

    def create_race(self, race: "Race") -> "Race":
        """Insert a new race row and return the (persisted) Race."""
        self._execute(self._RACE_INSERT_SQL, self._race_insert_params(race))
        return race

    def get_race(self, race_id: str) -> Optional["Race"]:
        row = self._conn.execute(
            "SELECT * FROM races WHERE id = ?;", (race_id,)
        ).fetchone()
        return self._row_to_race(row) if row else None

    def list_races(self) -> List["Race"]:
        """Return all races, ordered by scheduled_at (nulls last) then created_at."""
        rows = self._conn.execute(
            """
            SELECT * FROM races
            ORDER BY
                CASE WHEN scheduled_at IS NULL THEN 1 ELSE 0 END,
                scheduled_at,
                created_at;
            """
        ).fetchall()
        return [self._row_to_race(r) for r in rows]

    def update_race(self, race_id: str, **fields) -> Optional["Race"]:
        """Partial update on a race row. Returns the updated Race or None if missing."""
        if not fields:
            return self.get_race(race_id)
        allowed = {"name", "scheduled_at", "total_laps", "started", "started_at",
                   "ended", "ended_at", "snapshot_interval_s", "finish_mode",
                   "duration_s", "final_laps"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"unknown race fields: {sorted(unknown)}")
        # Normalize datetime fields to ISO strings, booleans to 0/1.
        sets, params = [], []
        for k, v in fields.items():
            if k in {"scheduled_at", "started_at", "ended_at"}:
                v = _iso(v)
            elif k in {"started", "ended"}:
                v = 1 if v else 0
            sets.append(f"{k} = ?")
            params.append(v)
        params.append(race_id)
        self._execute(
            f"UPDATE races SET {', '.join(sets)} WHERE id = ?;",
            tuple(params),
        )
        return self.get_race(race_id)

    def delete_race(self, race_id: str) -> bool:
        """Delete a race + cascade riders + tag_events. Returns True if removed."""
        cur = self._execute("DELETE FROM races WHERE id = ?;", (race_id,))
        return cur.rowcount > 0

    @staticmethod
    def _row_to_race(row: sqlite3.Row) -> "Race":
        from domain.races import Race

        def _opt_int(col: str) -> Optional[int]:
            # Columns added by later migrations may be absent on very old rows.
            try:
                raw = row[col]
            except (KeyError, IndexError):
                return None
            return int(raw) if raw is not None else None

        try:
            finish_mode = row["finish_mode"] or "leader"
        except (KeyError, IndexError):
            finish_mode = "leader"

        return Race(
            id=row["id"],
            name=row["name"],
            scheduled_at=_parse_iso(row["scheduled_at"]),
            total_laps=int(row["total_laps"]),
            started=bool(row["started"]),
            started_at=_parse_iso(row["started_at"]),
            ended=bool(row["ended"]),
            ended_at=_parse_iso(row["ended_at"]),
            created_at=_parse_iso(row["created_at"]) or datetime.now(timezone.utc),
            snapshot_interval_s=_opt_int("snapshot_interval_s"),
            finish_mode=finish_mode,
            duration_s=_opt_int("duration_s"),
            final_laps=_opt_int("final_laps"),
        )

    # ---- Active race ----------------------------------------------------

    def get_active_race_id(self) -> Optional[str]:
        return self._get_meta_locked("active_race_id")

    def set_active_race_id(self, race_id: str) -> None:
        # Validate the race exists before pointing at it.
        if self.get_race(race_id) is None:
            raise ValueError(f"unknown race_id: {race_id}")
        self._set_active_race_id_locked(race_id)

    def _set_active_race_id_locked(self, race_id: str) -> None:
        """set_meta for active_race_id, without re-validating the race exists.

        Used during bootstrap/migration where the race row was just created in
        the same transaction.
        """
        self.set_meta("active_race_id", race_id)

    def _require_race_id(self, race_id: Optional[str]) -> str:
        """Resolve race_id, defaulting to the active race; raise if unset."""
        if race_id is not None:
            return race_id
        active = self.get_active_race_id()
        if active is None:
            raise RuntimeError("no active race set and no race_id provided")
        return active

    # ---- Rider CRUD (race-scoped) --------------------------------------

    def upsert_rider(self, rider: "Rider", race_id: Optional[str] = None) -> None:
        """Insert or replace a rider row, scoped to a race (default: active)."""
        rid = self._require_race_id(race_id)
        created_at_str = (
            rider.created_at.isoformat(timespec="milliseconds").replace("+00:00", "Z")
            if hasattr(rider.created_at, "isoformat")
            else str(rider.created_at)
        )
        # status is deliberately NOT in the UPDATE clause: a CSV re-import or
        # re-coupling must not wipe a DNF/DNS/DSQ the operator already set
        # (F3). Status is managed through set_rider_status().
        self._execute(
            """
            INSERT INTO riders (race_id, tag_id, bib, name, created_at, status)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(race_id, tag_id) DO UPDATE SET
                bib        = excluded.bib,
                name       = excluded.name,
                created_at = excluded.created_at;
            """,
            (rid, rider.tag_id, rider.bib, rider.name, created_at_str, rider.status),
        )

    def set_rider_status(
        self, tag_id: str, status: Optional[str], race_id: Optional[str] = None
    ) -> bool:
        """Set (or clear with None) a rider's result status. Returns False if
        no such rider exists in the race."""
        rid = self._require_race_id(race_id)
        cur = self._execute(
            "UPDATE riders SET status = ? WHERE race_id = ? AND tag_id = ?;",
            (status, rid, tag_id),
        )
        return cur.rowcount > 0

    def get_rider(self, tag_id: str, race_id: Optional[str] = None) -> Optional["Rider"]:
        rid = self._require_race_id(race_id)
        row = self._conn.execute(
            "SELECT tag_id, bib, name, created_at, status FROM riders "
            "WHERE race_id = ? AND tag_id = ?;",
            (rid, tag_id),
        ).fetchone()
        return self._row_to_rider(row) if row else None

    def list_riders(self, race_id: Optional[str] = None) -> List["Rider"]:
        rid = self._require_race_id(race_id)
        rows = self._conn.execute(
            "SELECT tag_id, bib, name, created_at, status FROM riders "
            "WHERE race_id = ? ORDER BY rowid;",
            (rid,),
        ).fetchall()
        return [self._row_to_rider(r) for r in rows]

    def delete_rider(self, tag_id: str, race_id: Optional[str] = None) -> bool:
        rid = self._require_race_id(race_id)
        cur = self._execute(
            "DELETE FROM riders WHERE race_id = ? AND tag_id = ?;",
            (rid, tag_id),
        )
        return cur.rowcount > 0

    @staticmethod
    def _row_to_rider(row: sqlite3.Row) -> "Rider":
        from datetime import datetime, timezone
        from domain.riders import Rider

        created_at_str: str = row["created_at"]
        if created_at_str.endswith("Z"):
            created_at_str = created_at_str.replace("Z", "+00:00")
        created_at = datetime.fromisoformat(created_at_str)
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        try:
            status = row["status"]
        except (KeyError, IndexError):
            status = None
        return Rider(
            tag_id=row["tag_id"],
            bib=row["bib"],
            name=row["name"],
            created_at=created_at,
            status=status,
        )

    # ---- Tag-event persistence (race-scoped) ---------------------------

    def append_event(self, event: "TagEventDTO", race_id: Optional[str] = None) -> None:
        """Persist one event. Idempotent (AUDIT-2026-07 H4): re-delivery of an
        identical event — reader-service re-POST after a crash-during-commit,
        spool drain replaying a partially delivered outage window — must not
        create a duplicate audit row, otherwise replay-on-restart would count
        the duplicate as a real lap. Enforced by the unique index on
        (race_id, tag_id, event_type, timestamp) + INSERT OR IGNORE.
        """
        rid = self._require_race_id(race_id)
        self._execute(
            """
            INSERT OR IGNORE INTO tag_events
                (race_id, tag_id, event_type, timestamp, antenna, rssi, reader_serial)
            VALUES (?, ?, ?, ?, ?, ?, ?);
            """,
            (
                rid,
                event.tag_id,
                event.event_type.value,
                event.timestamp,
                event.antenna,
                event.rssi,
                event.reader_serial,
            ),
        )

    def iter_events(self, race_id: Optional[str] = None) -> Iterator["TagEventDTO"]:
        """Yield TagEventDTOs for the given race in CHRONOLOGICAL order (replay).

        Ordered by timestamp (id as tiebreaker), not insertion order
        (AUDIT-2026-07 H3): spool-recovered events are inserted AFTER newer
        live events, so insertion order is not chronological whenever a spool
        drain happened. Replaying out of order used to rewind last_pass_time
        and over-count laps. timestamps are uniform ISO-8601 UTC "Z" strings,
        so lexicographic ordering == chronological ordering.
        """
        from models_api import EventType, TagEventDTO

        rid = self._require_race_id(race_id)
        rows = self._conn.execute(
            "SELECT tag_id, event_type, timestamp, antenna, rssi, reader_serial "
            "FROM tag_events WHERE race_id = ? ORDER BY timestamp, id;",
            (rid,),
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

    def count_events(self, race_id: Optional[str] = None) -> int:
        rid = self._require_race_id(race_id)
        row = self._conn.execute(
            "SELECT COUNT(*) FROM tag_events WHERE race_id = ?;", (rid,)
        ).fetchone()
        return row[0]

    def update_rider_bib_name_all_races(self, tag_id: str, bib: str, name: str) -> int:
        """Set bib/name for this tag in EVERY race that has it registered.

        Day-model for multi-race events (Karli Krit): one tag + one number
        per PERSON for the whole day, so a late-entry name applies to all
        races the tag was pre-imported into. Update-only by design — races
        that don't know the tag are left alone. Status is never touched.
        Returns the number of race rows updated.
        """
        cur = self._execute(
            "UPDATE riders SET bib = ?, name = ? WHERE tag_id = ?;",
            (bib, name, tag_id),
        )
        return cur.rowcount if cur is not None else 0

    def tag_read_summary(self, race_id: Optional[str] = None) -> list[dict]:
        """One row per distinct tag read in the race, in first-read order.

        First-read order == the order the operator waved the tags, which is
        what the tag-inventory CSV export (GET /tags.csv) presents to the
        user; ``reads`` doubles as a read-quality check per tag.
        """
        rid = self._require_race_id(race_id)
        rows = self._conn.execute(
            "SELECT tag_id, COUNT(*) AS reads, MIN(timestamp) AS first_seen "
            "FROM tag_events WHERE race_id = ? "
            "GROUP BY tag_id ORDER BY first_seen, tag_id;",
            (rid,),
        ).fetchall()
        return [
            {"tag_id": r["tag_id"], "reads": r["reads"], "first_seen": r["first_seen"]}
            for r in rows
        ]

    def clear_events(self, race_id: Optional[str] = None) -> None:
        rid = self._require_race_id(race_id)
        self._execute("DELETE FROM tag_events WHERE race_id = ?;", (rid,))

    def find_last_event_id_for_tag(
        self,
        tag_id: str,
        race_id: Optional[str] = None,
        event_type: Optional[str] = None,
    ) -> Optional[int]:
        """Return the id (PK) of the most-recently-inserted event for *tag_id*
        in the given race, or None if no event exists.

        Used by the manual-lap-correction endpoint: an operator can delete the
        most-recent lap when the reader miscounted or recorded a phantom.

        If event_type is given, only events of that type are considered (e.g.
        only 'arrive' events count as laps).
        """
        rid = self._require_race_id(race_id)
        if event_type is not None:
            row = self._conn.execute(
                "SELECT id FROM tag_events "
                "WHERE race_id = ? AND tag_id = ? AND event_type = ? "
                "ORDER BY id DESC LIMIT 1;",
                (rid, tag_id, event_type),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT id FROM tag_events "
                "WHERE race_id = ? AND tag_id = ? "
                "ORDER BY id DESC LIMIT 1;",
                (rid, tag_id),
            ).fetchone()
        return row["id"] if row else None

    def delete_event_by_id(self, event_id: int) -> bool:
        """Delete the tag_events row with the given primary key.

        Returns True if a row was deleted, False if no row matched. Used by
        the manual-lap-correction endpoint to drop a single mis-read.
        """
        cur = self._execute(
            "DELETE FROM tag_events WHERE id = ?;", (event_id,)
        )
        return cur.rowcount > 0

    def delete_events_for_tag(
        self, tag_id: str, race_id: Optional[str] = None
    ) -> int:
        """Delete ALL events for *tag_id* in the given race (default: active).

        Rider-reset (2026-07-25): a botched measurement — e.g. a TT run where
        the start read caught the rider while staging — is wiped in one action
        so the rider can roll over the line again for a fresh attempt.
        Returns the number of rows deleted.
        """
        rid = self._require_race_id(race_id)
        cur = self._execute(
            "DELETE FROM tag_events WHERE race_id = ? AND tag_id = ?;",
            (rid, tag_id),
        )
        return cur.rowcount

    def count_events_by_antenna(
        self, window_s: int, race_id: Optional[str] = None
    ) -> dict:
        """{antenna_id: count} for events in the last window_s seconds of the
        given race (default: active)."""
        from datetime import datetime, timezone, timedelta
        rid = self._require_race_id(race_id)
        cutoff = (
            datetime.now(timezone.utc) - timedelta(seconds=window_s)
        ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        rows = self._conn.execute(
            "SELECT antenna, COUNT(*) AS cnt "
            "FROM tag_events "
            "WHERE race_id = ? AND timestamp >= ? AND antenna IS NOT NULL "
            "GROUP BY antenna;",
            (rid, cutoff),
        ).fetchall()
        return {row["antenna"]: row["cnt"] for row in rows}

    # ---- Meta key/value store ------------------------------------------

    def get_meta(self, key: str) -> Optional[str]:
        return self._get_meta_locked(key)

    def _get_meta_locked(self, key: str) -> Optional[str]:
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key = ?;", (key,)
        ).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self._execute(
            """
            INSERT INTO meta (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value;
            """,
            (key, value),
        )

    # ---- Lifecycle ------------------------------------------------------

    def close(self) -> None:
        self._conn.close()

    # ---- Snapshots / online backup -------------------------------------

    def backup_to(self, dst_path: "str | Path") -> None:
        """Write an online SQLite backup of the live DB to *dst_path*.

        Uses sqlite3's online backup API: the source connection stays usable,
        WAL is reconciled, and the target file is a consistent, point-in-time
        snapshot. Holds the write lock during the call to keep the source
        stable.
        """
        target = sqlite3.connect(str(dst_path))
        try:
            with self._lock:
                self._conn.backup(target)
        finally:
            target.close()
