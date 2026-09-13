from __future__ import annotations

import asyncio
import collections
import logging
import threading
import time
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Depends, HTTPException, Query, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field, StrictBool

from domain.config import Config, ConfigStore, is_valid_ipv4
from domain.race import RaceState
from domain.riders import Rider, RiderStore
from models_api import (
    EventType,
    TagEventDTO,
    ParticipantDTO,
    ClassificationDTO,
    RaceDTO,
    RaceSummaryDTO,
    RaceListDTO,
    RaceCreateDTO,
    RaceUpdateDTO,
    BatchIngestResultDTO,
    TagEventBatchDTO,
    RiderDTO,
    RiderCreateDTO,
    RiderStatusDTO,
    RidersListDTO,
    RecentReadDTO,
    RecentReadsListDTO,
    ManualLapAddDTO,
    ManualLapResultDTO,
)
from storage import Storage
from reader_status_hub import ReaderStatusHub, ReaderStatusIn

logger = logging.getLogger("racetag.backend")

# ---------------------------------------------------------------------------
# API Key auth
# ---------------------------------------------------------------------------
# RACETAG_API_KEY is intentionally NOT set in the default packaged build.
# When unset the require_api_key dependency becomes a no-op, so the app works
# out-of-the-box without any key management (security is not a priority for
# the packaged desktop build — see W-040 / ISSUES.md P1-7).
API_KEY_HEADER_NAME = "X-API-Key"
_API_KEY = os.getenv("RACETAG_API_KEY")
_RACE_TOTAL_LAPS = int(os.getenv("RACE_TOTAL_LAPS", "5"))
_RACE_MIN_PASS_INTERVAL_S = float(os.getenv("RACE_MIN_PASS_INTERVAL_S", "8.0"))
_READER_IP_ENV = os.getenv("READER_IP", None)
_MIN_LAP_INTERVAL_S_ENV = float(os.getenv("MIN_LAP_INTERVAL_S", "8.0"))
_api_key_header = APIKeyHeader(name=API_KEY_HEADER_NAME, auto_error=False)

def require_api_key(api_key: str = Security(_api_key_header)) -> bool:
    if not _API_KEY:
        return True
    if not api_key or api_key != _API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    return True

# Global dependency only if RACETAG_API_KEY is set
_global_deps = [Depends(require_api_key)] if _API_KEY else []

app = FastAPI(
    title="Racetag Backend",
    dependencies=_global_deps
)

# CORS for local static frontend (adjust origins for production)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# W-050: Storage — open SQLite with WAL + synchronous=FULL durability.
# RACETAG_DATA_DIR defaults to ./data (relative to cwd at startup).
# ---------------------------------------------------------------------------

_data_dir = Path(os.getenv("RACETAG_DATA_DIR", "./data"))
_data_dir.mkdir(parents=True, exist_ok=True)
storage = Storage(_data_dir / "racetag.db")
config_store = ConfigStore(storage)


# ---------------------------------------------------------------------------
# Race state
# ---------------------------------------------------------------------------

# Multi-race (2026-05-25): the active race is the one currently being raced.
# storage.__init__ bootstraps a default race + active_race_id on a fresh DB
# and migrates the legacy single-race tables into a default race on an old DB.
# RaceState here mirrors the active Race row; switching active rebuilds it.

def _load_active_race_state() -> "tuple[RaceState, RiderStore]":
    """Build a RaceState + RiderStore for whatever race is currently active.

    Reads the active Race row from storage and applies legacy meta overrides
    (``total_laps`` and ``race_started_at`` keys) for backward compat with
    pre-multi-race tests that touch meta directly.
    """
    from domain.race import parse_iso as _parse_iso

    active_id = storage.get_active_race_id()
    if active_id is None:
        raise RuntimeError("storage bootstrap failed to provide an active race")
    race_row = storage.get_race(active_id)
    if race_row is None:
        raise RuntimeError(f"active_race_id={active_id} but no race row")

    # total_laps — prefer the legacy meta override if it exists (test compat).
    total_laps = race_row.total_laps
    legacy_total = storage.get_meta("total_laps")
    if legacy_total:
        try:
            total_laps = int(legacy_total)
        except ValueError:
            pass

    # min_pass_interval_s — the persisted config value is the single source of
    # truth (AUDIT-2026-07 H5): previously PATCH /config wrote a meta key that
    # nothing read back, so the operator-configured cooldown was dead and the
    # live gate always used the env default. Fall back to the env default only
    # when no value was ever persisted.
    min_pass_interval_s = config_store.get_min_lap_interval_s()
    if min_pass_interval_s is None:
        min_pass_interval_s = _RACE_MIN_PASS_INTERVAL_S

    rs = RaceState(
        total_laps=total_laps,
        min_pass_interval_s=min_pass_interval_s,
        race_id=active_id,
        finish_mode=race_row.finish_mode,
        duration_s=race_row.duration_s,
        final_laps=race_row.final_laps,
    )

    # started state — Race row OR legacy meta key
    started_at = race_row.started_at
    legacy_started = storage.get_meta("race_started_at")
    if legacy_started:
        try:
            started_at = _parse_iso(legacy_started)
        except (ValueError, TypeError):
            pass
    if started_at is not None:
        rs.start(now=started_at)

    # ended state — DEFERRED. We deliberately don't call rs.end() here:
    # add_lap() is a no-op post-end, so applying ended BEFORE the event replay
    # below would make every replayed pass a no-op and the standings of an
    # already-ended race would rehydrate empty (BUG-004). Callers must invoke
    # `_apply_ended_state_after_replay(rs)` after replaying persisted events.

    rstore = RiderStore(storage=storage, race_id=active_id)
    # F3 — mirror persisted rider statuses into the RaceState so standings()
    # can sort DNF/DNS/DSQ correctly.
    for _rider in rstore.list():
        if _rider.status:
            rs.status[_rider.tag_id] = _rider.status
    return rs, rstore


def _apply_ended_state_after_replay(rs: "RaceState") -> None:
    """Apply the persisted ended state to `rs` after the event replay loop.

    Split out from `_load_active_race_state` so the replay can mutate the
    in-memory standings BEFORE the race becomes a no-op. See BUG-004.
    """
    if rs.race_id is None:
        return
    race_row = storage.get_race(rs.race_id)
    if race_row is None:
        return
    if race_row.ended and race_row.ended_at is not None:
        rs.end(now=race_row.ended_at)


def _rebuild_active_race_state_in_place() -> None:
    """Rebuild the in-memory RaceState for the active race by replaying
    persisted events from scratch.

    Used after a write that invalidates the current participants/laps view —
    e.g. the manual-lap-remove endpoint deletes a tag_events row, so we can't
    cheaply decrement; we drop everything and replay.

    Mutates the module-level `race` in place (clears participants, resets
    started/ended flags, re-applies them from the race row, replays events,
    re-applies ended). Does NOT touch the SSE subscribers / unknown-tag ring.
    """
    race.participants.clear()
    events.clear()
    race.started = False
    race.started_at = None
    race.ended = False
    race.ended_at = None
    # F1/F2 runtime state is re-derived by the replay below — reset it first
    # so a stale finishing/time-target from before the rebuild can't linger.
    race.finishing = False
    race.finishing_at = None
    race.time_target_laps = None
    # F5 pass-time history is rebuilt by the replay too.
    race.pass_times.clear()

    if race.race_id is not None:
        race_row = storage.get_race(race.race_id)
        if race_row is not None and race_row.started and race_row.started_at is not None:
            race.start(now=race_row.started_at)

    ended_cutoff = _ended_cutoff_iso_for_active_race()
    for ev in storage.iter_events():
        _replay_event(ev, ended_cutoff_iso=ended_cutoff)

    _apply_ended_state_after_replay(race)
    # F3 — statuses live in the RiderStore, not the event log, so re-sync them
    # after the replay (they are not touched by the replay).
    race.status.clear()
    for _rider in rider_store.list():
        if _rider.status:
            race.status[_rider.tag_id] = _rider.status


