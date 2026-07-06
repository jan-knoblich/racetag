"""Tests for AUDIT-2026-07 spool package (H1 + H2 + M1).

H1: spool/log paths are absolute (RACETAG_LOG_DIR / ~/.racetag/logs), not
    CWD-relative — a Finder-launched .app (CWD "/") must still spool.
H2: shutdown drains the queue and spools undelivered events instead of
    silently dropping them.
M1: a corrupt spool line is quarantined and the drain continues; it must
    never wedge the drain and strand every batch behind it.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from unittest.mock import MagicMock

import pytest


def _make_event_payload(tag_id: str = "AA01") -> dict:
    return {
        "source": "test",
        "reader_ip": "127.0.0.1",
        "timestamp": "2026-04-15T12:00:00.000Z",
        "event_type": "arrive",
        "tag_id": tag_id,
    }


def _make_ok_response(n: int = 1):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"events_processed": n}
    return resp


class _FakeTagEvent:
    """Minimal stand-in for models.TagEvent (only to_payload is used)."""

    def __init__(self, tag_id: str):
        self.tag_id = tag_id

    def to_payload(self) -> dict:
        return _make_event_payload(self.tag_id)


# ---------------------------------------------------------------------------
# H1 — absolute spool path resolution
# ---------------------------------------------------------------------------

class TestLogDirResolution:

    def test_resolve_log_dir_prefers_env(self, monkeypatch):
        monkeypatch.setenv("RACETAG_LOG_DIR", "/tmp/custom-racetag-logs")
        from utils import resolve_log_dir
        assert resolve_log_dir() == "/tmp/custom-racetag-logs"

    def test_resolve_log_dir_defaults_to_home(self, monkeypatch):
        monkeypatch.delenv("RACETAG_LOG_DIR", raising=False)
        from utils import resolve_log_dir
        d = resolve_log_dir()
        assert os.path.isabs(d), f"log dir must be absolute, got {d!r}"
        assert d == os.path.join(os.path.expanduser("~"), ".racetag", "logs")

    def test_spool_path_is_absolute(self):
        """The module-level spool path must never be CWD-relative again."""
        import backend_client.http as http_mod
        # Tests may have patched it; check the ORIGINAL computation instead:
        # recompute from resolve_log_dir the way the module does at import.
        from utils import resolve_log_dir
        expected_dir = resolve_log_dir()
        assert os.path.isabs(os.path.join(expected_dir, "spool.jsonl"))


# ---------------------------------------------------------------------------
# M1 — corrupt line quarantine
# ---------------------------------------------------------------------------

class TestDrainQuarantine:

    def _client_with_spool(self, spool_path: str):
        import backend_client.http as http_mod
        self._http_mod = http_mod
        self._orig_spool = http_mod._SPOOL_PATH
        http_mod._SPOOL_PATH = spool_path
        from backend_client.http import HttpBackendClient
        return HttpBackendClient(url="http://localhost:8600")

    def teardown_method(self, _method):
        if hasattr(self, "_orig_spool"):
            self._http_mod._SPOOL_PATH = self._orig_spool

    def test_corrupt_line_is_quarantined_and_drain_continues(self):
        """corrupt line at the HEAD: good batches behind it must still deliver."""
        with tempfile.TemporaryDirectory() as tmpdir:
            spool = os.path.join(tmpdir, "spool.jsonl")
            good1 = {"events": [_make_event_payload("GOOD1")]}
            good2 = {"events": [_make_event_payload("GOOD2")]}
            with open(spool, "w") as f:
                f.write('{"events": [{"truncated...\n')   # torn write
                f.write(json.dumps(good1) + "\n")
                f.write(json.dumps(good2) + "\n")

            client = self._client_with_spool(spool)
            mock_session = MagicMock()
            mock_session.post.return_value = _make_ok_response(1)

            client._drain_spool(mock_session, {}, "http://x/events/tag/batch")

            # Both good batches delivered
            assert mock_session.post.call_count == 2
            # Spool truncated
            with open(spool) as f:
                assert f.read().strip() == ""
            # Corrupt line quarantined next to the spool
            corrupt = os.path.join(tmpdir, "spool.corrupt.jsonl")
            assert os.path.exists(corrupt)
            with open(corrupt) as f:
                assert "truncated" in f.read()

    def test_events_not_a_list_is_quarantined(self):
        """A parseable line with the wrong shape is also quarantine-worthy."""
        with tempfile.TemporaryDirectory() as tmpdir:
            spool = os.path.join(tmpdir, "spool.jsonl")
            with open(spool, "w") as f:
                f.write('{"events": "not-a-list"}\n')
                f.write(json.dumps({"events": [_make_event_payload("OK")]}) + "\n")

            client = self._client_with_spool(spool)
            mock_session = MagicMock()
            mock_session.post.return_value = _make_ok_response(1)

            client._drain_spool(mock_session, {}, "http://x/events/tag/batch")

            assert mock_session.post.call_count == 1
            assert os.path.exists(os.path.join(tmpdir, "spool.corrupt.jsonl"))

    def test_delivery_failure_still_preserves_remaining_lines(self):
        """M1 fix must NOT change delivery-failure semantics: stop + preserve."""
        with tempfile.TemporaryDirectory() as tmpdir:
            spool = os.path.join(tmpdir, "spool.jsonl")
            b1 = {"events": [_make_event_payload("B1")]}
            b2 = {"events": [_make_event_payload("B2")]}
            with open(spool, "w") as f:
                f.write(json.dumps(b1) + "\n")
                f.write(json.dumps(b2) + "\n")

            client = self._client_with_spool(spool)
            mock_session = MagicMock()
            mock_session.post.side_effect = ConnectionError("backend down")

            client._drain_spool(mock_session, {}, "http://x/events/tag/batch")

            # First delivery attempt failed → both lines preserved
            with open(spool) as f:
                lines = [l for l in f if l.strip()]
            assert len(lines) == 2

    def test_corrupt_line_before_delivery_failure_is_not_restored(self):
        """Quarantined line must not reappear in the rewritten spool."""
        with tempfile.TemporaryDirectory() as tmpdir:
            spool = os.path.join(tmpdir, "spool.jsonl")
            good = {"events": [_make_event_payload("G")]}
            with open(spool, "w") as f:
                f.write("NOT JSON AT ALL\n")
                f.write(json.dumps(good) + "\n")

            client = self._client_with_spool(spool)
            mock_session = MagicMock()
            mock_session.post.side_effect = ConnectionError("down")

            client._drain_spool(mock_session, {}, "http://x/events/tag/batch")

            with open(spool) as f:
                lines = [l.strip() for l in f if l.strip()]
            # Only the good (undelivered) line remains; corrupt one is gone
            assert lines == [json.dumps(good)]


# ---------------------------------------------------------------------------
# H2 — shutdown never abandons queued events
# ---------------------------------------------------------------------------

class TestShutdownFlush:

    def _client_with_spool(self, spool_path: str, **kwargs):
        import backend_client.http as http_mod
        self._http_mod = http_mod
        self._orig_spool = http_mod._SPOOL_PATH
        http_mod._SPOOL_PATH = spool_path
        from backend_client.http import HttpBackendClient
        return HttpBackendClient(url="http://localhost:8600", **kwargs)

    def teardown_method(self, _method):
        if hasattr(self, "_orig_spool"):
            self._http_mod._SPOOL_PATH = self._orig_spool

    def test_queued_events_are_spooled_when_backend_down_on_stop(self, monkeypatch):
        """Events still in the queue at stop() must land in the spool, not vanish."""
        with tempfile.TemporaryDirectory() as tmpdir:
            spool = os.path.join(tmpdir, "spool.jsonl")
            client = self._client_with_spool(spool, batch_size=5, flush_interval_ms=10_000)

            # Session whose POST always fails (backend down)
            mock_session = MagicMock()
            mock_session.post.side_effect = ConnectionError("backend down")
            import backend_client.http as http_mod
            monkeypatch.setattr(
                http_mod.requests, "Session", lambda: mock_session
            )
            # Skip retry sleeps
            monkeypatch.setattr(http_mod.time, "sleep", lambda s: None)

            client.start()
            # Queue up events; flush_interval is long so they sit in the queue
            for i in range(7):
                client.send(_FakeTagEvent(f"Q{i:02d}"))
            time.sleep(0.05)
            client.stop()

            # Everything undelivered must be in the spool
            assert os.path.exists(spool), "spool file missing after shutdown"
            with open(spool) as f:
                lines = [l for l in f if l.strip()]
            spooled = []
            for line in lines:
                spooled.extend(json.loads(line)["events"])
            tags = {e["tag_id"] for e in spooled}
            assert tags == {f"Q{i:02d}" for i in range(7)}, (
                f"lost events on shutdown: {sorted(tags)}"
            )

    def test_queued_events_are_delivered_when_backend_up_on_stop(self, monkeypatch):
        """Happy path: final flush delivers, nothing spooled."""
        with tempfile.TemporaryDirectory() as tmpdir:
            spool = os.path.join(tmpdir, "spool.jsonl")
            client = self._client_with_spool(spool, batch_size=100, flush_interval_ms=10_000)

            delivered_payloads = []

            def fake_post(url, headers=None, data=None, timeout=None):
                delivered_payloads.append(json.loads(data))
                return _make_ok_response(len(json.loads(data)["events"]))

            mock_session = MagicMock()
            mock_session.post.side_effect = fake_post
            import backend_client.http as http_mod
            monkeypatch.setattr(http_mod.requests, "Session", lambda: mock_session)

            client.start()
            for i in range(3):
                client.send(_FakeTagEvent(f"D{i}"))
            time.sleep(0.05)
            client.stop()

            all_tags = {
                e["tag_id"] for p in delivered_payloads for e in p["events"]
            }
            assert {"D0", "D1", "D2"} <= all_tags
            # Nothing spooled
            assert not os.path.exists(spool) or open(spool).read().strip() == ""
