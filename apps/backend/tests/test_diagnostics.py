"""Tests for W-051: GET /diagnostics/antennas.

Each test reloads the app module so that the module-level storage + race
state are fresh.  The autouse _isolate_data_dir fixture in conftest.py
redirects RACETAG_DATA_DIR to a per-test tmp dir.
"""
from __future__ import annotations

import importlib
from datetime import datetime, timezone, timedelta

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tag_event(
    tag_id: str,
    ts: str,
    antenna: int,
) -> dict:
    return {
        "source": "test",
        "reader_ip": "127.0.0.1",
        "timestamp": ts,
        "event_type": "arrive",
        "tag_id": tag_id,
        "antenna": antenna,
    }


def _batch(events: list) -> dict:
    return {"events": events}


def _fresh_app(data_dir: str, monkeypatch):
    monkeypatch.setenv("RACETAG_DATA_DIR", data_dir)
    import app as app_module
    importlib.reload(app_module)
    return app_module


# ---------------------------------------------------------------------------
# test_diagnostics_counts_recent_reads
# ---------------------------------------------------------------------------

def test_diagnostics_counts_recent_reads(tmp_path, monkeypatch):
    """Post 3 events on antenna 1 and 2 on antenna 2; diagnostics should
    reflect those counts within the default window."""
    monkeypatch.setenv("RACE_MIN_PASS_INTERVAL_S", "0")
    app_module = _fresh_app(str(tmp_path), monkeypatch)

    # Use timestamps very close to now so they fall within the window.
    now = datetime.now(timezone.utc)

    def _ts(offset_s: int) -> str:
        t = now - timedelta(seconds=offset_s)
        return t.isoformat(timespec="milliseconds").replace("+00:00", "Z")

    with TestClient(app_module.app) as client:
        # 3 reads on antenna 1 — must use distinct tags (or different timestamps
        # far enough apart to pass cooldown, but min_interval=0 handles it).
        for i in range(3):
            resp = client.post(
                "/events/tag/batch",
                json=_batch([_tag_event(f"T1-{i}", _ts(50 - i), antenna=1)]),
            )
            assert resp.status_code == 200, resp.text

        # 2 reads on antenna 2.
        for i in range(2):
            resp = client.post(
                "/events/tag/batch",
                json=_batch([_tag_event(f"T2-{i}", _ts(40 - i), antenna=2)]),
            )
            assert resp.status_code == 200, resp.text

        diag = client.get("/diagnostics/antennas?window_s=60")
        assert diag.status_code == 200, diag.text
        counts = diag.json()["counts"]

        # Keys may be int or string depending on JSON serialisation.
        def _get(counts: dict, key: int) -> int:
            return counts.get(key) or counts.get(str(key)) or 0

        assert _get(counts, 1) == 3, f"Expected 3 on antenna 1, got: {counts}"
        assert _get(counts, 2) == 2, f"Expected 2 on antenna 2, got: {counts}"


# ---------------------------------------------------------------------------
# test_diagnostics_window_filters_old
# ---------------------------------------------------------------------------

def test_diagnostics_window_filters_old(tmp_path, monkeypatch):
    """Events with timestamps older than window_s must not appear in counts."""
    app_module = _fresh_app(str(tmp_path), monkeypatch)

    from models_api import EventType, TagEventDTO

    # Insert an old event directly into storage (bypasses cooldown + SSE).
    old_ts = (
        datetime.now(timezone.utc) - timedelta(seconds=120)
    ).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    old_ev = TagEventDTO(
        source="test",
        reader_ip="127.0.0.1",
        timestamp=old_ts,
        event_type=EventType.arrive,
        tag_id="OLD-TAG",
        antenna=3,
    )
    app_module.storage.append_event(old_ev)

    with TestClient(app_module.app) as client:
        # window_s=10 — the 120-second-old event must be excluded.
        diag = client.get("/diagnostics/antennas?window_s=10")
        assert diag.status_code == 200, diag.text
        counts = diag.json()["counts"]

        # Antenna 3 must not appear (or must be 0).
        ant3 = counts.get(3) or counts.get("3") or 0
        assert ant3 == 0, f"Old event should be filtered out, got counts: {counts}"