race, rider_store = _load_active_race_state()

# Debug/event store
events: List[TagEventDTO] = []

# ---------------------------------------------------------------------------
# W-032: SSE subscribers — one asyncio.Queue per /stream connection.
#
# The subscribers list is mutated from both the async /stream handler and the
# sync routes (which FastAPI runs in worker threads), so list mutation is
# guarded by a threading.Lock.
#
# asyncio.Queue is not thread-safe: put_nowait() from a foreign thread neither
# is safe nor wakes the waiting get() until the loop happens to tick (up to
# the 15 s keepalive). Each queue is therefore stored together with the event
# loop that owns it (captured in stream_events via get_running_loop()), and
# _publish always hands the payload over with loop.call_soon_threadsafe —
# from sync routes, the reader-status staleness thread, or the loop itself.
#
# Backward-compat shim: tests append a plain list to `subscribers` and read
# what list.append() collected. Anything that is not a _QueueSubscriber is
# treated as such a legacy list.
# ---------------------------------------------------------------------------


class _QueueSubscriber:
    """An SSE client's queue plus the event loop that consumes it."""

    __slots__ = ("queue", "loop")

    def __init__(self, queue: "asyncio.Queue[Dict[str, Any]]", loop: asyncio.AbstractEventLoop) -> None:
        self.queue = queue
        self.loop = loop


subscribers: List[Any] = []   # elements are _QueueSubscriber or legacy list
_subscribers_lock = threading.Lock()

# Rider registry (W-010) is built per-race by _load_active_race_state() above.

# W-011: ring buffer of recent unknown-tag reads (max 50, newest at right)
_UNKNOWN_TAG_CAP = 50
recent_unknown_tags: collections.deque = collections.deque(maxlen=_UNKNOWN_TAG_CAP)
_unknown_tags_lock = threading.Lock()

# W-075: per-tag throttle for the tag_seen SSE broadcast (serial coupling
# mode). Every arrive — registered AND unknown — emits at most one tag_seen
# frame per tag per interval, taming parked-tag / zone-edge flutter streams.
_TAG_SEEN_MIN_INTERVAL_S = 2.0
_tag_seen_last: Dict[str, float] = {}  # tag_id -> _monotonic() of last emit
_tag_seen_lock = threading.Lock()
_monotonic = time.monotonic  # indirection so tests can monkeypatch the clock


def _tag_seen_should_publish(tag_id: str) -> bool:
    now = _monotonic()
    with _tag_seen_lock:
        last = _tag_seen_last.get(tag_id)
        if last is not None and (now - last) < _TAG_SEEN_MIN_INTERVAL_S:
            return False
        _tag_seen_last[tag_id] = now
        return True


# ---------------------------------------------------------------------------
# W-050: Replay persisted events on startup to restore race state.
#
# We replay without re-persisting (replay_event skips storage.append_event).
# This is idempotent: events already in the DB are not duplicated.
# ---------------------------------------------------------------------------

def _replay_event(ev: TagEventDTO, ended_cutoff_iso: Optional[str] = None) -> None:
    """Apply a single event to in-memory state without writing to storage.

    BUG-003 fix: mirrors the batch-ingest gating — only registered tags affect
    the Race state. Unregistered events stay in tag_events for the audit trail
    but don't reappear as phantom rows in standings after a restart.

    Post-end cutoff: when *ended_cutoff_iso* is set (i.e. the persisted race
    row is ended), events whose timestamp is strictly after the cutoff are
    appended to the audit `events` list but NOT fed into race.add_lap. This
    prevents the BUG-004 fix from inadvertently counting post-end arrives
    (which storage.append_event persists for the audit trail) as real laps on
    every subsequent restart / state rebuild.
    """
    events.append(ev)
    if ev.event_type != EventType.arrive:
        return
    if ev.tag_id not in rider_store:
        return
    if ended_cutoff_iso is not None and ev.timestamp > ended_cutoff_iso:
        # Post-end stray read — already in tag_events for audit, but does not
        # change standings.
        return
    race.add_lap(ev.tag_id, ev.timestamp)


def _ended_cutoff_iso_for_active_race() -> Optional[str]:
    """Return the ISO timestamp at which the active race ended, or None.

    Looked up from the persisted race row (which is the source of truth for
    the ended state). Compared lexically against tag_events.timestamp (also
    ISO 8601 UTC) — both formats are byte-comparable.
    """
    if race.race_id is None:
        return None
    race_row = storage.get_race(race.race_id)
    if race_row is None or not race_row.ended or race_row.ended_at is None:
        return None
    # _iso_or_none equivalent (inlined since this helper is called at module
    # load time, before _iso_or_none is defined further down the file).
    dt = race_row.ended_at
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


_ended_cutoff = _ended_cutoff_iso_for_active_race()
for _ev in storage.iter_events():
    _replay_event(_ev, ended_cutoff_iso=_ended_cutoff)

# BUG-004 fix: apply the persisted ended state only AFTER the replay has
# rebuilt the standings. See _apply_ended_state_after_replay docstring.
_apply_ended_state_after_replay(race)


# ---------------------------------------------------------------------------
# Auto-snapshots (per-race configurable interval). The Snapshotter thread
# pulls the latest state on each tick via _snapshot_state_provider so we don't
# have to deal with re-registration when the active race switches.
# ---------------------------------------------------------------------------

from snapshots import Snapshotter  # noqa: E402  (deferred to use existing globals)


def _snapshot_state_provider():
    """Return the snapshot input tuple for the active race, or None to skip.

    Returns (race_id, interval_s, csv_provider, backup_fn). csv_provider is a
    zero-arg callable that returns the up-to-date CSV string — invoked by the
    Snapshotter only when it actually decides to write (avoids rebuilding the
    CSV on every 5 s poll tick when no snapshot is due).
    """
    if race.race_id is None:
        return None
    race_row = storage.get_race(race.race_id)
    if race_row is None:
        return None
    interval = race_row.snapshot_interval_s
    if interval is None or interval <= 0:
        return None
    # Snapshot is only useful AFTER the race has started — otherwise the CSV
    # contains zero laps for everyone. Skip until start.
    if not race.started:
        return None

    def csv_provider() -> str:
        text, _filename = _build_classification_csv()
        return text

    return (race.race_id, interval, csv_provider, storage.backup_to)


_snapshotter: "Snapshotter | None" = None


@app.on_event("startup")
def _start_snapshotter() -> None:
    """Create a fresh Snapshotter on every FastAPI startup.

    Threads can only be .start()-ed once, so we replace the instance on each
    startup. Tests that re-enter TestClient (which retriggers startup/shutdown)
    rely on this.
    """
    global _snapshotter
    _snapshotter = Snapshotter(_data_dir, _snapshot_state_provider)
    _snapshotter.start()


@app.on_event("shutdown")
def _stop_snapshotter() -> None:
    global _snapshotter
    if _snapshotter is not None:
        _snapshotter.stop()
        _snapshotter = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _rider_to_dto(rider: Rider) -> RiderDTO:
    return RiderDTO(
        tag_id=rider.tag_id,
        bib=rider.bib,
        name=rider.name,
        created_at=rider.created_at,
        status=rider.status,
        verein=rider.verein,
        uci_id=rider.uci_id,
    )


def _publish(payload: Dict[str, Any]) -> None:
    """Fan-out *payload* to all current subscribers.

    Safe to call from any thread and from the event loop itself: queue-based
    subscribers always receive the payload through their own loop's
    call_soon_threadsafe, which also wakes a get() that is already waiting.
    Legacy list-based subscribers (test shim) get a plain append.
    """
    with _subscribers_lock:
        current = list(subscribers)

    for sub in current:
        try:
            if isinstance(sub, _QueueSubscriber):
                sub.loop.call_soon_threadsafe(sub.queue.put_nowait, payload)
            else:
                sub.append(payload)
        except Exception:
            # A subscriber whose loop is already closed (client gone, server
            # shutting down) must not stop the fan-out to the others.
            pass


