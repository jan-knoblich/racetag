"""Tests for W-010: Rider domain + CRUD endpoints."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from domain.riders import Rider, RiderStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_rider(tag_id: str = "AABBCCDD", bib: str = "42", name: str = "Alice") -> Rider:
    return Rider(tag_id=tag_id, bib=bib, name=name, created_at=_now())


def _tag_event(tag_id: str, ts: str = "2026-04-15T12:00:00.000Z") -> dict:
    return {
        "source": "test",
        "reader_ip": "127.0.0.1",
        "reader_serial": "SN001",
        "timestamp": ts,
        "event_type": "arrive",
        "tag_id": tag_id,
    }


def _batch(events: list) -> dict:
    return {"events": events}


# ---------------------------------------------------------------------------
# Domain-level tests
# ---------------------------------------------------------------------------

def test_rider_store_upsert_get_list_delete():
    store = RiderStore()

    # Initially empty
    assert store.list() == []
    assert store.get("AABBCCDD") is None
    assert "AABBCCDD" not in store

    # Upsert
    rider = _make_rider("AABBCCDD")
    returned = store.upsert(rider)
    assert returned.tag_id == "AABBCCDD"
    assert "AABBCCDD" in store

    # Get
    fetched = store.get("AABBCCDD")
    assert fetched is not None
    assert fetched.bib == "42"

    # List
    store.upsert(_make_rider("11223344", bib="7", name="Bob"))
    assert len(store.list()) == 2

    # Delete existing
    removed = store.delete("AABBCCDD")
    assert removed is True
    assert "AABBCCDD" not in store
    assert len(store.list()) == 1

    # Delete non-existing
    removed_again = store.delete("AABBCCDD")
    assert removed_again is False


# ---------------------------------------------------------------------------
# HTTP endpoint tests (fresh app instance per test via module reload trick)
# ---------------------------------------------------------------------------

@pytest.fixture()
def client():
    """Return a TestClient backed by a fresh app instance with empty state."""
    # Import app module fresh so module-level state (race, rider_store, etc.) is reset.
    import importlib
    import app as app_module
    importlib.reload(app_module)
    from app import app as fastapi_app
    with TestClient(fastapi_app) as c:
        yield c


def test_post_riders_endpoint_persists(client):
    resp = client.post("/riders", json={"tag_id": "AABBCCDD", "bib": "42", "name": "Alice"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["tag_id"] == "AABBCCDD"
    assert body["bib"] == "42"
    assert body["name"] == "Alice"
    assert "created_at" in body

    # GET by tag_id
    resp2 = client.get("/riders/AABBCCDD")
    assert resp2.status_code == 200
    assert resp2.json()["name"] == "Alice"


def test_get_riders_lists_all(client):
    for i, (tag, bib, name) in enumerate([
        ("TAG001", "1", "Rider One"),
        ("TAG002", "2", "Rider Two"),
        ("TAG003", "3", "Rider Three"),
    ]):
        client.post("/riders", json={"tag_id": tag, "bib": bib, "name": name})

    resp = client.get("/riders")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 3
    assert len(body["items"]) == 3


def test_delete_riders_endpoint(client):
    client.post("/riders", json={"tag_id": "DDEEFF00", "bib": "99", "name": "Charlie"})

    del_resp = client.delete("/riders/DDEEFF00")
    assert del_resp.status_code == 204

    get_resp = client.get("/riders/DDEEFF00")
    assert get_resp.status_code == 404


def test_delete_rider_not_found(client):
    resp = client.delete("/riders/NONEXISTENT")
    assert resp.status_code == 404


def test_standings_enriched_with_bib_name(client):
    # Register a rider for TAG-X
    client.post("/riders", json={"tag_id": "TAGX0001", "bib": "17", "name": "Dave"})

    # Post an arrive event for that tag
    resp = client.post(
        "/events/tag/batch",
        json=_batch([_tag_event("TAGX0001")])
    )
    assert resp.status_code == 200

    # GET /classification must include bib and name
    cls_resp = client.get("/classification")
    assert cls_resp.status_code == 200
    standings = cls_resp.json()["standings"]
    assert len(standings) == 1
    row = standings[0]
    assert row["tag_id"] == "TAGX0001"
    assert row["bib"] == "17"
    assert row["name"] == "Dave"
