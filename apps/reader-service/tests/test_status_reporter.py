"""ReaderStatusReporter (contract §2.1 / §3.2a) with a fake requests session."""
from __future__ import annotations

import logging
import threading
from typing import Any, List, Optional

import pytest

from fake_sirit import wait_until


class _Response:
    def __init__(self, status_code: int = 200, body: Any = None):
        self.status_code = status_code
        self._body = body if body is not None else {"config": {"reader_ip": None, "antenna_power": None}, "command": None}

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class FakeSession:
    def __init__(self, responses: Optional[List[Any]] = None):
        self.calls: List[dict] = []
        self._responses = list(responses or [])
        self._lock = threading.Lock()
        self.closed = False

    def post(self, url, json=None, headers=None, timeout=None):
        with self._lock:
            self.calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
            response = self._responses.pop(0) if self._responses else _Response()
        if isinstance(response, Exception):
            raise response
        return response

    def close(self):
        self.closed = True

    def bodies(self) -> List[dict]:
        with self._lock:
            return [c["json"] for c in self.calls]


def make_reporter(session: FakeSession, interval_s: float = 30.0, on_reply=None, token: Optional[str] = "secret"):
    from status_reporter import ReaderStatusReporter
    return ReaderStatusReporter(
        url="http://backend/reader/status",
        token=token,
        interval_s=interval_s,
        on_reply=on_reply,
        session_factory=lambda: session,
    )


CONTRACT_KEYS = {
    "state", "ip", "target_source", "serial", "antennas", "antenna_power",
    "antenna_reads", "last_event_at", "connected_since", "error",
    "consecutive_failures", "next_retry_s", "candidates", "discovered_ip",
    "discovery", "reader_service_version", "pid",
}


def test_first_post_has_every_contract_key_and_headers():
    session = FakeSession()
    reporter = make_reporter(session)
    try:
        reporter.start()
        assert wait_until(lambda: len(session.calls) >= 1, 2)
        call = session.calls[0]
        assert call["url"] == "http://backend/reader/status"
        assert call["headers"]["X-API-Key"] == "secret"
        assert call["timeout"] == 1.0
        assert set(call["json"]) == CONTRACT_KEYS
        assert call["json"]["antennas"] == [] and call["json"]["antenna_reads"] == {}
    finally:
        reporter.stop()


def test_state_change_posts_immediately():
    session = FakeSession()
    reporter = make_reporter(session, interval_s=30.0)
    try:
        reporter.start()
        assert wait_until(lambda: len(session.calls) == 1, 2)
        reporter.update(ip="192.168.178.22")  # no state change: waits for the interval
        reporter.update(state="connecting", ip="192.168.178.22")
        assert wait_until(lambda: len(session.calls) >= 2, 1)
        assert session.bodies()[-1]["state"] == "connecting"
        assert session.bodies()[-1]["ip"] == "192.168.178.22"
    finally:
        reporter.stop()


def test_reply_is_handed_to_on_reply_with_last_sent_payload():
    replies = []
    reply_body = {"config": {"reader_ip": "192.168.178.22", "antenna_power": 250}, "command": {"id": 7, "type": "discover"}}
    session = FakeSession([_Response(200, reply_body)])

    def on_reply(reply):
        replies.append((reply, reporter.last_sent()["state"]))

    reporter = make_reporter(session, on_reply=on_reply)
    reporter.update(state="active")
    try:
        reporter.start()
        assert wait_until(lambda: len(replies) == 1, 2)
        assert replies[0] == (reply_body, "active")
    finally:
        reporter.stop()


def test_stop_sends_final_stopped_post_and_closes_session():
    session = FakeSession()
    reporter = make_reporter(session)
    reporter.update(state="active")
    reporter.start()
    assert wait_until(lambda: len(session.calls) == 1, 2)
    reporter.stop()
    assert session.bodies()[-1]["state"] == "stopped"
    assert reporter.snapshot()["state"] == "stopped"
    assert session.closed


def test_discovery_result_is_sent_once_and_only_cleared_after_success():
    session = FakeSession([_Response(200), ConnectionError("backend down"), _Response(200)])
    reporter = make_reporter(session, interval_s=30.0)
    result = {"request_id": 7, "candidates": [], "error": None}
    try:
        reporter.start()
        assert wait_until(lambda: len(session.calls) == 1, 2)
        reporter.update(discovery=result)  # wakes the sender
        assert wait_until(lambda: len(session.calls) == 2, 1)
        assert session.bodies()[1]["discovery"] == result
        assert reporter.snapshot()["discovery"] == result, "failed POST must not drop the result"
        reporter.update(discovery=result)
        assert wait_until(lambda: len(session.calls) == 3, 1)
        assert wait_until(lambda: reporter.snapshot()["discovery"] is None, 1)
    finally:
        reporter.stop()