def _add_sse_subscriber() -> _QueueSubscriber:
    """Register a queue bound to the running event loop. Call from the loop."""
    sub = _QueueSubscriber(asyncio.Queue(), asyncio.get_running_loop())
    with _subscribers_lock:
        subscribers.append(sub)
    return sub


def _remove_sse_subscriber(sub: _QueueSubscriber) -> None:
    with _subscribers_lock:
        try:
            subscribers.remove(sub)
        except ValueError:
            pass


# ---------------------------------------------------------------------------
# Tag-event ingest
# ---------------------------------------------------------------------------

@app.post("/events/tag/batch", response_model=BatchIngestResultDTO)
def post_events_batch(batch: TagEventBatchDTO):
    items = batch.events or []
    if not items:
        return {"events_processed": 0}
    accepted = 0
    for ev in items:
        events.append(ev)
        accepted += 1
        # Update race on ARRIVE (simple rule for MVP)
        if ev.event_type == EventType.arrive:
            # Persist BEFORE mutating in-memory state (W-050 durability policy).
            # All events go into tag_events regardless of registration status —
            # this keeps the audit trail complete (diagnostics panel, BUG-003
            # post-hoc analysis, replay etc.).
            storage.append_event(ev)

            # BUG-003 fix: only registered tags create / update Participant rows
            # in standings. Unregistered tags (stray, bleed-through from other
            # rooms, badge-style retail tags in someone's bag, …) would otherwise
            # show up as phantom rows that the operator has to delete manually.
            # The unknown_tag SSE + recent-reads ring buffer still fire so the
            # "Couple tag → rider" modal can offer them for explicit registration;
            # once coupled, the next pass shows up in standings normally.
            rider = rider_store.get(ev.tag_id)
            is_registered = rider is not None

            # W-075: live feed for the serial coupling panel. Fires for BOTH
            # registered and unknown tags (registered pre-start re-scans are
            # otherwise fully silent — the RECHECK #1 suppression below),
            # throttled per tag. The frontend ignores it outside coupling mode.
            if _tag_seen_should_publish(ev.tag_id):
                _publish({
                    "type": "tag_seen",
                    "tag_id": ev.tag_id,
                    "timestamp": ev.timestamp,
                    "antenna": ev.antenna,
                    "rssi": ev.rssi,
                    "registered": is_registered,
                    "bib": rider.bib if rider else None,
                    "name": rider.name if rider else None,
                })

            if is_registered:
                # RECHECK-2026-07-25 #1: broadcast only when the pass actually
                # changed something. With the reader-side cooldown gone (M6),
                # a tag fluttering in the read zone (riders STAGING next to
                # the line) delivers arrive events continuously; each used to
                # trigger a misleading "lap" event + a full standings
                # re-render several times a second. We still broadcast when a
                # NEW participant row appears (pre-start check-in behaviour).
                was_present = ev.tag_id in race.participants
                prev_laps = race.participants[ev.tag_id].laps if was_present else -1
                p = race.add_lap(ev.tag_id, ev.timestamp)
                if not was_present or p.laps != prev_laps:
                    lap_payload = {
                        "type": "lap",
                        "tag_id": p.tag_id,
                        "laps": p.laps,
                        "finished": p.finished,
                        "last_pass_time": p.last_pass_time,
                    }
                    _publish(lap_payload)
                    # Broadcast updated standings snapshot (enriched with rider info)
                    table = _build_standings_items()
                    standings_payload = {
                        "type": "standings", "items": table, **_race_live_status()
                    }
                    _publish(standings_payload)
            else:
                # W-011: fire unknown_tag SSE + add to ring buffer so the
                # operator can register the tag via the "Couple tag → rider"
                # modal. NO Participant row is created — that only happens
                # once the rider is registered and the tag is read again.
                unknown_payload: Dict[str, Any] = {
                    "type": "unknown_tag",
                    "tag_id": ev.tag_id,
                    "timestamp": ev.timestamp,
                    "antenna": ev.antenna,
                    "rssi": ev.rssi,
                }
                _publish(unknown_payload)
                ring_entry = {
                    "tag_id": ev.tag_id,
                    "timestamp": ev.timestamp,
                    "antenna": ev.antenna,
                    "rssi": ev.rssi,
                }
                with _unknown_tags_lock:
                    recent_unknown_tags.append(ring_entry)

    return {"events_processed": accepted}


# ---------------------------------------------------------------------------
# Classification / race
# ---------------------------------------------------------------------------

def _build_standings_items() -> List[Dict[str, Any]]:
    """Build standings list enriched with rider bib/name from rider_store."""
    result = []
    for p in race.standings():
        d = p.model_dump()
        rider = rider_store.get(p.tag_id)
        d["bib"] = rider.bib if rider else None
        d["name"] = rider.name if rider else None
        result.append(d)
    return result


def _race_live_status() -> Dict[str, Any]:
    """Live finishing/bell state attached to standings broadcasts so the UI
    can update the 'laps to go' banner on every lap without a second fetch
    (F8)."""
    return {"finishing": race.finishing, "laps_to_go": race.laps_to_go()}


@app.get("/classification", response_model=ClassificationDTO)
def get_classification():
    items = _build_standings_items()
    return {"count": len(items), "standings": items}


def _slugify(s: str) -> str:
    """Filesystem-safe slug for the export filename. Keeps letters/numbers/dash."""
    import re
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s or "race"


def _build_classification_csv() -> tuple[str, str]:
    """Build the CSV body for the active race and a filename hint.

    Returns (csv_text, filename). The body starts with a UTF-8 BOM so Excel
    auto-detects the encoding, plus a few '#'-prefixed metadata lines
    (Excel treats them as text in column A), then the header + rows.
    """
    import csv
    import io
    from datetime import datetime, timezone

    race_row = storage.get_race(race.race_id) if race.race_id else None
    race_name = race_row.name if race_row else "Race"
    scheduled_iso = _iso_or_none(race_row.scheduled_at) if race_row else None
    started_iso = _iso_or_none(race.started_at)
    ended_iso = _iso_or_none(race.ended_at)
    exported_iso = datetime.now(timezone.utc).isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")

    items = _build_standings_items()

    buf = io.StringIO()
    buf.write("﻿")  # UTF-8 BOM
    buf.write(f"# Race: {race_name}\n")
    if scheduled_iso:
        buf.write(f"# Scheduled: {scheduled_iso}\n")
    if started_iso:
        buf.write(f"# Started: {started_iso}\n")
    if ended_iso:
        buf.write(f"# Ended: {ended_iso}\n")
    buf.write(f"# Exported: {exported_iso}\n")
    buf.write(f"# Total laps: {race.total_laps}\n")

    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(
        ["position", "bib", "name", "tag_id", "laps", "laps_behind", "status",
         "finished", "finish_time", "total_time_ms", "net_time_ms",
         "last_pass_time"]
    )
    for idx, item in enumerate(items, start=1):
        # F3: a non-classified rider (DNF/DNS/DSQ) has no finishing position —
        # the status column carries the result and the position cell is blank.
        status = (item.get("status") or "").upper()
        writer.writerow([
            "" if status else idx,
            item.get("bib") or "",
            item.get("name") or "",
            item.get("tag_id") or "",
            item.get("laps", 0),
            item.get("laps_behind") if item.get("laps_behind") is not None else "",
            status,
            "true" if item.get("finished") else "false",
            item.get("finish_time") or "",
            item.get("total_time_ms") if item.get("total_time_ms") is not None else "",
            item.get("net_time_ms") if item.get("net_time_ms") is not None else "",
            item.get("last_pass_time") or "",
        ])

    # Filename: racetag-<slug>-<YYYY-MM-DD>.csv (use scheduled date if set,
    # else export date)
    date_for_name = (
        race_row.scheduled_at.strftime("%Y-%m-%d") if (race_row and race_row.scheduled_at)
        else datetime.now(timezone.utc).strftime("%Y-%m-%d")
    )
    filename = f"racetag-{_slugify(race_name)}-{date_for_name}.csv"
    return buf.getvalue(), filename


