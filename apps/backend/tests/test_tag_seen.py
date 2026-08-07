"""Tests for the W-075 ``tag_seen`` SSE event (serial coupling mode).

Every arrive — registered AND unknown — emits one tag_seen frame per tag per
throttle interval. Registered pre-start re-scans are otherwise fully silent
(RECHECK-2026-07-25 #1 suppression), which is exactly why this event exists:
the coupling panel needs live "this tag was just read" feedback with identity
info (bib/name) attached.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient


def _tag_event(tag_id: str, ts: str = "2026-08-07T12:00:00.000Z",
               antenna: int = 1, rssi: int = -55) -> dict:
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


@pytest.fixture()
def fake_clock(app_state, monkeypatch):
    """Replace the throttle clock with a controllable one. Returns the dict."""
    _, app_module = app_state
    clock = {"t": 1000.0}
    monkeypatch.setattr(app_module, "_monotonic", lambda: clock["t"])
    return clock


def _subscribe(app_module):
    buf: list = []
    with app_module._subscribers_lock:
        app_module.subscribers.append(buf)
    return buf


def _unsubscribe(app_module, buf):
    with app_module._subscribers_lock:
        try:
            app_module.subscribers.remove(buf)
        except ValueError:
            pass


def _frames(buf, type_):
    return [f for f in buf if f.get("type") == type_]


def test_tag_seen_fires_for_unknown_tag(app_state):
    client, app_module = app_state
    buf = _subscribe(app_module)
    try:
        client.post("/events/tag/batch", json=_batch([
            _tag_event("UNSEEN-1", antenna=2, rssi=-48),
        ]))
        seen = _frames(buf, "tag_seen")
        assert len(seen) == 1
        f = seen[0]
        assert f["tag_id"] == "UNSEEN-1"
        assert f["timestamp"] == "2026-08-07T12:00:00.000Z"
        assert f["antenna"] == 2
        assert f["rssi"] == -48
        assert f["registered"] is False
        assert f["bib"] is None
        assert f["name"] is None
        # The one-shot coupling path must keep working: unknown_tag coexists.
        assert len(_frames(buf, "unknown_tag")) == 1
    finally:
        _unsubscribe(app_module, buf)


def test_tag_seen_fires_for_registered_tag_with_bib_name(app_state):
    client, app_module = app_state
    client.post("/riders", json={"tag_id": "REGSEEN-1", "bib": "5", "name": "Eve"})
    buf = _subscribe(app_module)
    try:
        client.post("/events/tag/batch", json=_batch([_tag_event("REGSEEN-1")]))
        seen = _frames(buf, "tag_seen")
        assert len(seen) == 1
        assert seen[0]["registered"] is True
        assert seen[0]["bib"] == "5"
        assert seen[0]["name"] == "Eve"
        # Registered first pass still broadcasts lap; never unknown_tag.
        assert len(_frames(buf, "lap")) == 1
        assert len(_frames(buf, "unknown_tag")) == 0
    finally:
        _unsubscribe(app_module, buf)


def test_tag_seen_fires_even_when_lap_broadcast_suppressed(app_state, fake_clock):
    """The raison d'être: pre-start re-scans are silent on lap/standings
    (RECHECK #1 state-diff guard) but MUST still announce via tag_seen."""
    client, app_module = app_state
    client.post("/riders", json={"tag_id": "REGSEEN-2", "bib": "7", "name": "Bob"})
    buf = _subscribe(app_module)
    try:
        client.post("/events/tag/batch", json=_batch([
            _tag_event("REGSEEN-2", ts="2026-08-07T12:00:00.000Z"),
        ]))
        fake_clock["t"] += 3.0  # past the tag_seen throttle
        client.post("/events/tag/batch", json=_batch([
            _tag_event("REGSEEN-2", ts="2026-08-07T12:00:03.000Z"),
        ]))
        assert len(_frames(buf, "tag_seen")) == 2
        # Second pre-start scan changed nothing → lap suppression intact.
        assert len(_frames(buf, "lap")) == 1
    finally:
        _unsubscribe(app_module, buf)


def test_tag_seen_throttled_within_interval(app_state, fake_clock):
    client, app_module = app_state
    buf = _subscribe(app_module)
    try:
        # Same tag twice + another tag, all at the same instant (one batch):
        # per-tag throttle → A once, B once.
        client.post("/events/tag/batch", json=_batch([
            _tag_event("THROT-A", ts="2026-08-07T12:00:00.000Z"),
            _tag_event("THROT-A", ts="2026-08-07T12:00:00.500Z"),
            _tag_event("THROT-B", ts="2026-08-07T12:00:00.700Z"),
        ]))
        assert [f["tag_id"] for f in _frames(buf, "tag_seen")] == ["THROT-A", "THROT-B"]

        # Still inside the interval → suppressed.
        fake_clock["t"] += 1.0
        client.post("/events/tag/batch", json=_batch([
            _tag_event("THROT-A", ts="2026-08-07T12:00:01.500Z"),
        ]))
        assert len(_frames(buf, "tag_seen")) == 2

        # Past the interval → fires again.
        fake_clock["t"] += 1.5
        client.post("/events/tag/batch", json=_batch([
            _tag_event("THROT-A", ts="2026-08-07T12:00:03.000Z"),
        ]))
        assert len(_frames(buf, "tag_seen")) == 3
    finally:
        _unsubscribe(app_module, buf)


def test_tag_seen_throttle_resets_on_race_reset(app_state, fake_clock):
    client, app_module = app_state
    buf = _subscribe(app_module)
    try:
        client.post("/events/tag/batch", json=_batch([_tag_event("RESET-1")]))
        assert len(_frames(buf, "tag_seen")) == 1

        resp = client.post("/race/reset")
        assert resp.status_code == 204

        # Same tag, same fake instant — throttle map was cleared → fires.
        client.post("/events/tag/batch", json=_batch([
            _tag_event("RESET-1", ts="2026-08-07T12:00:01.000Z"),
        ]))
        assert len(_frames(buf, "tag_seen")) == 2
    finally:
        _unsubscribe(app_module, buf)


def test_tag_seen_after_race_switch_reflects_new_registry(app_state, fake_clock):
    client, app_module = app_state
    client.post("/riders", json={"tag_id": "SWITCH-1", "bib": "9", "name": "Ada"})
    buf = _subscribe(app_module)
    try:
        client.post("/events/tag/batch", json=_batch([_tag_event("SWITCH-1")]))
        first = _frames(buf, "tag_seen")
        assert len(first) == 1 and first[0]["registered"] is True

        other = client.post("/races", json={"name": "Race 2"}).json()
        client.post(f"/races/{other['id']}/activate")

        # Same fake instant: switch cleared the throttle map, and the rider
        # was scoped to the OLD race → announces again, now unregistered.
        client.post("/events/tag/batch", json=_batch([
            _tag_event("SWITCH-1", ts="2026-08-07T12:00:02.000Z"),
        ]))
        seen = _frames(buf, "tag_seen")
        assert len(seen) == 2
        assert seen[1]["registered"] is False
        assert seen[1]["bib"] is None
    finally:
        _unsubscribe(app_module, buf)
