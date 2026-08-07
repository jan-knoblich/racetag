"""Tests for GET /tags.csv — tag-inventory export (Karlie-Krit start-list prep).

Wave tags in a scratch race, download an import-ready ``tag_id;bib;name``
template: one row per distinct tag, first-read (= wave) order, read counts as
a per-tag quality signal, no '#'-metadata lines (the rider importer skips
exactly one header row).
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient


def _tag_event(tag_id: str, ts: str, antenna: int = 1, rssi: int = -50) -> dict:
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


@pytest.fixture()
def app_state():
    """Reload app module and return (TestClient, app_module) with fresh state."""
    import app as app_module
    importlib.reload(app_module)
    from app import app as fastapi_app
    with TestClient(fastapi_app) as c:
        yield c, app_module


def test_tags_csv_empty_race(app_state):
    client, _ = app_state
    resp = client.get("/tags.csv")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    lines = resp.text.lstrip("\ufeff").strip().split("\n")
    assert len(lines) == 1  # header only
    assert lines[0].startswith("tag_id;bib;name;")


def test_tags_csv_wave_order_dedup_and_read_counts(app_state):
    client, _ = app_state
    # Wave three tags: B first (twice — dedup + count), then A, then C.
    events = [
        _tag_event("TAG-B", "2026-08-08T08:00:01.000Z"),
        _tag_event("TAG-B", "2026-08-08T08:00:20.000Z"),
        _tag_event("TAG-A", "2026-08-08T08:00:30.000Z"),
        _tag_event("TAG-C", "2026-08-08T08:00:40.000Z"),
    ]
    resp = client.post("/events/tag/batch", json={"events": events})
    assert resp.status_code == 200

    resp = client.get("/tags.csv")
    assert resp.status_code == 200
    body = resp.text
    assert body.startswith("\ufeff")  # BOM for Excel
    assert "#" not in body.split("\n")[0]  # no metadata lines — must round-trip

    rows = [line.split(";") for line in body.lstrip("\ufeff").strip().split("\n")]
    header, data = rows[0], rows[1:]
    assert header[:3] == ["tag_id", "bib", "name"]
    # First-read order, one row per tag.
    assert [r[0] for r in data] == ["TAG-B", "TAG-A", "TAG-C"]
    # bib/name empty — ready to fill; read counts per tag.
    assert all(r[1] == "" and r[2] == "" for r in data)
    assert [r[3] for r in data] == ["2", "1", "1"]
    # Filename hint present for the download path.
    assert "racetag-tags-" in resp.headers.get("content-disposition", "")


def test_tags_csv_includes_unregistered_and_registered(app_state):
    client, _ = app_state
    client.post("/riders", json={"tag_id": "REG-1", "bib": "7", "name": "Känguru"})
    events = [
        _tag_event("REG-1", "2026-08-08T09:00:00.000Z"),
        _tag_event("UNREG-1", "2026-08-08T09:00:05.000Z"),
    ]
    client.post("/events/tag/batch", json={"events": events})
    resp = client.get("/tags.csv")
    rows = [line.split(";")
            for line in resp.text.lstrip("\ufeff").strip().split("\n")[1:]]
    assert [r[0] for r in rows] == ["REG-1", "UNREG-1"]
    # W-075: registered tags export with bib/name prefilled (master list
    # after a coupling session); unregistered stay blank for filling.
    assert rows[0][1] == "7" and rows[0][2] == "K\u00e4nguru"
    assert rows[1][1] == "" and rows[1][2] == ""


def test_tags_csv_prefill_quotes_semicolon_in_name(app_state):
    client, _ = app_state
    client.post("/riders", json={"tag_id": "SEMI-1", "bib": "9", "name": "A;B"})
    client.post("/events/tag/batch", json={"events": [
        _tag_event("SEMI-1", "2026-08-08T10:00:00.000Z"),
    ]})
    resp = client.get("/tags.csv")
    data_line = resp.text.lstrip("\ufeff").strip().split("\n")[1]
    # csv.writer must quote the field so the importer's quote-aware parser
    # round-trips it as ONE name field.
    assert '"A;B"' in data_line
    assert data_line.startswith('SEMI-1;9;"A;B";')