@app.get("/classification.csv", responses={200: {"content": {"text/csv": {}}}})
def get_classification_csv():
    """CSV export of the active race standings. UTF-8 with BOM for Excel."""
    from fastapi.responses import Response
    body, filename = _build_classification_csv()
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/races/{race_id}/classification.csv",
         responses={200: {"content": {"text/csv": {}}}})
def get_classification_csv_for_race(race_id: str):
    """CSV export per race. For now only supported on the **active** race
    (matches the in-memory RaceState). For non-active races, returns 409 —
    the operator should activate the race first."""
    if storage.get_race(race_id) is None:
        raise HTTPException(status_code=404, detail="race not found")
    if race.race_id != race_id:
        raise HTTPException(
            status_code=409,
            detail="race is not active; activate it first to export results",
        )
    return get_classification_csv()


@app.get("/tags.csv", responses={200: {"content": {"text/csv": {}}}})
def get_tags_csv():
    """Tag-inventory export: every distinct tag read in the ACTIVE race, in
    first-read (= wave) order, as an import-ready start-list CSV.

    Workflow: create a scratch race, wave the physical tag pool past the
    antenna, download — a ready-to-fill ``tag_id;bib;name`` template. The
    trailing info columns are ignored by the rider import (it only reads the
    first three); low ``reads`` values flag tags that read poorly. No
    '#'-metadata lines on purpose — unlike classification.csv this file must
    round-trip through the importer, which skips exactly one header row.
    Semicolon + UTF-8 BOM so German Excel opens it in columns.
    """
    import csv
    import io
    from datetime import datetime, timezone

    from fastapi.responses import Response

    summary = storage.tag_read_summary()
    race_row = storage.get_race(race.race_id) if race.race_id else None
    race_name = race_row.name if race_row else "Race"

    buf = io.StringIO()
    buf.write("﻿")
    writer = csv.writer(buf, delimiter=";", lineterminator="\n")
    writer.writerow(
        ["tag_id", "bib", "name",
         "reads (ignored on import)", "first read (ignored on import)"]
    )
    for row in summary:
        # W-075: after a coupling session this export IS the master start
        # list — prefill bib/name for tags that are already registered.
        rider = rider_store.get(row["tag_id"])
        writer.writerow([
            row["tag_id"],
            rider.bib if rider else "",
            rider.name if rider else "",
            row["reads"],
            row["first_seen"],
        ])

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    filename = f"racetag-tags-{_slugify(race_name)}-{date_str}.csv"
    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _iso_or_none(dt) -> Optional[str]:
    if dt is None:
        return None
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


@app.get("/race", response_model=RaceDTO)
def get_race():
    """Return the active race, enriched with id/name/scheduled_at from the
    persisted Race row plus the in-memory standings."""
    race_row = storage.get_race(race.race_id) if race.race_id else None
    return {
        "id": race_row.id if race_row else None,
        "name": race_row.name if race_row else None,
        "scheduled_at": _iso_or_none(race_row.scheduled_at) if race_row else None,
        "total_laps": race.total_laps,
        "start_time": race.start_time.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "started": race.started,
        "started_at": _iso_or_none(race.started_at),
        "ended": race.ended,
        "ended_at": _iso_or_none(race.ended_at),
        "participants": _build_standings_items(),
        "finish_mode": race.finish_mode,
        "duration_s": race.duration_s,
        "final_laps": race.final_laps,
        "finishing": race.finishing,
        "laps_to_go": race.laps_to_go(),
    }


@app.post("/race/start", status_code=200)
def post_race_start():
    """Mark the active race as started. Idempotent: returns the existing
    started_at if already started. Persists to both the legacy meta key
    (backward compat) and the active Race row. Broadcasts race_started SSE."""
    from datetime import datetime, timezone
    started_at = race.start(now=datetime.now(timezone.utc))
    started_at_iso = (
        started_at.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    )
    storage.set_meta("race_started_at", started_at_iso)
    if race.race_id:
        storage.update_race(race.race_id, started=True, started_at=started_at)
    _publish({"type": "race_started", "started_at": started_at_iso})
    return {"started": True, "started_at": started_at_iso}


# ---------------------------------------------------------------------------
# W-036: Race reset + total-laps control
# ---------------------------------------------------------------------------

class PatchRaceBody(BaseModel):
    total_laps: int = Field(..., ge=1, le=999)


@app.post("/race/reset", status_code=204)
def post_race_reset():
    """Clear all active-race participants + persisted events. Preserves riders.
    Resets started/ended state on both the in-memory race AND the Race row.
    Broadcasts race_reset SSE."""
    race.participants.clear()
    race.started = False
    race.started_at = None
    race.ended = False
    race.ended_at = None
    # F1/F5 runtime state is derived from the (now cleared) event log.
    race.finishing = False
    race.finishing_at = None
    race.time_target_laps = None
    race.pass_times.clear()
    storage.set_meta("race_started_at", "")
    storage.set_meta("race_ended_at", "")
    if race.race_id:
        storage.update_race(
            race.race_id,
            started=False, started_at=None,
            ended=False, ended_at=None,
        )
    storage.clear_events()
    events.clear()
    with _unknown_tags_lock:
        recent_unknown_tags.clear()
    with _tag_seen_lock:
        _tag_seen_last.clear()
    _publish({"type": "race_reset"})


class PatchRaceFullBody(BaseModel):
    """Body of PATCH /race — any subset of fields may be set."""
    total_laps: Optional[int] = Field(default=None, ge=1, le=999)
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    scheduled_at: Optional[str] = None


@app.patch("/race", status_code=200)
def patch_race(body: PatchRaceFullBody):
    """Update active-race metadata (total_laps / name / scheduled_at).
    Persists to both the legacy meta key (total_laps only, for backward compat)
    and the active Race row. Broadcasts race_updated SSE."""
    from domain.race import parse_iso as _parse_iso

    fields_to_update = {}
    if body.total_laps is not None:
        race.total_laps = body.total_laps
        storage.set_meta("total_laps", str(body.total_laps))
        fields_to_update["total_laps"] = body.total_laps
    if body.name is not None:
        fields_to_update["name"] = body.name
    if body.scheduled_at is not None:
        # Empty string == clear it.
        if body.scheduled_at.strip():
            try:
                fields_to_update["scheduled_at"] = _parse_iso(body.scheduled_at)
            except (ValueError, TypeError):
                raise HTTPException(status_code=400, detail="scheduled_at must be ISO 8601")
        else:
            fields_to_update["scheduled_at"] = None

    if race.race_id and fields_to_update:
        storage.update_race(race.race_id, **fields_to_update)

    payload = {"type": "race_updated"}
    if body.total_laps is not None:
        payload["total_laps"] = body.total_laps
    _publish(payload)

    # Return the active race id + the updated fields for the caller.
    race_row = storage.get_race(race.race_id) if race.race_id else None
    return {
        "id": race_row.id if race_row else None,
        "name": race_row.name if race_row else None,
        "scheduled_at": _iso_or_none(race_row.scheduled_at) if race_row else None,
        "total_laps": race.total_laps,
    }


@app.post("/race/end", status_code=200)
def post_race_end():
    """Freeze the active race: ended/ended_at set, add_lap becomes a no-op.
    Events still flow through to storage (record stays complete). Idempotent.
    Broadcasts race_ended SSE."""
    from datetime import datetime, timezone
    ended_at = race.end(now=datetime.now(timezone.utc))
    ended_at_iso = ended_at.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    storage.set_meta("race_ended_at", ended_at_iso)
    if race.race_id:
        storage.update_race(race.race_id, ended=True, ended_at=ended_at)
    _publish({"type": "race_ended", "ended_at": ended_at_iso})
    return {"ended": True, "ended_at": ended_at_iso}


