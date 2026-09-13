"""Rider-Stammdaten verein/uci_id (SRB-Export): Persistenz + Keep-Semantik.

Ein Upsert mit leeren Stammdaten (Koppel-Modus, Namens-Push, CSV-Re-Import)
darf vorhandene Werte nie wegwischen — nur nicht-leere Werte überschreiben.
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


def test_stammdaten_roundtrip(app_state):
    client, _ = app_state
    resp = client.post("/riders", json={
        "tag_id": "SRB-1", "bib": "7", "name": "Christian Hoffmann",
        "verein": "OWK Markstätter Bikescheune", "uci_id": "10059208679",
    })
    assert resp.status_code == 201
    dto = resp.json()
    assert dto["verein"] == "OWK Markstätter Bikescheune"
    assert dto["uci_id"] == "10059208679"

    got = client.get("/riders/SRB-1").json()
    assert got["verein"] == "OWK Markstätter Bikescheune"
    assert got["uci_id"] == "10059208679"


def test_empty_upsert_keeps_stammdaten(app_state):
    client, _ = app_state
    client.post("/riders", json={"tag_id": "SRB-2", "bib": "9", "name": "A",
                                 "verein": "RSV Test", "uci_id": "123"})
    # Namens-Push/Koppel-Upsert ohne Stammdaten:
    resp = client.post("/riders", json={"tag_id": "SRB-2", "bib": "9",
                                        "name": "Anna Neu"})
    dto = resp.json()
    assert dto["name"] == "Anna Neu"
    assert dto["verein"] == "RSV Test"
    assert dto["uci_id"] == "123"
    got = client.get("/riders/SRB-2").json()
    assert (got["verein"], got["uci_id"]) == ("RSV Test", "123")


def test_nonempty_overwrites(app_state):
    client, _ = app_state
    client.post("/riders", json={"tag_id": "SRB-3", "bib": "1", "name": "B",
                                 "verein": "Alt", "uci_id": "111"})
    client.post("/riders", json={"tag_id": "SRB-3", "bib": "1", "name": "B",
                                 "verein": "Neu"})
    got = client.get("/riders/SRB-3").json()
    assert got["verein"] == "Neu"
    assert got["uci_id"] == "111"  # leer nicht angefasst


def test_all_races_propagates_with_keep_semantics(app_state):
    client, _ = app_state
    client.post("/riders", json={"tag_id": "SRB-4", "bib": "5", "name": "C",
                                 "verein": "Verein X"})
    race2 = client.post("/races", json={"name": "Race 2"}).json()
    client.post(f"/races/{race2['id']}/activate")
    client.post("/riders", json={"tag_id": "SRB-4", "bib": "5", "name": "C",
                                 "verein": "Verein X"})

    # UCI-ID kommt später dazu, all_races, Verein diesmal leer → bleibt.
    resp = client.post("/riders", json={
        "tag_id": "SRB-4", "bib": "5", "name": "C",
        "uci_id": "999", "all_races": True,
    })
    assert resp.json()["races_updated"] == 2
    got = client.get("/riders/SRB-4").json()
    assert (got["verein"], got["uci_id"]) == ("Verein X", "999")

    races = client.get("/races").json()["items"]
    other = next(r["id"] for r in races if r["id"] != race2["id"])
    client.post(f"/races/{other}/activate")
    got = client.get("/riders/SRB-4").json()
    assert (got["verein"], got["uci_id"]) == ("Verein X", "999")
