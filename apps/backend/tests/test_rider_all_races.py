"""POST /riders with all_races=true — day-model propagation (Karli Krit).

One tag + one number per PERSON for the whole day: a late-entry name entered
once must reach every race the tag was pre-imported into.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def app_state():
    import app as app_module
    importlib.reload(app_module)
    from app import app as fastapi_app
    with TestClient(fastapi_app) as c:
        yield c, app_module


def test_all_races_updates_every_race_with_the_tag(app_state):
    client, _ = app_state
    # Master import simulation: tag registered in the default race AND race 2.
    client.post("/riders", json={"tag_id": "DAY-1", "bib": "57", "name": ""})
    race2 = client.post("/races", json={"name": "Race 2"}).json()
    client.post(f"/races/{race2['id']}/activate")
    client.post("/riders", json={"tag_id": "DAY-1", "bib": "57", "name": ""})

    # Late entry signs on during race 2: name entered once, all_races on.
    resp = client.post("/riders", json={
        "tag_id": "DAY-1", "bib": "57", "name": "Späte Meldung", "all_races": True,
    })
    assert resp.status_code == 201
    assert resp.json()["races_updated"] == 2

    # Active race sees it immediately …
    items = client.get("/riders").json()["items"]
    assert [r["name"] for r in items if r["tag_id"] == "DAY-1"] == ["Späte Meldung"]

    # … and the other race picks it up from the DB on activation.
    races = client.get("/races").json()["items"]
    default_id = next(r["id"] for r in races if r["id"] != race2["id"])
    client.post(f"/races/{default_id}/activate")
    items = client.get("/riders").json()["items"]
    assert [r["name"] for r in items if r["tag_id"] == "DAY-1"] == ["Späte Meldung"]


def test_all_races_never_inserts_into_foreign_races(app_state):
    client, _ = app_state
    race2 = client.post("/races", json={"name": "Race 2"}).json()
    # Tag exists ONLY in the active default race.
    client.post("/riders", json={"tag_id": "DAY-2", "bib": "9", "name": "Solo",
                                 "all_races": True})
    client.post(f"/races/{race2['id']}/activate")
    assert client.get("/riders").json()["count"] == 0


def test_without_flag_response_has_no_races_updated(app_state):
    client, _ = app_state
    resp = client.post("/riders", json={"tag_id": "DAY-3", "bib": "1", "name": "X"})
    assert resp.json()["races_updated"] is None