@app.post("/race/reopen", status_code=200)
def post_race_reopen():
    """Undo an accidental "End race" (RECHECK-2026-07-25 #4).

    Before this endpoint, one stray End click was irreversible without DB
    surgery: add_lap no-ops post-end and the replay cutoff keeps it that way
    across restarts. The events ARE all persisted though — so clearing the
    ended state and rebuilding recovers everything losslessly, including the
    passes that arrived while the race was wrongly ended.

    409 when the race isn't ended. In leader mode a legitimately-finished race
    re-derives its finishing state from the replay, so reopening after a real
    finish keeps the flags — this is a hatch for ACCIDENTAL ends, not an
    un-finish.
    """
    if not race.ended:
        raise HTTPException(status_code=409, detail="race is not ended")
    storage.set_meta("race_ended_at", "")
    if race.race_id:
        storage.update_race(race.race_id, ended=False, ended_at=None)
    # Rebuild replays ALL events — the ended cutoff is gone now, so passes
    # recorded during the accidental ended window count again.
    _rebuild_active_race_state_in_place()
    _publish({"type": "race_reopened"})
    _publish({"type": "standings", "items": _build_standings_items(), **_race_live_status()})
    return {"ended": False}


# ---------------------------------------------------------------------------
# Multi-race CRUD (2026-05-25)
# ---------------------------------------------------------------------------

def _race_row_to_summary(row, *, active_id: Optional[str]) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "scheduled_at": _iso_or_none(row.scheduled_at),
        "total_laps": row.total_laps,
        "started": row.started,
        "started_at": _iso_or_none(row.started_at),
        "ended": row.ended,
        "ended_at": _iso_or_none(row.ended_at),
        "created_at": _iso_or_none(row.created_at),
        "is_active": row.id == active_id,
        "snapshot_interval_s": row.snapshot_interval_s,
        "finish_mode": row.finish_mode,
        "duration_s": row.duration_s,
        "final_laps": row.final_laps,
    }


def _switch_active_race(new_race_id: str) -> None:
    """Persist new active id, rebuild race + rider_store + replay events.

    Single-threaded assumption: the operator UI sends one request at a time;
    not safe for concurrent /races/{id}/activate calls (good enough for now)."""
    global race, rider_store
    if storage.get_race(new_race_id) is None:
        raise HTTPException(status_code=404, detail=f"race {new_race_id} not found")
    storage.set_active_race_id(new_race_id)
    # Clear legacy meta keys so they don't bleed into the new race.
    storage.set_meta("race_started_at", "")
    storage.set_meta("race_ended_at", "")
    storage.set_meta("total_laps", "")
    race, rider_store = _load_active_race_state()
    events.clear()
    ended_cutoff = _ended_cutoff_iso_for_active_race()
    for ev in storage.iter_events():
        _replay_event(ev, ended_cutoff_iso=ended_cutoff)
    # BUG-004 fix: apply ended state only after replay (see helper docstring).
    _apply_ended_state_after_replay(race)
    with _unknown_tags_lock:
        recent_unknown_tags.clear()
    # W-075: throttle state is per-race context (registered/bib payloads change
    # with the rider registry) — a parked tag must re-announce promptly.
    with _tag_seen_lock:
        _tag_seen_last.clear()


@app.get("/races", response_model=RaceListDTO)
def get_races():
    active_id = storage.get_active_race_id()
    rows = storage.list_races()
    return {
        "count": len(rows),
        "items": [_race_row_to_summary(r, active_id=active_id) for r in rows],
        "active_race_id": active_id,
    }


@app.post("/races", response_model=RaceSummaryDTO, status_code=201)
def post_race(body: RaceCreateDTO):
    from domain.race import parse_iso as _parse_iso
    from domain.races import Race

    sched = None
    if body.scheduled_at:
        try:
            sched = _parse_iso(body.scheduled_at)
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="scheduled_at must be ISO 8601")

    # A time-based race needs BOTH duration_s and final_laps (final_laps may
    # be 0 for "on the timer, no extra laps"). Reject a half-specified format.
    if (body.duration_s is None) != (body.final_laps is None):
        raise HTTPException(
            status_code=422,
            detail="duration_s and final_laps must be set together (time-based race)",
        )

    new_race = Race(
        name=body.name,
        scheduled_at=sched,
        total_laps=body.total_laps,
        snapshot_interval_s=body.snapshot_interval_s,
        finish_mode=body.finish_mode,
        duration_s=body.duration_s,
        final_laps=body.final_laps,
    )
    storage.create_race(new_race)
    active_id = storage.get_active_race_id()
    return _race_row_to_summary(new_race, active_id=active_id)


@app.get("/races/{race_id}", response_model=RaceSummaryDTO)
def get_one_race(race_id: str):
    row = storage.get_race(race_id)
    if row is None:
        raise HTTPException(status_code=404, detail="race not found")
    active_id = storage.get_active_race_id()
    return _race_row_to_summary(row, active_id=active_id)


@app.patch("/races/{race_id}", response_model=RaceSummaryDTO)
def patch_race_by_id(race_id: str, body: RaceUpdateDTO):
    from domain.race import parse_iso as _parse_iso

    if storage.get_race(race_id) is None:
        raise HTTPException(status_code=404, detail="race not found")

    fields_to_update = {}
    if body.name is not None:
        fields_to_update["name"] = body.name
    if body.total_laps is not None:
        fields_to_update["total_laps"] = body.total_laps
    if body.scheduled_at is not None:
        if body.scheduled_at.strip():
            try:
                fields_to_update["scheduled_at"] = _parse_iso(body.scheduled_at)
            except (ValueError, TypeError):
                raise HTTPException(status_code=400, detail="scheduled_at must be ISO 8601")
        else:
            fields_to_update["scheduled_at"] = None
    if body.snapshot_interval_s is not None:
        # 0 is a valid "disabled" value distinct from null (the field is set
        # explicitly to 0 by the operator); store it as-is.
        fields_to_update["snapshot_interval_s"] = body.snapshot_interval_s
    if body.finish_mode is not None:
        fields_to_update["finish_mode"] = body.finish_mode
    if body.duration_s is not None:
        # 0 disables the time format; treat it as clearing duration_s.
        fields_to_update["duration_s"] = body.duration_s or None
    if body.final_laps is not None:
        fields_to_update["final_laps"] = body.final_laps

    if fields_to_update:
        storage.update_race(race_id, **fields_to_update)

    # Mirror format changes onto the live in-memory race if it's the active one
    # so they take effect without a restart. Changing race format mid-race is
    # unusual, but total_laps/finish_mode are UI-exposed pre-start controls.
    if race.race_id == race_id:
        if "total_laps" in fields_to_update:
            race.total_laps = fields_to_update["total_laps"]
            storage.set_meta("total_laps", str(fields_to_update["total_laps"]))
        if "finish_mode" in fields_to_update:
            race.finish_mode = fields_to_update["finish_mode"]
        if "duration_s" in fields_to_update:
            race.duration_s = fields_to_update["duration_s"]
        if "final_laps" in fields_to_update:
            race.final_laps = fields_to_update["final_laps"]

    active_id = storage.get_active_race_id()
    return _race_row_to_summary(storage.get_race(race_id), active_id=active_id)


@app.delete("/races/{race_id}", status_code=204)
def delete_race_by_id(race_id: str):
    if storage.get_race(race_id) is None:
        raise HTTPException(status_code=404, detail="race not found")
    if storage.get_active_race_id() == race_id:
        raise HTTPException(
            status_code=409,
            detail="cannot delete the active race — switch to another race first",
        )
    storage.delete_race(race_id)


@app.post("/races/{race_id}/activate", response_model=RaceSummaryDTO)
def post_race_activate(race_id: str):
    """Make race_id the active race: persist, rebuild in-memory state, replay events.
    Broadcasts active_race_changed SSE so clients can refresh."""
    _switch_active_race(race_id)
    _publish({"type": "active_race_changed", "race_id": race_id})
    active_id = storage.get_active_race_id()
    return _race_row_to_summary(storage.get_race(race_id), active_id=active_id)


