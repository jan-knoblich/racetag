"""Unit tests for W-031: retry logic and JSONL spool in HttpBackendClient."""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import types
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_event(tag_id: str = "AA01") -> dict:
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


def _make_error_response(status: int = 500):
    resp = MagicMock()
    resp.status_code = status
    resp.text = "Internal Server Error"
    return resp


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestHttpBackendClientRetry:

    def _client(self, spool_path: str):
        """Return an HttpBackendClient whose spool path is overridden to a temp file."""
        import backend_client.http as http_mod
        # Patch the module-level _SPOOL_PATH constant
        self._orig_spool = http_mod._SPOOL_PATH
        http_mod._SPOOL_PATH = spool_path
        from backend_client.http import HttpBackendClient
        client = HttpBackendClient(
            url="http://localhost:8600",
            batch_size=10,
            flush_interval_ms=50,
        )
        return client

    def teardown_method(self, _method):
        import backend_client.http as http_mod
        if hasattr(self, "_orig_spool"):
            http_mod._SPOOL_PATH = self._orig_spool

    def test_retry_on_transient_failure_then_success(self):
        """First two POST calls raise ConnectionError; third succeeds → batch delivered once."""
        with tempfile.TemporaryDirectory() as tmpdir:
            spool = os.path.join(tmpdir, "spool.jsonl")
            client = self._client(spool)

            call_count = 0
            ok_resp = _make_ok_response(1)

            def fake_post(*args, **kwargs):
                nonlocal call_count
                call_count += 1
                if call_count < 3:
                    raise ConnectionError("simulated failure")
                return ok_resp

            mock_session = MagicMock()
            mock_session.post.side_effect = fake_post

            items = [_make_event()]
            # Patch sleep so the test does not actually wait
            with patch("backend_client.http.time.sleep"):
                client._flush_with_retry(mock_session, {}, "http://x/events/tag/batch", items)

            assert mock_session.post.call_count == 3
            # Spool file should be empty (success on 3rd attempt)
            assert not os.path.exists(spool) or os.path.getsize(spool) == 0

    def test_spool_after_three_failures(self):
        """All three POST attempts raise → batch written to spool file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            spool = os.path.join(tmpdir, "spool.jsonl")
            client = self._client(spool)

            mock_session = MagicMock()
            mock_session.post.side_effect = ConnectionError("always fails")

            items = [_make_event("BB02")]
            with patch("backend_client.http.time.sleep"):
                client._flush_with_retry(mock_session, {}, "http://x/events/tag/batch", items)

            assert mock_session.post.call_count == 3
            assert os.path.exists(spool)
            with open(spool) as f:
                lines = [l.strip() for l in f if l.strip()]
            assert len(lines) == 1
            obj = json.loads(lines[0])
            assert obj["events"][0]["tag_id"] == "BB02"

    def test_drain_spool_on_startup(self):
        """Prime the spool file, instantiate client, mock POST succeeds → spool becomes empty."""
        with tempfile.TemporaryDirectory() as tmpdir:
            spool = os.path.join(tmpdir, "spool.jsonl")

            # Write a pre-existing batch to spool
            batch = {"events": [_make_event("CC03")]}
            with open(spool, "w") as f:
                f.write(json.dumps(batch) + "\n")

            import backend_client.http as http_mod
            orig = http_mod._SPOOL_PATH
            http_mod._SPOOL_PATH = spool
            try:
                from backend_client.http import HttpBackendClient
                client = HttpBackendClient(url="http://localhost:8600")

                ok_resp = _make_ok_response(1)
                mock_session = MagicMock()
                mock_session.post.return_value = ok_resp

                client._drain_spool(mock_session, {}, "http://localhost:8600/events/tag/batch")

                # Spool file should be empty after successful drain
                with open(spool) as f:
                    content = f.read().strip()
                assert content == "", f"Spool not empty after drain: {content!r}"

                # POST was called once for the one batch in the spool
                assert mock_session.post.call_count == 1
            finally:
                http_mod._SPOOL_PATH = orig
