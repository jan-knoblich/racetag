from __future__ import annotations

import asyncio
import collections
import threading
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Depends, HTTPException, Query, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field

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
    RidersListDTO,
    RecentReadDTO,
    RecentReadsListDTO,
)
from storage import Storage


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

    rs = RaceState(
        total_laps=total_laps,
        min_pass_interval_s=_RACE_MIN_PASS_INTERVAL_S,
        race_id=active_id,
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

    # ended state — only from Race row
    if race_row.ended and race_row.ended_at is not None:
        rs.end(now=race_row.ended_at)

    rstore = RiderStore(storage=storage, race_id=active_id)
    return rs, rstore


race, rider_store = _load_active_race_state()

# Debug/event store
events: List[TagEventDTO] = []

# ---------------------------------------------------------------------------
# W-032: SSE subscribers — each subscriber is an asyncio.Queue.
#
# The subscribers list is mutated from both the async /stream handler and the
# sync POST /events/tag/batch route.  We protect list mutation with a
# threading.Lock (safe across sync routes and asyncio tasks in the same
# process).  Publishing from the sync route uses
# loop.call_soon_threadsafe(queue.put_nowait, payload) so the queue stays
# fully async-compatible.
#
# Backward-compat shim: test_unknown_tag.py directly appends a plain list to
# `subscribers` and calls list.append() on it.  We keep that working by
# accepting both asyncio.Queue objects and plain list objects in the fan-out
# loop: if the element has a `put_nowait` method we treat it as a queue;
# otherwise we call `.append()` on it (legacy list-based subscriber).
# ---------------------------------------------------------------------------

subscribers: List[Any] = []   # elements are asyncio.Queue or legacy list
_subscribers_lock = threading.Lock()

# Rider registry (W-010) is built per-race by _load_active_race_state() above.

# W-011: ring buffer of recent unknown-tag reads (max 50, newest at right)
_UNKNOWN_TAG_CAP = 50
recent_unknown_tags: collections.deque = collections.deque(maxlen=_UNKNOWN_TAG_CAP)
_unknown_tags_lock = threading.Lock()


# ---------------------------------------------------------------------------
# W-050: Replay persisted events on startup to restore race state.
#
# We replay without re-persisting (replay_event skips storage.append_event).
# This is idempotent: events already in the DB are not duplicated.
# ---------------------------------------------------------------------------

def _replay_event(ev: TagEventDTO) -> None:
    """Apply a single event to in-memory state without writing to storage."""
    events.append(ev)
    if ev.event_type == EventType.arrive:
        race.add_lap(ev.tag_id, ev.timestamp)


for _ev in storage.iter_events():
    _replay_event(_ev)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _rider_to_dto(rider: Rider) -> RiderDTO:
    return RiderDTO(
        tag_id=rider.tag_id,
        bib=rider.bib,
        name=rider.name,
        created_at=rider.created_at,
    )


def _publish(payload: Dict[str, Any]) -> None:
    """Fan-out *payload* to all current subscribers.

    Safe to call from both sync and async contexts.  Handles both queue-based
    subscribers (asyncio.Queue) and legacy list-based subscribers (used by
    existing tests that directly append a plain list to `subscribers`).
    """
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = None

    with _subscribers_lock:
        current = list(subscribers)

    for sub in current:
        try:
            if hasattr(sub, "put_nowait"):
                # asyncio.Queue — schedule from whichever thread we're on
                if loop is not None and loop.is_running():
                    loop.call_soon_threadsafe(sub.put_nowait, payload)
                else:
                    sub.put_nowait(payload)
            else:
                # Legacy list-based subscriber (test shim)
                sub.append(payload)
        except Exception:
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
            # add_lap returns the unchanged participant if the event is suppressed
            # by the cooldown; we persist regardless so the event is on record.
            storage.append_event(ev)
            p = race.add_lap(ev.tag_id, ev.timestamp)
            # Broadcast lap update (always, laps keep advancing)
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
            standings_payload = {"type": "standings", "items": table}
            _publish(standings_payload)

            # W-011: if tag has no registered rider, fire unknown_tag SSE + add to ring buffer
            if ev.tag_id not in rider_store:
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
        ["position", "bib", "name", "tag_id", "laps", "finished",
         "finish_time", "total_time_ms", "last_pass_time"]
    )
    for idx, item in enumerate(items, start=1):
        writer.writerow([
            idx,
            item.get("bib") or "",
            item.get("name") or "",
            item.get("tag_id") or "",
            item.get("laps", 0),
            "true" if item.get("finished") else "false",
            item.get("finish_time") or "",
            item.get("total_time_ms") if item.get("total_time_ms") is not None else "",
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
    for ev in storage.iter_events():
        _replay_event(ev)
    with _unknown_tags_lock:
        recent_unknown_tags.clear()


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

    new_race = Race(name=body.name, scheduled_at=sched, total_laps=body.total_laps)
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

    if fields_to_update:
        storage.update_race(race_id, **fields_to_update)

    # If we just patched the active race, mirror total_laps onto the in-memory race.
    if race.race_id == race_id and "total_laps" in fields_to_update:
        race.total_laps = fields_to_update["total_laps"]
        storage.set_meta("total_laps", str(fields_to_update["total_laps"]))

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
# W-074: Config endpoints — GET/PATCH /config
# ---------------------------------------------------------------------------

class PatchConfigBody(BaseModel):
    """Partial config update — all fields optional."""

    reader_ip: str | None = None
    min_lap_interval_s: float | None = None
    total_laps: int | None = None


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
    )


@app.get("/config", response_model=Config)
def get_config():
    """Return the effective config (persisted values merged over env defaults)."""
    return _effective_config()


@app.patch("/config", response_model=Config)
def patch_config(body: PatchConfigBody):
    """Partially update config. Validates ranges; persists via meta table.

    On total_laps change: updates race.total_laps live and broadcasts
    race_updated SSE.  reader_ip and min_lap_interval_s are persisted only
    (their consumers are external processes managed by the desktop shell).
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

    if errors:
        raise HTTPException(status_code=422, detail=errors)

    if body.reader_ip is not None:
        config_store.set_reader_ip(body.reader_ip)

    if body.min_lap_interval_s is not None:
        config_store.set_min_lap_interval_s(body.min_lap_interval_s)

    if body.total_laps is not None:
        config_store.set_total_laps(body.total_laps)
        # Update in-memory race state immediately
        race.total_laps = body.total_laps
        # Also keep legacy meta key in sync (W-036 reads "total_laps" on startup)
        storage.set_meta("total_laps", str(body.total_laps))
        _publish({"type": "race_updated", "total_laps": body.total_laps})

    return _effective_config()


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

    Each subscriber gets its own asyncio.Queue.  The publisher (post_events_batch)
    enqueues payloads via loop.call_soon_threadsafe so the queue stays thread-safe.
    A 15-second timeout on queue.get() yields a keepalive comment so proxies do
    not drop the connection.
    """
    client_queue: asyncio.Queue = asyncio.Queue()
    with _subscribers_lock:
        subscribers.append(client_queue)

    async def event_stream():
        try:
            while True:
                try:
                    item = await asyncio.wait_for(client_queue.get(), timeout=15.0)
                    data = json.dumps(item, separators=(",", ":"))
                    yield f"data: {data}\n\n"
                except asyncio.TimeoutError:
                    yield f": keepalive {_now_iso()}\n\n"
        finally:
            with _subscribers_lock:
                try:
                    subscribers.remove(client_queue)
                except ValueError:
                    pass

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
    )
    rider_store.upsert(rider)
    return _rider_to_dto(rider)


@app.get("/riders/recent-reads", response_model=RecentReadsListDTO)
def get_recent_reads(limit: int = Query(default=10, ge=1, le=50)):
    """Return the most recent unknown-tag reads in reverse-chronological order (newest first).

    Query param `limit` is capped at 50 (the ring-buffer size).
    """
    with _unknown_tags_lock:
        # deque is ordered oldest→newest; reverse for newest-first
        snapshot = list(recent_unknown_tags)
    snapshot.reverse()
    sliced = snapshot[:limit]
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
