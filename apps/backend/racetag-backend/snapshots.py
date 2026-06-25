"""Auto-snapshot background worker for the active race.

Writes two artefacts at the configured interval (per-race), with a rolling
retention window so the directory doesn't grow without bound:

  <data_dir>/snapshots/<race_id>/<UTC-ISO>.csv  — classification snapshot
  <data_dir>/snapshots/<race_id>/<UTC-ISO>.db   — sqlite online backup

This is independent of the manual `Export results` button: the user wanted
*regular* persistence during the race so that an app crash (or the
"BUG-004"-style ended-race rehydrate bug) can never blank-out a result.

Configuration is per-race: `Race.snapshot_interval_s`. None or 0 disables
snapshots for that race.

Design notes:
- The worker thread polls every POLL_INTERVAL_S seconds, looks up the
  *current* active race + its snapshot_interval_s, and writes if the most
  recent snapshot for that race id is older than the interval.
- The interval is re-read on every tick, so changing it mid-race (via PATCH
  /races/{id}) takes effect on the next poll.
- The snapshotter is started after FastAPI boots and stopped on shutdown.
- Errors during snapshot writing are logged but never crash the worker.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger("racetag.snapshots")


POLL_INTERVAL_S = 5.0
ROLLING_WINDOW = 30


def _utc_iso_stamp(now: datetime) -> str:
    """Filesystem-safe UTC stamp with millisecond resolution: 20260625T180515123Z.

    Millisecond precision avoids stem collisions between snapshots taken in
    the same wall-clock second (review #14: two manual POSTs back-to-back used
    to clobber the first file).
    """
    return now.strftime("%Y%m%dT%H%M%S") + f"{now.microsecond // 1000:03d}Z"


def cleanup_old_snapshots(snap_dir: Path, keep: int = ROLLING_WINDOW) -> int:
    """Delete all but the most recent *keep* CSV+DB pairs in *snap_dir*.

    Pairs are matched by stem: ``20260625T180515Z.csv`` + ``20260625T180515Z.db``.
    Both members of an old pair are deleted together so we never end up with a
    lonely CSV or lonely DB.

    Returns the number of files deleted (CSV+DB combined).
    """
    if not snap_dir.exists():
        return 0
    stems = sorted({p.stem for p in snap_dir.iterdir() if p.suffix in {".csv", ".db"}})
    to_delete = stems[:-keep] if len(stems) > keep else []
    deleted = 0
    for stem in to_delete:
        for suffix in (".csv", ".db"):
            f = snap_dir / f"{stem}{suffix}"
            try:
                if f.exists():
                    f.unlink()
                    deleted += 1
            except OSError as e:
                logger.warning("failed to delete snapshot %s: %s", f, e)
    return 0 if not to_delete else deleted


def write_snapshot(
    data_dir: Path,
    race_id: str,
    csv_text: str,
    storage_backup: Callable[[str], None],
    now: Optional[datetime] = None,
    *,
    keep: int = ROLLING_WINDOW,
) -> tuple[Path, Path]:
    """Write one CSV + one DB snapshot for *race_id* and prune to *keep*.

    Returns (csv_path, db_path). The DB is written via the storage_backup
    callable (Storage.backup_to) so the source connection stays consistent.
    """
    now = now or datetime.now(timezone.utc)
    snap_dir = data_dir / "snapshots" / race_id
    snap_dir.mkdir(parents=True, exist_ok=True)
    stem = _utc_iso_stamp(now)
    csv_path = snap_dir / f"{stem}.csv"
    db_path = snap_dir / f"{stem}.db"
    csv_path.write_text(csv_text, encoding="utf-8")
    storage_backup(str(db_path))
    cleanup_old_snapshots(snap_dir, keep=keep)
    return csv_path, db_path


def record_manual_snapshot(snapshotter: "Snapshotter | None", race_id: str, now: datetime) -> None:
    """Tell the running Snapshotter that a manual snapshot was just written.

    Without this, the next interval tick would write a redundant snapshot
    immediately because the snapshotter has no record of the manual one
    (review #13).
    """
    if snapshotter is None:
        return
    snapshotter._last_snapshot_at[race_id] = now


class Snapshotter(threading.Thread):
    """Background thread that writes periodic snapshots of the active race.

    The thread polls every POLL_INTERVAL_S seconds. On each tick it calls
    ``get_state()`` which returns either ``None`` (snapshotting disabled or
    no active race) or a tuple
    ``(race_id, interval_s, csv_text, backup_fn)`` describing what to snapshot
    *right now*. If ``last_snapshot_at[race_id] + interval_s <= now``, the
    tick writes a snapshot.

    The provider closes over the latest app state so we don't need any
    module-level imports here.
    """

    def __init__(
        self,
        data_dir: Path,
        get_state: Callable[[], Optional[tuple]],
        *,
        poll_interval_s: float = POLL_INTERVAL_S,
        keep: int = ROLLING_WINDOW,
    ) -> None:
        super().__init__(daemon=True, name="racetag-snapshotter")
        self._data_dir = data_dir
        self._get_state = get_state
        self._poll_interval_s = poll_interval_s
        self._keep = keep
        self._stop = threading.Event()
        self._last_snapshot_at: dict[str, datetime] = {}

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        logger.info(
            "Snapshotter started: data_dir=%s poll_interval=%.1fs keep=%d",
            self._data_dir, self._poll_interval_s, self._keep,
        )
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:  # never crash the worker
                logger.exception("snapshot tick raised; continuing")
            # Sleep but wake early if stop() is signalled
            self._stop.wait(timeout=self._poll_interval_s)
        logger.info("Snapshotter stopped")

    def _tick(self) -> None:
        state = self._get_state()
        if state is None:
            return
        race_id, interval_s, csv_provider, backup_fn = state
        if not race_id or not interval_s or interval_s <= 0:
            return
        now = datetime.now(timezone.utc)
        last = self._last_snapshot_at.get(race_id)
        if last is not None and (now - last).total_seconds() < interval_s:
            return
        # Only build the CSV when we're about to write — avoids rebuilding it
        # on every poll tick during the long quiet intervals between writes.
        try:
            csv_text = csv_provider() if callable(csv_provider) else csv_provider
        except Exception:
            logger.exception("snapshot CSV builder raised; skipping tick")
            return
        try:
            csv_path, db_path = write_snapshot(
                self._data_dir, race_id, csv_text, backup_fn, now=now, keep=self._keep,
            )
            self._last_snapshot_at[race_id] = now
            logger.info("snapshot written: %s, %s", csv_path.name, db_path.name)
        except Exception:
            logger.exception("failed to write snapshot for race %s", race_id)


def list_snapshots(data_dir: Path, race_id: str) -> list[dict]:
    """Return a list of {stem, csv_path, db_path, written_at} for one race,
    sorted oldest-first. Used by tests + the /races/{id}/snapshots endpoint.
    """
    snap_dir = data_dir / "snapshots" / race_id
    if not snap_dir.exists():
        return []
    out = []
    for stem in sorted({p.stem for p in snap_dir.iterdir() if p.suffix in {".csv", ".db"}}):
        csv_path = snap_dir / f"{stem}.csv"
        db_path = snap_dir / f"{stem}.db"
        out.append({
            "stem": stem,
            "csv_path": csv_path,
            "db_path": db_path,
            "csv_exists": csv_path.exists(),
            "db_exists": db_path.exists(),
        })
    return out


__all__ = [
    "POLL_INTERVAL_S",
    "ROLLING_WINDOW",
    "Snapshotter",
    "cleanup_old_snapshots",
    "list_snapshots",
    "write_snapshot",
]
