from __future__ import annotations

import asyncio
import collections
import threading
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any, Dict, List

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

# W-036: override env default with persisted total_laps if present in meta.
_persisted_total_laps = storage.get_meta("total_laps")
if _persisted_total_laps is not None:
    try:
        _RACE_TOTAL_LAPS = int(_persisted_total_laps)
    except ValueError:
        pass  # corrupted meta value — fall back to env default

# Global single race for MVP
race = RaceState(total_laps=_RACE_TOTAL_LAPS, min_pass_interval_s=_RACE_MIN_PASS_INTERVAL_S)

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

# Rider registry (W-010) — backed by persistent storage (W-050)
rider_store = RiderStore(storage=storage)

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


@app.get("/race", response_model=RaceDTO)
def get_race():
    return {
        "total_laps": race.total_laps,
        "start_time": race.start_time.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "participants": _build_standings_items(),
    }


# ---------------------------------------------------------------------------
# W-036: Race reset + total-laps control
# ---------------------------------------------------------------------------

class PatchRaceBody(BaseModel):
    total_laps: int = Field(..., ge=1, le=999)


@app.post("/race/reset", status_code=204)
def post_race_reset():
    """Clear all race participants + persisted events. Preserves riders.

    Broadcasts a {type: "race_reset"} SSE event. Returns 204 on success.
    """
    race.participants.clear()
    storage.clear_events()
    # Clear the in-memory events log too
    events.clear()
    # Clear recent unknown-tag ring buffer
    with _unknown_tags_lock:
        recent_unknown_tags.clear()
    _publish({"type": "race_reset"})


@app.patch("/race", status_code=200)
def patch_race(body: PatchRaceBody):
    """Update total_laps. Persists to meta table so the value survives restart.

    Broadcasts a {type: "race_updated", total_laps: <new>} SSE event.
    """
    race.total_laps = body.total_laps
    storage.set_meta("total_laps", str(body.total_laps))
    _publish({"type": "race_updated", "total_laps": body.total_laps})
    return {"total_laps": race.total_laps}


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