def test_errors_are_logged_at_most_once_per_minute(caplog):
    session = FakeSession()
    reporter = make_reporter(session)
    clock = [1000.0]
    reporter._monotonic = lambda: clock[0]
    with caplog.at_level(logging.WARNING, logger="reader.status"):
        reporter._post(FakeSession([ConnectionError("down")]), deliver_reply=True)
        reporter._post(FakeSession([_Response(500)]), deliver_reply=True)
        clock[0] += 61
        reporter._post(FakeSession([_Response(404)]), deliver_reply=True)
    lines = [r.getMessage() for r in caplog.records if "[STATUS]" in r.getMessage()]
    assert len(lines) == 2, lines
    assert "down" in lines[0] and "404" in lines[1]


def test_reply_handler_errors_do_not_kill_the_sender(caplog):
    session = FakeSession()
    calls = []

    def on_reply(reply):
        calls.append(reply)
        raise RuntimeError("boom")

    reporter = make_reporter(session, interval_s=0.05, on_reply=on_reply)
    try:
        reporter.start()
        assert wait_until(lambda: len(calls) >= 3, 2)
    finally:
        reporter.stop()


def test_update_rejects_unknown_fields_and_states():
    reporter = make_reporter(FakeSession())
    with pytest.raises(KeyError):
        reporter.update(nonsense=1)
    with pytest.raises(ValueError):
        reporter.update(state="unknown")


def test_disabled_reporter_keeps_state_without_a_thread():
    from status_reporter import ReaderStatusReporter

    for reporter in (
        ReaderStatusReporter(url=None, token=None),
        ReaderStatusReporter(url="http://backend/reader/status", token=None, interval_s=0),
    ):
        reporter.start()
        assert reporter._thread is None
        reporter.update(state="active", antennas=[1, 2])
        assert reporter.snapshot()["antennas"] == [1, 2]
        reporter.stop()
        assert reporter.snapshot()["state"] == "stopped"


def test_snapshot_is_a_copy():
    reporter = make_reporter(FakeSession())
    reads = {"1": 1}
    reporter.update(antenna_reads=reads)
    reads["1"] = 99
    snap = reporter.snapshot()
    snap["antenna_reads"]["1"] = 42
    assert reporter.snapshot()["antenna_reads"] == {"1": 1}


def test_version_prefers_shell_environment(monkeypatch):
    from status_reporter import __version__, reader_service_version
    monkeypatch.delenv("RACETAG_VERSION", raising=False)
    assert reader_service_version() == __version__
    monkeypatch.setenv("RACETAG_VERSION", "1.2.3")
    assert reader_service_version() == "1.2.3"


def test_client_builds_heartbeat_url_only_for_http_transport():
    from sirit_client import SiritClient

    common = dict(ip=None, control_port=50007, event_port=50008, init_commands_path=None,
                  colorize=False, raw=False, interactive=False)
    http = SiritClient(backend_url="http://127.0.0.1:8600/", backend_token="t", **common)
    assert http._status.url == "http://127.0.0.1:8600/reader/status"
    assert http._status.enabled
    assert not SiritClient(backend_transport="mock", backend_url="http://x", **common)._status.enabled
    assert not SiritClient(backend_url="http://x", heartbeat_interval_s=0, **common)._status.enabled


def test_reply_command_round_trip_through_a_running_client():
    """Reporter -> backend reply with a discover command -> connection thread
    runs discovery -> the result is POSTed back exactly once."""
    from discovery import Candidate
    from fake_sirit import FakeSiritServer
    from sirit_client import SiritClient
    from status_reporter import ReaderStatusReporter

    first_reply = {"config": {"reader_ip": "127.0.0.1", "antenna_power": 300}, "command": {"id": 5, "type": "discover"}}
    session = FakeSession([_Response(200, first_reply)])
    server = FakeSiritServer().start()
    client = SiritClient(
        ip="127.0.0.1", control_port=server.control_port, event_port=server.event_port,
        init_commands_path=None, colorize=False, raw=False, interactive=False,
        backend_transport="mock", discovery_enabled=False,
    )
    client._send_delay_s = 0.0
    client._antenna_probe_delay_s = 0.0
    client._discover = lambda **kwargs: [Candidate("192.168.178.40", None, "sweep")]
    client._status = ReaderStatusReporter(
        url="http://backend/reader/status", token=None, interval_s=0.05,
        on_reply=client._on_status_reply, session_factory=lambda: session,
    )
    client._status.update(state="connecting", ip="127.0.0.1", target_source="cli", antenna_power=300)

    def discovery_posts():
        return [b["discovery"] for b in session.bodies() if b["discovery"] is not None]

    try:
        client.start()
        assert wait_until(lambda: len(discovery_posts()) == 1, 5)
        result = discovery_posts()[0]
        assert result["request_id"] == 5 and result["error"] is None
        assert result["candidates"][-1] == {"ip": "192.168.178.40", "serial": None, "source": "sweep"}
        count = len(session.calls)
        assert wait_until(lambda: len(session.calls) >= count + 3, 2)
        assert len(discovery_posts()) == 1, "a discovery result is delivered once"
        # A manual discovery lists readers; it never moves a configured target.
        assert wait_until(lambda: session.bodies()[-1]["state"] == "active", 5)
        assert session.bodies()[-1]["ip"] == "127.0.0.1"
    finally:
        client.stop()
        server.stop()
    assert session.bodies()[-1]["state"] == "stopped"
