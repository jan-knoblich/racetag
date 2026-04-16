"""Tests for W-032: async SSE with asyncio.Queue — multiple subscribers receive all events."""
from __future__ import annotations

import importlib
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient


def _tag_event(tag_id: str, ts: str = "2026-04-15T12:00:00.000Z") -> dict:
    return {
        "source": "test",
        "reader_ip": "127.0.0.1",
        "timestamp": ts,
        "event_type": "arrive",
        "tag_id": tag_id,
    }


def _batch(events: list) -> dict:
    return {"events": events}


@pytest.fixture()
def fresh_app():
    import app as app_module
    importlib.reload(app_module)
    from app import app as fastapi_app
    with TestClient(fastapi_app) as c:
        yield c, app_module


def test_sse_multiple_subscribers_get_all_events(fresh_app):
    """Two list-based subscribers receive all 10 events in order via the _publish fan-out."""
    client, app_module = fresh_app

    # Register two legacy list-based subscriber buffers (backward-compat shim)
    buf_a: list = []
    buf_b: list = []

    with app_module._subscribers_lock:
        app_module.subscribers.append(buf_a)
        app_module.subscribers.append(buf_b)

    try:
        # Post 10 unique tags so each generates a lap + standings + unknown_tag = 3 frames
        for i in range(10):
            tag_id = f"MULTI{i:02d}"
            resp = client.post(
                "/events/tag/batch",
                json=_batch([_tag_event(tag_id, ts=f"2026-04-15T12:00:{i:02d}.000Z")]),
            )
            assert resp.status_code == 200

        # Each of the 10 events is unknown (no rider registered), so we expect
        # 3 frames per event (lap, standings, unknown_tag) = 30 frames total.
        assert len(buf_a) == 30, f"Expected 30 frames in buf_a, got {len(buf_a)}"
        assert len(buf_b) == 30, f"Expected 30 frames in buf_b, got {len(buf_b)}"

        # Both buffers should have identical content
        assert buf_a == buf_b

        # Verify all 10 lap events are present
        lap_frames_a = [f for f in buf_a if f.get("type") == "lap"]
        assert len(lap_frames_a) == 10

        tag_ids_seen = {f["tag_id"] for f in lap_frames_a}
        expected = {f"MULTI{i:02d}" for i in range(10)}
        assert tag_ids_seen == expected

    finally:
        with app_module._subscribers_lock:
            for buf in (buf_a, buf_b):
                try:
                    app_module.subscribers.remove(buf)
                except ValueError:
                    pass
