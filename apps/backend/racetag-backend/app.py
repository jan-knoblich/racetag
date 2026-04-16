from __future__ import annotations

import collections
import threading
from datetime import datetime, timezone
import json
import os
from typing import Any, Dict, List

from fastapi import FastAPI, Depends, HTTPException, Query, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.security import APIKeyHeader

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


# API Key using env RACETAG_API_KEY
API_KEY_HEADER_NAME = "X-API-Key"
_API_KEY = os.getenv("RACETAG_API_KEY")
_RACE_TOTAL_LAPS = int(os.getenv("RACE_TOTAL_LAPS", "5"))
_RACE_MIN_PASS_INTERVAL_S = float(os.getenv("RACE_MIN_PASS_INTERVAL_S", "8.0"))
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


# Global single race for MVP
race = RaceState(total_laps=_RACE_TOTAL_LAPS, min_pass_interval_s=_RACE_MIN_PASS_INTERVAL_S)

# Debug/event store
events: List[TagEventDTO] = []

# SSE subscribers: list of buffers
subscribers: List[List[Dict[str, Any]]] = []

# Rider registry (W-010)
rider_store = RiderStore()

# W-011: ring buffer of recent unknown-tag reads (max 50, newest at right)
_UNKNOWN_TAG_CAP = 50
recent_unknown_tags: collections.deque = collections.deque(maxlen=_UNKNOWN_TAG_CAP)
_unknown_tags_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _rider_to_dto(rider: Rider) -> RiderDTO:
    return RiderDTO(
        tag_id=rider.tag_id,
        bib=rider.bib,
        name=rider.name,
        created_at=rider.created_at,
    )


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
            # Use event timestamp as the pass time
            p = race.add_lap(ev.tag_id, ev.timestamp)
            # Broadcast lap update (always, laps keep advancing)
            lap_payload = {
                "type": "lap",
                "tag_id": p.tag_id,
                "laps": p.laps,
                "finished": p.finished,
                "last_pass_time": p.last_pass_time,
            }
            for q in list(subscribers):
                try:
                    q.append(lap_payload)
                except Exception:
                    pass
            # Broadcast updated standings snapshot (enriched with rider info)
            table = _build_standings_items()
            standings_payload = {"type": "standings", "items": table}
            for q in list(subscribers):
                try:
                    q.append(standings_payload)
                except Exception:
                    pass

            # W-011: if tag has no registered rider, fire unknown_tag SSE + add to ring buffer
            if ev.tag_id not in rider_store:
                unknown_payload: Dict[str, Any] = {
                    "type": "unknown_tag",
                    "tag_id": ev.tag_id,
                    "timestamp": ev.timestamp,
                    "antenna": ev.antenna,
                    "rssi": ev.rssi,
                }
                for q in list(subscribers):
                    try:
                        q.append(unknown_payload)
                    except Exception:
                        pass
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
# SSE stream
# ---------------------------------------------------------------------------

@app.get("/stream")
def stream_events():
    # Simple Server-Sent Events stream
    client_buf: List[Dict[str, Any]] = []
    subscribers.append(client_buf)

    def iterator():
        try:
            last_idx = 0
            while True:
                if last_idx < len(client_buf):
                    item = client_buf[last_idx]
                    last_idx += 1
                    data = json.dumps(item, separators=(",", ":"))
                    yield f"data: {data}\n\n"
                else:
                    # heartbeat
                    yield f": keepalive {_now_iso()}\n\n"
                    import time as _t

                    _t.sleep(1)
        finally:
            try:
                subscribers.remove(client_buf)
            except ValueError:
                pass

    return StreamingResponse(iterator(), media_type="text/event-stream")


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
