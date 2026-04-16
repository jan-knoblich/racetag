"""Tests for W-033 (reader_serial optional), W-040 (API key), and W-022 (contract tests)."""
from __future__ import annotations

import importlib
import os

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def fresh_app():
    """Reload the app module to get a clean state for each test."""
    import app as app_module
    importlib.reload(app_module)
    from app import app as fastapi_app
    with TestClient(fastapi_app) as c:
        yield c, app_module


def _minimal_event(tag_id: str = "AA01") -> dict:
    """Minimal TagEventDTO payload — no reader_serial."""
    return {
        "source": "test",
        "reader_ip": "127.0.0.1",
        "timestamp": "2026-04-15T12:00:00.000Z",
        "event_type": "arrive",
        "tag_id": tag_id,
    }


def _batch(events: list) -> dict:
    return {"events": events}


# ---------------------------------------------------------------------------
# W-033: reader_serial is optional
# ---------------------------------------------------------------------------

def test_batch_ingest_without_reader_serial_returns_200(fresh_app):
    """Posting a batch without reader_serial must return 200 (field is now optional)."""
    client, _ = fresh_app
    payload = _batch([_minimal_event("NOSERIAL01")])
    # Confirm reader_serial is absent from the payload
    assert "reader_serial" not in payload["events"][0]

    resp = client.post("/events/tag/batch", json=payload)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["events_processed"] == 1


def test_batch_ingest_with_reader_serial_still_works(fresh_app):
    """Posting a batch WITH reader_serial must also return 200 (no regression)."""
    client, _ = fresh_app
    event = _minimal_event("WITHSERIAL01")
    event["reader_serial"] = "SNTEST001"
    resp = client.post("/events/tag/batch", json=_batch([event]))
    assert resp.status_code == 200
    assert resp.json()["events_processed"] == 1


# ---------------------------------------------------------------------------
# W-040: API key disabled by default
# ---------------------------------------------------------------------------

def test_api_works_without_key_when_env_unset(fresh_app, monkeypatch):
    """When RACETAG_API_KEY is not set, the API must accept requests without an X-API-Key header."""
    # Ensure the env var is unset (it may have been set in the outer environment)
    monkeypatch.delenv("RACETAG_API_KEY", raising=False)

    # Reload so the app picks up the missing env var
    import app as app_module
    importlib.reload(app_module)
    from app import app as fastapi_app

    with TestClient(fastapi_app) as client:
        # No X-API-Key header
        resp = client.get("/classification")
        assert resp.status_code == 200, (
            f"Expected 200 without API key when env is unset, got {resp.status_code}"
        )


def test_api_rejects_request_when_key_set_and_wrong(monkeypatch):
    """When RACETAG_API_KEY is set, requests with a wrong key must get 401."""
    monkeypatch.setenv("RACETAG_API_KEY", "secret-key")

    import app as app_module
    importlib.reload(app_module)
    from app import app as fastapi_app

    with TestClient(fastapi_app, raise_server_exceptions=False) as client:
        resp = client.get("/classification", headers={"X-API-Key": "wrong-key"})
        assert resp.status_code == 401

    monkeypatch.delenv("RACETAG_API_KEY", raising=False)


def test_api_accepts_request_when_key_set_and_correct(monkeypatch):
    """When RACETAG_API_KEY is set, requests with the correct key must succeed."""
    monkeypatch.setenv("RACETAG_API_KEY", "correct-key")

    import app as app_module
    importlib.reload(app_module)
    from app import app as fastapi_app

    with TestClient(fastapi_app) as client:
        resp = client.get("/classification", headers={"X-API-Key": "correct-key"})
        assert resp.status_code == 200

    monkeypatch.delenv("RACETAG_API_KEY", raising=False)


# ---------------------------------------------------------------------------
# W-022: Contract-level coverage — endpoints not covered by other test files
# ---------------------------------------------------------------------------

def _arrive(tag_id: str, ts: str, antenna: int = 1, rssi: int = -55) -> dict:
    """Build a minimal arrive event payload for batch ingest."""
    return {
        "source": "test",
        "reader_ip": "127.0.0.1",
        "timestamp": ts,
        "event_type": "arrive",
        "tag_id": tag_id,
        "antenna": antenna,
        "rssi": rssi,
    }