# ---------------------------------------------------------------------------
# Auto-snapshot management — list snapshots for a race, force one immediately.
# Operator UI uses these to confirm "yes, snapshots ARE being written" and to
# get a recovery point without waiting for the next interval tick.
# ---------------------------------------------------------------------------

@app.get("/races/{race_id}/snapshots")
def get_race_snapshots(race_id: str):
    """List on-disk snapshots for *race_id*.

    Returns ``{count, items: [{stem, csv, db}]}`` sorted oldest-first.
    """
    from snapshots import list_snapshots
    if storage.get_race(race_id) is None:
        raise HTTPException(status_code=404, detail="race not found")
    raw = list_snapshots(_data_dir, race_id)
    items = [
        {
            "stem": s["stem"],
            "csv": s["csv_path"].name,
            "db": s["db_path"].name,
        }
        for s in raw if s["csv_exists"] and s["db_exists"]
    ]
    return {"count": len(items), "items": items}


@app.post("/races/{race_id}/snapshots", status_code=201)
def post_race_snapshot_now(race_id: str):
    """Force a snapshot for *race_id* right now, bypassing the interval timer.

    Only works for the active race (we'd need a per-race classification
    builder for non-active races; not built yet — operator can switch the
    active race if they want a snapshot of an inactive one).
    """
    from snapshots import write_snapshot, record_manual_snapshot
    from datetime import datetime, timezone
    if storage.get_race(race_id) is None:
        raise HTTPException(status_code=404, detail="race not found")
    if race.race_id != race_id:
        raise HTTPException(
            status_code=400,
            detail="manual snapshot only supported for the active race",
        )
    csv_text, _filename = _build_classification_csv()
    now = datetime.now(timezone.utc)
    csv_path, db_path = write_snapshot(
        _data_dir, race_id, csv_text, storage.backup_to, now=now,
    )
    # Tell the periodic snapshotter we just wrote one, so the next tick
    # doesn't immediately write a redundant snapshot.
    record_manual_snapshot(_snapshotter, race_id, now)
    return {"csv": csv_path.name, "db": db_path.name}


# ---------------------------------------------------------------------------
# W-074: Config endpoints — GET/PATCH /config
# ---------------------------------------------------------------------------

class PatchConfigBody(BaseModel):
    """Partial config update — all fields optional.

    An absent field is left untouched. ``reader_ip: null`` explicitly clears
    the persisted reader IP; null for the other fields is ignored.
    ``assistant_done`` must be a real JSON boolean (strict: ``"true"`` or
    ``1`` are rejected with 422).
    """

    reader_ip: str | None = None
    min_lap_interval_s: float | None = None
    total_laps: int | None = None
    antenna_power: int | None = None
    assistant_done: StrictBool | None = None


def _reader_controller() -> Any:
    """The desktop shell's ReaderSupervisor, or None (Docker, tests)."""
    return getattr(app.state, "reader_controller", None)


def _effective_config() -> Config:
    """Build the effective Config by merging persisted values over env defaults."""
    return Config(
        reader_ip=config_store.get_reader_ip() or _READER_IP_ENV,
        min_lap_interval_s=(
            config_store.get_min_lap_interval_s()
            if config_store.get_min_lap_interval_s() is not None
            else _MIN_LAP_INTERVAL_S_ENV
        ),
        total_laps=(
            config_store.get_total_laps()
            if config_store.get_total_laps() is not None
            else _RACE_TOTAL_LAPS
        ),
        antenna_power=config_store.get_antenna_power(),
        assistant_done=config_store.get_assistant_done(),
        desktop=_reader_controller() is not None,
        # Read per call: the desktop shell sets it before loading the backend,
        # but nothing else depends on import order.
        version=os.getenv("RACETAG_VERSION") or None,
    )


@app.get("/config", response_model=Config)
def get_config():
    """Return the effective config (persisted values merged over env defaults)."""
    return _effective_config()


@app.patch("/config", response_model=Config)
def patch_config(body: PatchConfigBody):
    """Partially update config. Validates ranges; persists via meta table.

    min_lap_interval_s and total_laps apply to the live race immediately and
    broadcast race_updated SSE. reader_ip and antenna_power are persisted
    only: the reader-service receives them in the reply to its next
    POST /reader/status (within ~2 s) and reconnects by itself, so no restart
    is triggered from here.
    """
    errors = []

    if body.reader_ip is not None:
        if not is_valid_ipv4(body.reader_ip):
            errors.append("reader_ip must be a valid IPv4 address")

    if body.min_lap_interval_s is not None:
        if not (0.0 <= body.min_lap_interval_s <= 60.0):
            errors.append("min_lap_interval_s must be between 0.0 and 60.0")

    if body.total_laps is not None:
        if not (1 <= body.total_laps <= 999):
            errors.append("total_laps must be between 1 and 999")

    if body.antenna_power is not None:
        # 0.1 dBm units; the Sirit's practical range is 10.0–30.0 dBm.
        if not (100 <= body.antenna_power <= 300):
            errors.append("antenna_power must be between 100 and 300 (0.1 dBm units)")

    if errors:
        raise HTTPException(status_code=422, detail=errors)

    if body.reader_ip is not None:
        config_store.set_reader_ip(body.reader_ip)
    elif "reader_ip" in body.model_fields_set:
        config_store.clear_reader_ip()

    if body.antenna_power is not None:
        config_store.set_antenna_power(body.antenna_power)

    if body.assistant_done is not None:
        config_store.set_assistant_done(body.assistant_done)

    if body.min_lap_interval_s is not None:
        config_store.set_min_lap_interval_s(body.min_lap_interval_s)
        # Apply to the live race immediately (AUDIT-2026-07 H5): the cooldown
        # is now a single source of truth, so a change takes effect on the
        # next pass without a restart.
        race.min_pass_interval_s = body.min_lap_interval_s
        _publish({"type": "race_updated", "min_lap_interval_s": body.min_lap_interval_s})

    if body.total_laps is not None:
        config_store.set_total_laps(body.total_laps)
        # Update in-memory race state immediately
        race.total_laps = body.total_laps
        # Also keep legacy meta key in sync (W-036 reads "total_laps" on startup)
        storage.set_meta("total_laps", str(body.total_laps))
        _publish({"type": "race_updated", "total_laps": body.total_laps})

    return _effective_config()


# ---------------------------------------------------------------------------
# Reader status protocol (PLAN-WINDOWS-NONTECHIE contract §2)
#
# The reader-service heartbeats POST /reader/status; the reply carries the
# current reader config and at most one queued command. State, SSE publishing,
# staleness and the discovery rendezvous live in reader_status_hub.py.
# ---------------------------------------------------------------------------

# Module-level so tests can shrink them; read at call / thread-start time.
_READER_DISCOVER_TIMEOUT_S = 15.0
_READER_STALE_CHECK_INTERVAL_S = 1.0


def _reader_supervisor_status() -> Optional[Dict[str, Any]]:
    """The controller's status() dict, or None without a (working) controller."""
    controller = _reader_controller()
    if controller is None:
        return None
    try:
        return dict(controller.status())
    except Exception:
        logger.warning("reader_controller.status() failed", exc_info=True)
        return None


_reader_hub = ReaderStatusHub(
    publish=_publish,
    # Late-bound so tests can monkeypatch the module-level _monotonic.
    clock=lambda: _monotonic(),
    now_iso=_now_iso,
    supervisor_status=_reader_supervisor_status,
)


def _check_reader_stale() -> bool:
    """Flip the reader state to unknown after 6 s without a heartbeat.

    Runs every second on the staleness thread; tests call it directly.
    """
    return _reader_hub.check_stale()


_reader_stale_stop: Optional[threading.Event] = None
_reader_stale_thread: Optional[threading.Thread] = None


def _reader_stale_loop(stop: threading.Event, interval_s: float) -> None:
    while not stop.wait(interval_s):
        try:
            _check_reader_stale()
        except Exception:
            logger.exception("reader staleness check failed")


