"""Tests for W-011: unknown_tag SSE event + recent-reads ring buffer."""
from __future__ import annotations

import importlib
import json
import sys
import threading
import time

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tag_event(tag_id: str, ts: str = "2026-04-15T12:00:00.000Z", antenna: int = 1, rssi: int = -50) -> dict:
    return {
        "source": "test",
        "reader_ip": "127.0.0.1",
        "reader_serial": "SN001",
        "timestamp": ts,
        "event_type": "arrive",
        "tag_id": tag_id,
        "antenna": antenna,
        "rssi": rssi,
    }


def _batch(events: list) -> dict:
    return {"events": events}


@pytest.fixture()
def app_state():
    """Reload app module and return (TestClient, app_module) with fresh state."""
    import app as app_module
    importlib.reload(app_module)
    from app import app as fastapi_app
    with TestClient(fastapi_app) as c:
        yield c, app_module


# ---------------------------------------------------------------------------
# Tests for the ring buffer and SSE push logic (via module inspection)
# ---------------------------------------------------------------------------

def test_unknown_tag_sse_event_fires_for_unregistered(app_state):
    """An arrive for an unregistered tag must push an unknown_tag frame to subscribers."""
    client, app_module = app_state

    # Manually register a fake subscriber buffer so we can inspect what was pushed.
    fake_buf: list = []
    app_module.subscribers.append(fake_buf)

    try:
        resp = client.post(
            "/events/tag/batch",
            json=_batch([_tag_event("UNREG-1")]),
        )
        assert resp.status_code == 200

        unknown_frames = [f for f in fake_buf if f.get("type") == "unknown_tag"]
        assert unknown_frames, f"Expected an unknown_tag frame in subscriber buffer, got: {fake_buf}"
        assert unknown_frames[0]["tag_id"] == "UNREG-1"
        assert unknown_frames[0]["antenna"] == 1
        assert unknown_frames[0]["rssi"] == -50
    finally:
        try:
            app_module.subscribers.remove(fake_buf)
        except ValueError:
            pass


def test_unknown_tag_sse_NOT_fired_for_registered(app_state):
    """An arrive for a registered rider must NOT push an unknown_tag frame."""
    client, app_module = app_state

    # Register the tag
    resp = client.post("/riders", json={"tag_id": "REG-1", "bib": "5", "name": "Eve"})
    assert resp.status_code == 201

    fake_buf: list = []
    app_module.subscribers.append(fake_buf)

    try:
        resp = client.post(
            "/events/tag/batch",
            json=_batch([_tag_event("REG-1")]),
        )
        assert resp.status_code == 200

        unknown_frames = [f for f in fake_buf if f.get("type") == "unknown_tag"]
        assert unknown_frames == [], (
            f"Did not expect unknown_tag frames for a registered tag, got: {unknown_frames}"
        )
        # Should still have lap + standings frames
        assert any(f.get("type") == "lap" for f in fake_buf)
    finally:
        try:
            app_module.subscribers.remove(fake_buf)
        except ValueError:
            pass


def test_recent_reads_endpoint_returns_reverse_chrono(app_state):
    """POST three unknown-tag events; GET /riders/recent-reads returns them newest-first."""
    client, _ = app_state

    timestamps = [
        "2026-04-15T12:00:01.000Z",
        "2026-04-15T12:00:02.000Z",
        "2026-04-15T12:00:03.000Z",
    ]
    tag_ids = ["UNREG-A", "UNREG-B", "UNREG-C"]

    for tag_id, ts in zip(tag_ids, timestamps):
        resp = client.post("/events/tag/batch", json=_batch([_tag_event(tag_id, ts=ts)]))
        assert resp.status_code == 200

    resp = client.get("/riders/recent-reads?limit=10")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 3

    returned_tags = [item["tag_id"] for item in body["items"]]
    # Newest-first → UNREG-C, UNREG-B, UNREG-A
    assert returned_tags == ["UNREG-C", "UNREG-B", "UNREG-A"]


def test_recent_reads_ring_buffer_caps_at_50(app_state):
    """POST 60 unique unknown-tag arrives; endpoint must return at most 50."""
    client, _ = app_state

    for i in range(60):
        tag_id = f"BULK-{i:03d}"
        # Each tag_id is unique so the 8s cooldown does not apply across tags
        ts = f"2026-04-15T12:00:{i % 60:02d}.000Z"
        client.post("/events/tag/batch", json=_batch([_tag_event(tag_id, ts=ts)]))

    resp = client.get("/riders/recent-reads?limit=50")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 50
    assert len(body["items"]) == 50