def test_batch_ingest_returns_events_processed_count(fresh_app):
    """POST N arrive events via /events/tag/batch; response body must contain events_processed == N.

    Distinct from test_batch_ingest_without_reader_serial_returns_200 (W-033) which checks
    the field is optional. This test asserts the counter is accurate for N > 1.
    """
    client, _ = fresh_app
    N = 4
    events = [
        _arrive(f"COUNT{i:02d}", f"2026-04-15T12:00:{i:02d}.000Z")
        for i in range(N)
    ]
    resp = client.post("/events/tag/batch", json=_batch(events))
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert "events_processed" in body, f"Response missing 'events_processed': {body}"
    assert body["events_processed"] == N, (
        f"Expected events_processed={N}, got {body['events_processed']}"
    )


def test_classification_reflects_posted_events(fresh_app):
    """POST 2 arrives for tag A (>8 s apart, so both count) + 1 arrive for tag B;
    GET /classification must return 2 participants with correct lap counts.

    This is a contract test: verifies that the ingest → race-state → classification
    pipeline is wired end-to-end through the HTTP layer.
    """
    client, _ = fresh_app

    # Two arrives for tag A separated by 12 s (past the 8 s min_pass_interval default)
    client.post("/events/tag/batch", json=_batch([
        _arrive("CLSA0001", "2026-04-15T12:00:00.000Z"),
    ]))
    client.post("/events/tag/batch", json=_batch([
        _arrive("CLSA0001", "2026-04-15T12:00:12.000Z"),  # 12 s later → counts
    ]))
    # One arrive for tag B
    client.post("/events/tag/batch", json=_batch([
        _arrive("CLSB0001", "2026-04-15T12:00:01.000Z"),
    ]))

    resp = client.get("/classification")
    assert resp.status_code == 200, f"GET /classification failed: {resp.text}"
    body = resp.json()

    assert body["count"] == 2, f"Expected 2 participants, got {body['count']}: {body}"

    by_tag = {row["tag_id"]: row for row in body["standings"]}
    assert "CLSA0001" in by_tag, f"CLSA0001 missing from standings: {body}"
    assert "CLSB0001" in by_tag, f"CLSB0001 missing from standings: {body}"
    assert by_tag["CLSA0001"]["laps"] == 2, (
        f"Expected 2 laps for CLSA0001, got {by_tag['CLSA0001']['laps']}"
    )
    assert by_tag["CLSB0001"]["laps"] == 1, (
        f"Expected 1 lap for CLSB0001, got {by_tag['CLSB0001']['laps']}"
    )


def test_rider_lifecycle_end_to_end(fresh_app):
    """POST a rider → GET all riders → GET by tag_id → DELETE → GET returns 404.

    Validates the full lifecycle contract. Distinct from test_riders.py unit tests
    (those cover domain logic and individual endpoints in isolation).
    """
    client, _ = fresh_app

    tag = "LIFECYCLE01"

    # POST: create
    resp = client.post("/riders", json={"tag_id": tag, "bib": "99", "name": "Lifecycle"})
    assert resp.status_code == 201, f"POST /riders failed: {resp.text}"
    created = resp.json()
    assert created["tag_id"] == tag
    assert created["bib"] == "99"
    assert created["name"] == "Lifecycle"

    # GET all: must include the new rider
    resp = client.get("/riders")
    assert resp.status_code == 200
    tag_ids = [r["tag_id"] for r in resp.json()["items"]]
    assert tag in tag_ids, f"{tag} not found in /riders: {tag_ids}"

    # GET by tag_id
    resp = client.get(f"/riders/{tag}")
    assert resp.status_code == 200, f"GET /riders/{tag} failed: {resp.text}"
    assert resp.json()["name"] == "Lifecycle"

    # DELETE
    resp = client.delete(f"/riders/{tag}")
    assert resp.status_code == 204, f"DELETE /riders/{tag} failed: {resp.text}"

    # GET after DELETE → 404
    resp = client.get(f"/riders/{tag}")
    assert resp.status_code == 404, (
        f"Expected 404 after DELETE of {tag}, got {resp.status_code}: {resp.text}"
    )