@app.on_event("startup")
def _start_reader_stale_watch() -> None:
    """Start the staleness thread (per startup, like the snapshotter)."""
    global _reader_stale_stop, _reader_stale_thread
    _reader_stale_stop = threading.Event()
    _reader_stale_thread = threading.Thread(
        target=_reader_stale_loop,
        args=(_reader_stale_stop, _READER_STALE_CHECK_INTERVAL_S),
        name="reader-status-stale",
        daemon=True,
    )
    _reader_stale_thread.start()


@app.on_event("shutdown")
def _stop_reader_stale_watch() -> None:
    global _reader_stale_stop, _reader_stale_thread
    if _reader_stale_stop is not None:
        _reader_stale_stop.set()
    if _reader_stale_thread is not None:
        _reader_stale_thread.join(timeout=2.0)
    _reader_stale_stop = None
    _reader_stale_thread = None


# The discovery last taken over into reader_ip, as (reader-service pid, ip).
# The reader-service repeats discovered_ip in every heartbeat until its
# connection thread has handled the echo, which can take several seconds while
# it sits in a TCP connect. Persisting each discovery only once keeps such a
# repeat from overwriting a reader_ip the operator saved in the meantime.
_discovery_persist_lock = threading.Lock()
_last_persisted_discovery: Optional[tuple] = None


def _persist_discovered_ip(discovered_ip: Optional[str], pid: Optional[int]) -> None:
    """Contract §2.2 rule 1, applied once per discovery.

    A heartbeat without ``discovered_ip`` ends the current discovery, so a
    later rediscovery of the same address is persisted again; so is the same
    address reported by a different reader-service process.
    """
    global _last_persisted_discovery
    with _discovery_persist_lock:
        if discovered_ip is None:
            _last_persisted_discovery = None
            return
        key = (pid, discovered_ip)
        if key == _last_persisted_discovery:
            return
        _last_persisted_discovery = key
        old_ip = config_store.get_reader_ip()
        if discovered_ip != old_ip:
            # Persist before building the reply so the reply already echoes
            # the new IP and the reader-service can clear discovered_ip.
            config_store.set_reader_ip(discovered_ip)
            logger.info("reader_ip changed via discovery: %s -> %s", old_ip, discovered_ip)


@app.post("/reader/status")
def post_reader_status(body: ReaderStatusIn):
    """Heartbeat from the reader-service (every ~2 s and on state change).

    Only an unknown or missing ``state`` is rejected (422); every other
    malformed field is sanitised to null/empty. Reply:
    ``{"config": {"reader_ip", "antenna_power"}, "command": null | {"id", "type"}}``.
    """
    _persist_discovered_ip(body.discovered_ip, body.pid)

    command = _reader_hub.ingest(body)
    cfg = _effective_config()
    return {
        "config": {"reader_ip": cfg.reader_ip, "antenna_power": cfg.antenna_power},
        "command": command,
    }


@app.get("/reader/status")
def get_reader_status():
    """Last reader status; ``state`` is ``unknown`` before the first heartbeat
    and after 6 s without one. ``supervisor`` is the desktop controller's
    status, or null outside the desktop build."""
    return _reader_hub.snapshot()


@app.post("/reader/discover")
def post_reader_discover():
    """Ask the reader-service to search the network and wait for the result.

    Sync on purpose: FastAPI runs it in a worker thread, so blocking for up to
    15 s here never stalls the event loop (SSE keeps flowing).
    """
    return _reader_hub.request_discovery(_READER_DISCOVER_TIMEOUT_S)


@app.post("/reader/restart", status_code=202)
def post_reader_restart():
    """Reconnect the reader: restart the reader-service process in the
    desktop build, otherwise ask the running reader-service to reconnect."""
    controller = _reader_controller()
    if controller is not None:
        try:
            controller.restart()
            return {"accepted": True, "via": "supervisor"}
        except Exception:
            logger.exception("reader_controller.restart() failed; queueing reconnect command")
    _reader_hub.queue_reconnect()
    return {"accepted": True, "via": "command"}


# ---------------------------------------------------------------------------
# W-051: Antenna diagnostics
# ---------------------------------------------------------------------------

@app.get("/diagnostics/antennas")
def get_diagnostics_antennas(window_s: int = Query(default=60, ge=5, le=3600)):
    """Return per-antenna read counts for the last window_s seconds.

    Query: SELECT antenna, COUNT(*) FROM tag_events WHERE timestamp >= ? AND
           antenna IS NOT NULL GROUP BY antenna.
    """
    counts = storage.count_events_by_antenna(window_s)
    return {"window_s": window_s, "counts": counts}


# ---------------------------------------------------------------------------
# W-032: async SSE stream
# ---------------------------------------------------------------------------

@app.get("/stream")
async def stream_events():
    """Server-Sent Events stream.

    Each subscriber gets its own asyncio.Queue bound to this handler's event
    loop; _publish enqueues via that loop's call_soon_threadsafe from whatever
    thread it runs on. A 15-second timeout on queue.get() yields a keepalive
    comment so proxies do not drop the connection.
    """
    sub = _add_sse_subscriber()

    async def event_stream():
        try:
            while True:
                try:
                    item = await asyncio.wait_for(sub.queue.get(), timeout=15.0)
                    data = json.dumps(item, separators=(",", ":"))
                    yield f"data: {data}\n\n"
                except asyncio.TimeoutError:
                    yield f": keepalive {_now_iso()}\n\n"
        finally:
            _remove_sse_subscriber(sub)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Rider CRUD (W-010)
# ---------------------------------------------------------------------------

@app.post("/riders", response_model=RiderDTO, status_code=201)
def post_rider(body: RiderCreateDTO):
    """Register or update a rider for a given tag_id. Returns 201 (upsert semantics)."""
    existing = rider_store.get(body.tag_id)
    created_at = existing.created_at if existing else datetime.now(timezone.utc)
    rider = Rider(
        tag_id=body.tag_id,
        bib=body.bib,
        name=body.name,
        created_at=created_at,
        verein=body.verein,
        uci_id=body.uci_id,
    )
    rider = rider_store.upsert(rider)  # merged result (keep-semantics!)
    dto = _rider_to_dto(rider)
    if body.all_races:
        # Day model (Karli Krit): one tag + one number per PERSON. Propagate
        # bib/name to every race that has this tag registered — the active
        # race's row was just written above and matches the same values, so
        # the blanket UPDATE is idempotent for it. Other races pick the row
        # up from the DB when they get activated.
        dto.races_updated = storage.update_rider_bib_name_all_races(
            body.tag_id, body.bib, body.name,
            verein=body.verein, uci_id=body.uci_id,
        )
    return dto


@app.get("/riders/recent-reads", response_model=RecentReadsListDTO)
def get_recent_reads(limit: int = Query(default=10, ge=1, le=50)):
    """Return the most recent unknown-tag reads in reverse-chronological order (newest first).

    Query param `limit` is capped at 50 (the ring-buffer size).

    Tags that have been registered SINCE their read are filtered out
    (2026-07-25): during rapid sequential coupling, the ring buffer still
    holds the tag that was just registered — offering it again invites the
    operator to overwrite the previous rider via the upsert.
    """
    with _unknown_tags_lock:
        # deque is ordered oldest→newest; reverse for newest-first
        snapshot = list(recent_unknown_tags)
    snapshot.reverse()
    still_unknown = [e for e in snapshot if e["tag_id"] not in rider_store]
    sliced = still_unknown[:limit]
    items = [RecentReadDTO(**entry) for entry in sliced]
    return RecentReadsListDTO(count=len(items), items=items)


@app.get("/riders", response_model=RidersListDTO)
def get_riders():
    """List all registered riders."""
    all_riders = rider_store.list()
    return RidersListDTO(count=len(all_riders), items=[_rider_to_dto(r) for r in all_riders])


@app.get("/riders/{tag_id}", response_model=RiderDTO)
def get_rider(tag_id: str):
    """Return a single rider by tag_id. 404 if not registered."""
    rider = rider_store.get(tag_id)
    if rider is None:
        raise HTTPException(status_code=404, detail=f"No rider registered for tag '{tag_id}'")
    return _rider_to_dto(rider)


