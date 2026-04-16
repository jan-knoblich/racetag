"""Tests for W-033 (reader_serial optional) and W-040 (API key disabled by default)."""
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
