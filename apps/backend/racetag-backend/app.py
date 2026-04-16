from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from typing import Any, Dict, List

from fastapi import FastAPI, Depends, HTTPException, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.security import APIKeyHeader

from domain.race import RaceState
from models_api import (
    EventType,
    TagEventDTO,
    ParticipantDTO,
    ClassificationDTO,
    RaceDTO,
    BatchIngestResultDTO,
    TagEventBatchDTO,
)



# API Key using env RACETAG_API_KEY
API_KEY_HEADER_NAME = "X-API-Key"
_API_KEY = os.getenv("RACETAG_API_KEY")
_RACE_TOTAL_LAPS = int(os.getenv("RACE_TOTAL_LAPS", "5"))
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
race = RaceState(total_laps=_RACE_TOTAL_LAPS)

# Debug/event store
events: List[TagEventDTO] = []

# SSE subscribers: list of buffers
subscribers: List[List[Dict[str, Any]]] = []


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


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
            # Broadcast updated standings snapshot
            table = [s.model_dump() for s in race.standings()]
            standings_payload = {"type": "standings", "items": table}
            for q in list(subscribers):
                try:
                    q.append(standings_payload)
                except Exception:
                    pass
    return {"events_processed": accepted}


@app.get("/classification", response_model=ClassificationDTO)
def get_classification():
    # Current classification ordered by race rules
    items = [ParticipantDTO(**p.model_dump()).model_dump() for p in race.standings()]
    return {"count": len(items), "standings": items}


@app.get("/race", response_model=RaceDTO)
def get_race():
    return {
        "total_laps": race.total_laps,
        "start_time": race.start_time.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "participants": [ParticipantDTO(**p.model_dump()).model_dump() for p in race.participants.values()],
    }


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