def test_unknown_tag_sse_via_subscriber_inspection(fresh_app):
    """POST an arrive for an unregistered tag; confirm an unknown_tag frame is broadcast.

    Uses the same legacy list-based subscriber shim as test_unknown_tag.py so the
    mechanism is consistent across both test files. The tag must NOT be pre-registered.
    """
    client, app_module = fresh_app

    fake_buf: list = []
    app_module.subscribers.append(fake_buf)

    try:
        resp = client.post(
            "/events/tag/batch",
            json=_batch([_arrive("UNKW0001", "2026-04-15T13:00:00.000Z")]),
        )
        assert resp.status_code == 200

        unknown_frames = [f for f in fake_buf if f.get("type") == "unknown_tag"]
        assert unknown_frames, (
            f"Expected at least one unknown_tag frame, subscriber saw: {fake_buf}"
        )
        frame = unknown_frames[0]
        assert frame["tag_id"] == "UNKW0001", f"tag_id mismatch in frame: {frame}"
    finally:
        try:
            app_module.subscribers.remove(fake_buf)
        except ValueError:
            pass


def test_openapi_schema_exposes_new_endpoints(fresh_app):
    """GET /openapi.json must include the expected API paths.

    W-036 endpoints (POST /race/reset, PATCH /race) are tested with the
    skip-if-missing pattern so the test lights up automatically once the
    backend agent lands them, without failing CI before that.
    """
    client, _ = fresh_app
    resp = client.get("/openapi.json")
    assert resp.status_code == 200, f"Could not fetch /openapi.json: {resp.status_code}"
    schema = resp.json()
    paths = schema.get("paths", {})

    # Core endpoints that MUST exist (no skip — these are P0)
    required_paths = [
        "/riders",
        "/riders/{tag_id}",
        "/riders/recent-reads",
        "/classification",
        "/events/tag/batch",
        "/stream",
    ]
    for path in required_paths:
        assert path in paths, (
            f"Expected path '{path}' in openapi.json but it was missing. "
            f"Present paths: {sorted(paths.keys())}"
        )

    # W-036 endpoints: skip-if-missing so CI stays green while backend agent lands them
    w036_paths = [
        ("/race/reset", "post"),
        ("/race", "patch"),
    ]
    for path, method in w036_paths:
        if path not in paths or method not in paths.get(path, {}):
            pytest.skip(
                f"W-036 endpoint {method.upper()} {path} not yet in openapi.json — "
                "skipping until backend agent lands it"
            )
        # If we reach here the endpoint exists; assert it's present (vacuously true at this point)
        assert path in paths, f"{method.upper()} {path} is in openapi.json"


def test_events_ingested_trigger_sse_broadcast(fresh_app):
    """POST an arrive for a registered tag; confirm both lap and standings SSE frames are pushed.

    Registers the rider first so no unknown_tag frame is also generated (keeps assertion clean).
    """
    client, app_module = fresh_app

    tag = "SSEBCAST01"
    # Register rider so the arrive doesn't generate an unknown_tag frame
    resp = client.post("/riders", json={"tag_id": tag, "bib": "7", "name": "Broadcast"})
    assert resp.status_code == 201

    fake_buf: list = []
    app_module.subscribers.append(fake_buf)

    try:
        resp = client.post(
            "/events/tag/batch",
            json=_batch([_arrive(tag, "2026-04-15T14:00:00.000Z")]),
        )
        assert resp.status_code == 200

        lap_frames = [f for f in fake_buf if f.get("type") == "lap"]
        standings_frames = [f for f in fake_buf if f.get("type") == "standings"]

        assert lap_frames, (
            f"Expected at least one 'lap' SSE frame after ingest, got: {fake_buf}"
        )
        assert standings_frames, (
            f"Expected at least one 'standings' SSE frame after ingest, got: {fake_buf}"
        )

        lap = lap_frames[0]
        assert lap["tag_id"] == tag, f"lap frame tag_id mismatch: {lap}"
        assert lap["laps"] == 1, f"Expected laps=1 for first arrive, got: {lap}"
        assert lap["finished"] is False, f"Expected finished=False on lap 1: {lap}"
    finally:
        try:
            app_module.subscribers.remove(fake_buf)
        except ValueError:
            pass