@app.delete("/riders/{tag_id}", status_code=204)
def delete_rider(tag_id: str):
    """Delete a rider by tag_id. 404 if not found."""
    removed = rider_store.delete(tag_id)
    if not removed:
        raise HTTPException(status_code=404, detail=f"No rider registered for tag '{tag_id}'")


@app.delete("/riders/{tag_id}/passes", status_code=200)
def delete_rider_passes(tag_id: str):
    """Reset a rider: delete ALL their passes in the active race (2026-07-25).

    For a botched measurement — e.g. a TT start read captured while the rider
    was still staging in the read zone — this wipes the rider's events in one
    action; their standings row disappears and their next roll across the
    line starts a fresh attempt. Deliberate full-delete (mirrors the manual
    -1 semantics, which already deletes single events permanently).

    404 unknown rider; 409 on an ended race (frozen results).
    """
    if rider_store.get(tag_id) is None:
        raise HTTPException(
            status_code=404,
            detail=f"No rider registered for tag '{tag_id}' in the active race",
        )
    if race.ended:
        raise HTTPException(
            status_code=409,
            detail="Race has ended — rider reset is not allowed",
        )

    deleted = storage.delete_events_for_tag(tag_id)
    _rebuild_active_race_state_in_place()

    _publish({"type": "standings", "items": _build_standings_items(), **_race_live_status()})
    return {"tag_id": tag_id, "deleted_events": deleted}


@app.patch("/riders/{tag_id}/status", response_model=RiderDTO)
def patch_rider_status(tag_id: str, body: RiderStatusDTO):
    """Set or clear a rider's result status (F3): "dnf", "dns", "dsq", or null.

    Persists to the RiderStore and mirrors into the live RaceState so
    standings re-sort immediately (non-classified riders drop below all
    finishers). Broadcasts a standings update.
    """
    from domain.race import _VALID_STATUSES  # noqa: PLC0415

    rider = rider_store.get(tag_id)
    if rider is None:
        raise HTTPException(
            status_code=404,
            detail=f"No rider registered for tag '{tag_id}' in the active race",
        )

    status = body.status
    if status is not None:
        status = status.lower()
        if status not in _VALID_STATUSES:
            raise HTTPException(
                status_code=422,
                detail=f"status must be one of dnf/dns/dsq or null, got {body.status!r}",
            )

    rider_store.set_status(tag_id, status)
    race.set_status(tag_id, status)

    _publish({"type": "standings", "items": _build_standings_items(), **_race_live_status()})
    return _rider_to_dto(rider_store.get(tag_id))


# ---------------------------------------------------------------------------
# Manual lap correction (operator can credit / revoke a lap when the reader
# miscounts). Synthetic events are tagged with reader_serial="MANUAL" so they
# stay distinguishable in the tag_events audit trail.
#
# Add (+1): inserts a synthetic arrive event for *tag_id* at the supplied
#   timestamp (defaults to server-now). Goes through the same add_lap pipeline
#   as a real reader pass — including cooldown gating (operator clicking +1
#   twice within min_pass_interval_s is correctly debounced).
# Remove (-1): deletes the most-recent tag_event row for *tag_id* and replays
#   the race state to recompute laps. Slower but correct: the most recent
#   event is not always the most recent COUNTED lap (cooldown could have
#   skipped it), and replaying is the same code path as restart so we know it
#   produces the correct standings.
# ---------------------------------------------------------------------------

_MANUAL_READER_SERIAL = "MANUAL"


@app.post(
    "/riders/{tag_id}/laps",
    response_model=ManualLapResultDTO,
    status_code=201,
)
def post_manual_lap(tag_id: str, body: ManualLapAddDTO):
    """Credit a manual lap for *tag_id* in the active race.

    Refuses:
    - 404 if the tag isn't registered as a rider in the active race.
    - 409 if the race hasn't been started yet, or has already ended.
    - 409 if add_lap would be debounced by the cooldown (operator double-click)
      — in that case the event is NOT persisted, so a future restart can't
      surface a phantom lap. Operator gets a clear "no change" signal.
    """
    if rider_store.get(tag_id) is None:
        raise HTTPException(
            status_code=404,
            detail=f"No rider registered for tag '{tag_id}' in the active race",
        )
    if not race.started:
        raise HTTPException(
            status_code=409,
            detail="Race has not started — start the race before crediting laps",
        )
    if race.ended:
        raise HTTPException(
            status_code=409,
            detail="Race has ended — manual lap changes are not allowed",
        )

    timestamp = body.timestamp or _now_iso()

    # Try the in-memory add_lap FIRST. If add_lap is a no-op (cooldown debounce,
    # or post-end if ended state was applied), we MUST NOT persist a synthetic
    # MANUAL event — otherwise a future replay would re-process it with the
    # race not yet in the ended state and count it as a real lap.
    prev_laps = race.participants.get(tag_id).laps if race.participants.get(tag_id) else 0
    p = race.add_lap(tag_id, timestamp)
    if p.laps == prev_laps:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Lap not credited (cooldown is {race.min_pass_interval_s:.1f}s "
                f"and the previous pass was too recent, or timestamp predates race start)"
            ),
        )

    ev = TagEventDTO(
        source="manual",
        reader_ip="0.0.0.0",
        reader_serial=_MANUAL_READER_SERIAL,
        timestamp=timestamp,
        event_type=EventType.arrive,
        tag_id=tag_id,
    )
    storage.append_event(ev)
    events.append(ev)

    lap_payload = {
        "type": "lap",
        "tag_id": p.tag_id,
        "laps": p.laps,
        "finished": p.finished,
        "last_pass_time": p.last_pass_time,
        "manual": True,
    }
    _publish(lap_payload)
    _publish({"type": "standings", "items": _build_standings_items()})

    return ManualLapResultDTO(
        tag_id=p.tag_id,
        laps=p.laps,
        last_pass_time=p.last_pass_time,
        finished=p.finished,
        finish_time=p.finish_time,
        total_time_ms=p.total_time_ms,
    )


@app.delete(
    "/riders/{tag_id}/laps",
    response_model=ManualLapResultDTO,
    status_code=200,
)
def delete_manual_lap(tag_id: str):
    """Revoke the most-recent lap for *tag_id* in the active race.

    Removes the most-recent ARRIVE tag_event for *tag_id* and replays the
    race state to recompute standings.

    Refuses:
    - 404 if the rider isn't registered for the active race.
    - 400 if there are no arrive events to remove.
    - 409 if the race has ended (post-end deletions would interact poorly
      with the replay-cutoff: an ended race is meant to be frozen).
    """
    if rider_store.get(tag_id) is None:
        raise HTTPException(
            status_code=404,
            detail=f"No rider registered for tag '{tag_id}' in the active race",
        )
    if race.ended:
        raise HTTPException(
            status_code=409,
            detail="Race has ended — manual lap changes are not allowed",
        )

    last_id = storage.find_last_event_id_for_tag(
        tag_id, event_type=EventType.arrive.value
    )
    if last_id is None:
        raise HTTPException(
            status_code=400,
            detail=f"No lap events to remove for tag '{tag_id}'",
        )

    storage.delete_event_by_id(last_id)
    _rebuild_active_race_state_in_place()

    p = race.participants.get(tag_id)
    laps = p.laps if p else 0
    last_pass_time = p.last_pass_time if p else None
    finished = p.finished if p else False
    finish_time = p.finish_time if p else None
    total_time_ms = p.total_time_ms if p else None

    lap_payload = {
        "type": "lap",
        "tag_id": tag_id,
        "laps": laps,
        "finished": finished,
        "last_pass_time": last_pass_time,
        "manual": True,
    }
    _publish(lap_payload)
    _publish({"type": "standings", "items": _build_standings_items()})

    return ManualLapResultDTO(
        tag_id=tag_id,
        laps=laps,
        last_pass_time=last_pass_time,
        finished=finished,
        finish_time=finish_time,
        total_time_ms=total_time_ms,
    )
