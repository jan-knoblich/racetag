"""Connection thread (plan B1/B4/C3, contract §3.2/§3.4) against a fake Sirit.

All timings are shrunk on the client instance (back-off, timeouts, probe
intervals), so every scenario completes in well under a second; waits are
bounded polls, and every client and server is stopped in ``finally``.
"""
from __future__ import annotations

import socket
import threading
from typing import Dict, List
from unittest.mock import MagicMock

import pytest

from fake_sirit import FakeSiritServer, PausableProxy, reserve_port, wait_until


ARRIVE = "event.tag.arrive tag_id=0xE2000001, first=2026-09-13T10:00:00.000, antenna=1"


def make_client(control_port: int = 0, event_port: int = 0, ip="127.0.0.1", **kwargs):
    from sirit_client import SiritClient

    options = dict(
        ip=ip,
        control_port=control_port,
        event_port=event_port,
        init_commands_path=None,
        colorize=False,
        raw=False,
        interactive=False,
        backend_transport="mock",
        min_lap_interval_s=0.0,
        discovery_enabled=False,
    )
    options.update(kwargs)
    client = SiritClient(**options)
    client._backoff_s = (0.05,)
    client._connect_timeout_s = 0.5
    client._io_timeout_s = 0.2
    client._send_delay_s = 0.0
    client._antenna_probe_delay_s = 0.0
    client._session_id_timeout_s = 2.0
    client._configure_timeout_s = 3.0
    client._probe_interval_s = 60.0
    client._join_timeout_s = 1.0
    return client


def routed_connect(routes: Dict[str, FakeSiritServer]):
    """Map reader IPs to fake servers on 127.0.0.1, so tests can switch
    between "different readers" without extra loopback addresses."""
    from utils import ConnectError, connect_socket

    def _connect(ip, port, name, timeout_s):
        server = routes.get(ip)
        if server is None:
            raise ConnectError(f"{name} connect timeout")
        real_port = server.control_port if name == "CONTROL" else server.event_port
        return connect_socket("127.0.0.1", real_port, name, timeout_s)

    return _connect


def record_updates(client) -> List[dict]:
    """Record every status update (in order) the client makes."""
    updates: List[dict] = []
    original = client._status.update

    def _update(**fields):
        updates.append(dict(fields))
        original(**fields)

    client._status.update = _update
    return updates


def states_of(updates: List[dict]) -> List[str]:
    out: List[str] = []
    for u in updates:
        if "state" in u and (not out or out[-1] != u["state"]):
            out.append(u["state"])
    return out


def is_active(client, **expected) -> bool:
    snap = client.status_snapshot()
    return snap["state"] == "active" and all(snap[k] == v for k, v in expected.items())


# ---------------------------------------------------------------------------
# Startup and reconnect
# ---------------------------------------------------------------------------

def test_unreachable_at_start_then_server_appears():
    control_port, event_port = reserve_port(), reserve_port()
    client = make_client(control_port, event_port)
    server = FakeSiritServer(control_port=control_port, event_port=event_port)
    try:
        client.start()  # must not raise although nothing listens
        assert wait_until(lambda: client.status_snapshot()["consecutive_failures"] >= 2, 3)
        snap = client.status_snapshot()
        assert snap["state"] == "connecting"
        assert snap["error"] == "CONTROL connection refused"

        server.start()
        assert wait_until(lambda: is_active(client), 5)
        snap = client.status_snapshot()
        assert snap["serial"] == "DEADBEEF01"
        assert snap["antennas"] == [1, 2]
        assert snap["consecutive_failures"] == 0
        assert snap["error"] is None
        assert snap["connected_since"] is not None
        assert server.count("reader.events.bind(id = 1)") == 1
    finally:
        client.stop()
        server.stop()


def test_connection_drop_goes_lost_then_rebinds_with_fresh_presence():
    server = FakeSiritServer().start()
    client = make_client(server.control_port, server.event_port)
    updates = record_updates(client)
    try:
        client.start()
        assert wait_until(lambda: is_active(client), 5)
        server.send_event(ARRIVE)
        assert wait_until(lambda: "E2000001" in client.tags.present, 3)
        assert client.status_snapshot()["antenna_reads"] == {"1": 1}
        assert len(client._backend.collected()) == 1

        server.drop_clients()  # reader reboots: both sockets close
        assert wait_until(lambda: server.event_accepts == 2 and is_active(client), 5)

        states = states_of(updates)
        first_active = states.index("active")
        assert "lost" in states[first_active:], states
        assert states[-1] == "active"
        assert client.session.id == 2
        assert server.count("reader.events.bind(id = 2)") == 1
        assert client.tags.present == {}, "presence must not survive a reconnect"
        assert "E2000001" in client.tags.seen, "seen history is kept"
        assert client.status_snapshot()["antenna_reads"] == {}

        # The tag that was in the field during the drop counts again.
        server.send_event(ARRIVE)
        assert wait_until(lambda: len(client._backend.collected()) == 2, 3)
    finally:
        client.stop()
        server.stop()


def test_missing_connection_id_is_a_configuration_failure():
    """A peer that accepts but never sends event.connection id must not
    leave the client stuck in configuring."""
    listener_control = socket.socket()
    listener_event = socket.socket()
    for s in (listener_control, listener_event):
        s.bind(("127.0.0.1", 0))
        s.listen(8)
    client = make_client(listener_control.getsockname()[1], listener_event.getsockname()[1])
    client._session_id_timeout_s = 0.2
    updates = record_updates(client)
    try:
        client.start()
        assert wait_until(lambda: client.status_snapshot()["consecutive_failures"] >= 1, 3)
        assert "configuring" in states_of(updates)
        errors = [u["error"] for u in updates if u.get("error")]
        assert any("event.connection id" in e for e in errors), errors
    finally:
        client.stop()
        listener_control.close()
        listener_event.close()


def test_stop_is_idempotent_and_reports_stopped():
    server = FakeSiritServer().start()
    client = make_client(server.control_port, server.event_port)
    try:
        client.start()
        assert wait_until(lambda: is_active(client), 5)
        client.stop()
        client.stop()
        assert client.status_snapshot()["state"] == "stopped"
        assert not client._conn_thread.is_alive()
        assert client.control_sock is None and client.event_sock is None
        assert wait_until(lambda: server.count("setup.operating_mode=standby") == 1, 2)
    finally:
        client.stop()
        server.stop()


def test_request_stop_puts_reader_into_standby():
    """The desktop close (stdin EOF) and SIGTERM only call request_stop();
    the reader must still be switched to standby before the sockets close."""
    server = FakeSiritServer().start()
    client = make_client(server.control_port, server.event_port)
    timer = threading.Timer(0.05, client.request_stop)
    try:
        client.start()
        assert wait_until(lambda: is_active(client), 5)
        timer.start()
        client.run_forever()  # returns within one 0.5 s tick
        assert server.count("setup.operating_mode=standby") == 1 or wait_until(
            lambda: server.count("setup.operating_mode=standby") == 1, 2
        )
        assert server.commands[-1] == "setup.operating_mode=standby"
        assert client.status_snapshot()["state"] == "stopped"
        assert client.control_sock is None
    finally:
        timer.cancel()
        client.stop()
        server.stop()


# ---------------------------------------------------------------------------
# Event channel robustness
# ---------------------------------------------------------------------------

def test_unparseable_event_lines_never_stop_lap_counting():
    server = FakeSiritServer().start()
    client = make_client(server.control_port, server.event_port)
    original = client._handle_message

    def flaky_handler(name, msg):
        if "BOOM" in msg:
            raise RuntimeError("handler bug")
        return original(name, msg)

    client._handle_message = flaky_handler
    try:
        client.start()
        assert wait_until(lambda: is_active(client), 5)
        server.send_event("event.tag.arrive tag_id=0xE2000009, antenna=1, rssi=-61.5")
        server.send_event("event.tag.arrive tag_id=0xE2000008, antenna=, rssi=abc")
        server.send_event("event.warning.BOOM")
        server.send_event(ARRIVE)
        assert wait_until(lambda: len(client._backend.collected()) == 3, 3)
        events = {e.tag_id: e for e in client._backend.collected()}
        assert (events["E2000009"].antenna, events["E2000009"].rssi) == (1, -61)
        assert (events["E2000008"].antenna, events["E2000008"].rssi) == (None, None)
        assert "E2000001" in events
        assert is_active(client) and server.event_accepts == 1
    finally:
        client.stop()
        server.stop()


def test_unexpected_receive_failure_marks_connection_lost():
    from sirit_client import _Connection

    class _BrokenSocket:
        def recv(self, size):
            raise RuntimeError("driver bug")

        def shutdown(self, how):
            pass

        def close(self):
            pass

    client = make_client()
    sock = _BrokenSocket()
    client._conn = _Connection(generation=client._generation, ip="127.0.0.1", control=sock, event=sock)
    client._recv_loop("EVENT", sock, client._generation)
    assert client._conn.lost.is_set()
    assert client._conn.reason.startswith("EVENT receive error")


def test_parse_int_field_is_lenient():
    from sirit_client import SiritClient

    assert SiritClient._parse_int_field("-61") == -61
    assert SiritClient._parse_int_field("-61.5") == -61
    for bad in (None, "", "abc", "nan", "inf"):
        assert SiritClient._parse_int_field(bad) is None


# ---------------------------------------------------------------------------
# Heartbeat reply handling (contract §3.4)
# ---------------------------------------------------------------------------

def test_config_reader_ip_change_reconnects_to_new_target():
    server_a = FakeSiritServer(serial="AAAAAAAA01").start()
    server_b = FakeSiritServer(serial="BBBBBBBB02").start()
    client = make_client(ip="10.0.0.1")
    client._connect = routed_connect({"10.0.0.1": server_a, "10.0.0.2": server_b})
    try:
        client.start()
        assert wait_until(lambda: is_active(client, serial="AAAAAAAA01"), 5)

        client._on_status_reply({"config": {"reader_ip": "10.0.0.2", "antenna_power": 300}, "command": None})

        assert wait_until(lambda: is_active(client, serial="BBBBBBBB02", ip="10.0.0.2"), 5)
        assert client.status_snapshot()["target_source"] == "config"
        assert server_b.count("reader.events.bind") == 1
    finally:
        client.stop()
        server_a.stop()
        server_b.stop()


def test_config_antenna_power_change_reconnects_with_new_power():
    server = FakeSiritServer().start()
    client = make_client(server.control_port, server.event_port, antenna_power=300)
    try:
        client.start()
        assert wait_until(lambda: is_active(client), 5)
        assert server.count("antennas.1.conducted_power=300") >= 1

        client._on_status_reply({"config": {"reader_ip": "127.0.0.1", "antenna_power": 250}, "command": None})

        assert wait_until(lambda: server.event_accepts == 2 and is_active(client, antenna_power=250), 5)
        assert server.count("antennas.1.conducted_power=250") >= 1
    finally:
        client.stop()
        server.stop()


def test_reply_with_null_or_unchanged_values_queues_nothing():
    client = make_client(ip="10.0.0.1", antenna_power=300)
    client._on_status_reply({"config": {"reader_ip": None, "antenna_power": None}, "command": None})
    client._on_status_reply({"config": {"reader_ip": "10.0.0.1", "antenna_power": 300}, "command": None})
    client._on_status_reply({"config": {"reader_ip": "not-an-ip", "antenna_power": True}, "command": {"id": 1, "type": "bogus"}})
    assert client._pending_reader_ip is None
    assert client._pending_antenna_power is None
    assert client._pending_commands == []
    assert client._handle_requests() is False


def test_reconnect_command_opens_a_fresh_connection():
    server = FakeSiritServer().start()
    client = make_client(server.control_port, server.event_port)
    try:
        client.start()
        assert wait_until(lambda: is_active(client), 5)
        client._on_status_reply({"config": None, "command": {"id": 3, "type": "reconnect"}})
        assert wait_until(lambda: server.event_accepts == 2 and is_active(client), 5)
        assert server.count("reader.events.bind(id = 2)") == 1
    finally:
        client.stop()
        server.stop()


def test_discover_command_while_active_reports_connected_candidate():
    from discovery import Candidate

    server = FakeSiritServer().start()
    client = make_client(server.control_port, server.event_port)
    calls = []

    def fake_discover(**kwargs):
        calls.append(kwargs)
        return [Candidate("192.168.178.40", "CAFEBABE00", "sweep")]

    client._discover = fake_discover
    try:
        client.start()
        assert wait_until(lambda: is_active(client, serial="DEADBEEF01"), 5)
        client._on_status_reply({"config": None, "command": {"id": 7, "type": "discover"}})
        assert wait_until(lambda: client.status_snapshot()["discovery"] is not None, 3)

        expected = [
            {"ip": "127.0.0.1", "serial": "DEADBEEF01", "source": "connected"},
            {"ip": "192.168.178.40", "serial": "CAFEBABE00", "source": "sweep"},
        ]
        snap = client.status_snapshot()
        assert snap["discovery"] == {"request_id": 7, "candidates": expected, "error": None}
        assert snap["candidates"] == expected
        assert calls[0]["skip_ips"] == ["127.0.0.1"]
        # Still on the connected reader: a manual discovery never switches.
        assert snap["ip"] == "127.0.0.1" and snap["state"] == "active"
        assert server.event_accepts == 1
    finally:
        client.stop()
        server.stop()


# ---------------------------------------------------------------------------
# Discovery-driven target selection
# ---------------------------------------------------------------------------

class TestSelectTarget:
    def _candidates(self, *ips):
        from discovery import Candidate
        return [Candidate(ip, None, "sweep") for ip in ips]

    def test_single_candidate_switches_target(self):
        client = make_client(ip=None)
        assert client._select_target(self._candidates("192.168.178.22")) is True
        snap = client.status_snapshot()
        assert client.ip == "192.168.178.22"
        assert snap["target_source"] == "discovery"
        assert snap["discovered_ip"] == "192.168.178.22"
        assert snap["error"] is None

    def test_multiple_candidates_keep_target(self):
        client = make_client(ip="10.0.0.1")
        assert client._select_target(self._candidates("192.168.178.22", "192.168.178.23")) is False
        assert client.ip == "10.0.0.1"
        assert client.status_snapshot()["error"] == "multiple readers found"
        assert client.status_snapshot()["discovered_ip"] is None

    def test_zero_candidates_keep_target(self):
        client = make_client(ip=None)
        assert client._select_target([]) is False
        assert client.ip is None
        assert client.status_snapshot()["error"] == "no reader found"

    def test_current_target_among_candidates_is_kept(self):
        client = make_client(ip="10.0.0.1")
        assert client._select_target(self._candidates("10.0.0.1", "10.0.0.2")) is False
        assert client.ip == "10.0.0.1"


def test_no_ip_discovers_single_reader_and_clears_discovered_ip_on_echo():
    from discovery import Candidate

    server = FakeSiritServer().start()
    client = make_client(ip=None, discovery_enabled=True, discover_interval_s=60.0)
    client._connect = routed_connect({"10.0.0.5": server})
    client._discover = lambda **kwargs: [Candidate("10.0.0.5", "DEADBEEF01", "arp")]
    try:
        client.start()
        assert wait_until(lambda: is_active(client, ip="10.0.0.5"), 5)
        snap = client.status_snapshot()
        assert snap["target_source"] == "discovery"
        assert snap["discovered_ip"] == "10.0.0.5"

        # A reply to a POST sent before the switch still carries the old IP.
        client._status._last_sent = {"discovered_ip": None}
        client._on_status_reply({"config": {"reader_ip": "10.9.9.9", "antenna_power": 300}, "command": None})
        assert client._pending_reader_ip is None

        # The backend persisted the discovered IP and echoes it.
        client._status._last_sent = client.status_snapshot()
        client._on_status_reply({"config": {"reader_ip": "10.0.0.5", "antenna_power": 300}, "command": None})
        assert wait_until(lambda: client.status_snapshot()["discovered_ip"] is None, 3)
        assert client.ip == "10.0.0.5" and server.event_accepts == 1
    finally:
        client.stop()
        server.stop()


def test_discover_command_without_target_adopts_single_reader():
    from discovery import Candidate

    server = FakeSiritServer().start()
    client = make_client(ip=None, discovery_enabled=False)
    client._connect = routed_connect({"10.0.0.7": server})
    client._discover = lambda **kwargs: [Candidate("10.0.0.7", "DEADBEEF01", "sweep")]
    try:
        client.start()
        assert wait_until(lambda: client.status_snapshot()["error"] == "no reader ip configured", 3)
        client._on_status_reply({"config": {"reader_ip": None, "antenna_power": None}, "command": {"id": 9, "type": "discover"}})
        assert wait_until(lambda: is_active(client, ip="10.0.0.7", target_source="discovery"), 5)
        assert client.status_snapshot()["discovery"]["request_id"] == 9
    finally:
        client.stop()
        server.stop()


def test_discover_command_with_unreachable_target_does_not_switch():
    from discovery import Candidate

    client = make_client(ip="10.0.0.1")
    client._connect = routed_connect({})  # configured reader unreachable
    client._discover = lambda **kwargs: [Candidate("10.0.0.7", None, "sweep")]
    try:
        client.start()
        assert wait_until(lambda: client.status_snapshot()["consecutive_failures"] >= 1, 3)
        client._on_status_reply({"config": None, "command": {"id": 2, "type": "discover"}})
        assert wait_until(lambda: client.status_snapshot()["discovery"] is not None, 3)
        snap = client.status_snapshot()
        assert snap["ip"] == "10.0.0.1" and snap["discovered_ip"] is None
        assert snap["candidates"] == [{"ip": "10.0.0.7", "serial": None, "source": "sweep"}]
    finally:
        client.stop()


def test_discover_command_during_automatic_run_reuses_its_result():
    """'Reader suchen' while the startup sweep runs must not sweep twice:
    the second run could push the answer past the backend's 15 s wait."""
    from discovery import Candidate

    client = make_client(ip=None, discovery_enabled=True, discover_interval_s=60.0)
    sweeping, release = threading.Event(), threading.Event()
    calls = []

    def slow_discover(**kwargs):
        calls.append(kwargs)
        sweeping.set()
        release.wait(2)
        return [Candidate("10.0.0.5", None, "sweep"), Candidate("10.0.0.6", None, "sweep")]

    client._discover = slow_discover
    try:
        client.start()
        assert sweeping.wait(2)
        client._on_status_reply({"config": {"reader_ip": None, "antenna_power": None}, "command": {"id": 5, "type": "discover"}})
        release.set()
        assert wait_until(lambda: (client.status_snapshot()["discovery"] or {}).get("request_id") == 5, 2)
        snap = client.status_snapshot()
        assert [c["ip"] for c in snap["discovery"]["candidates"]] == ["10.0.0.5", "10.0.0.6"]
        assert not wait_until(lambda: len(calls) > 1, 0.2), "no second sweep"
        assert client._pending_commands == []
    finally:
        release.set()
        client.stop()


def test_repeated_discover_commands_share_one_sweep():
    from discovery import Candidate

    client = make_client(ip="10.0.0.1")
    calls = []
    client._discover = lambda **kwargs: calls.append(kwargs) or [Candidate("10.0.0.7", None, "sweep")]
    client._on_status_reply({"config": None, "command": {"id": 1, "type": "discover"}})
    client._on_status_reply({"config": None, "command": {"id": 2, "type": "discover"}})
    client._handle_requests()
    assert len(calls) == 1
    assert client.status_snapshot()["discovery"]["request_id"] == 2


def test_no_ip_multiple_readers_stays_searching():
    from discovery import Candidate

    client = make_client(ip=None, discovery_enabled=True, discover_interval_s=60.0)
    calls = []

    def fake_discover(**kwargs):
        calls.append(kwargs)
        return [Candidate("10.0.0.5", None, "sweep"), Candidate("10.0.0.6", None, "sweep")]

    client._discover = fake_discover
    try:
        client.start()
        assert wait_until(lambda: client.status_snapshot()["error"] == "multiple readers found", 3)
        snap = client.status_snapshot()
        assert snap["state"] == "searching"
        assert snap["ip"] is None
        assert len(snap["candidates"]) == 2
        assert snap["next_retry_s"] is not None and snap["next_retry_s"] > 50
        assert len(calls) == 1, "discovery must wait discover_interval_s between runs"
    finally:
        client.stop()


def test_repeated_failures_trigger_discovery_and_switch():
    from discovery import Candidate

    server = FakeSiritServer().start()
    client = make_client(ip="10.0.0.1", discovery_enabled=True, discover_after_failures=3, discover_interval_s=60.0)
    client._connect = routed_connect({"10.0.0.2": server})  # 10.0.0.1 is gone
    calls = []

    def fake_discover(**kwargs):
        calls.append((client.status_snapshot()["consecutive_failures"], kwargs))
        return [Candidate("10.0.0.2", "DEADBEEF01", "sweep")]

    client._discover = fake_discover
    try:
        client.start()
        assert wait_until(lambda: is_active(client, ip="10.0.0.2"), 5)
        assert calls[0][0] == 3
        assert calls[0][1]["skip_ips"] == []
        snap = client.status_snapshot()
        assert snap["target_source"] == "discovery"
        assert snap["discovered_ip"] == "10.0.0.2"
    finally:
        client.stop()
        server.stop()


def test_discovery_seeing_the_target_reconnects_without_backoff():
    """The reader is back at its configured IP: no back-off idle between
    the sweep that saw it and the next connect attempt."""
    from discovery import Candidate

    server = FakeSiritServer().start()
    routes: Dict[str, FakeSiritServer] = {}
    client = make_client(ip="10.0.0.1", discovery_enabled=True, discover_after_failures=1, discover_interval_s=60.0)
    client._backoff_s = (30.0,)
    client._connect = routed_connect(routes)
    calls = []

    def fake_discover(**kwargs):
        calls.append(kwargs)
        routes["10.0.0.1"] = server  # the reader finished rebooting
        return [Candidate("10.0.0.1", "DEADBEEF01", "sweep")]

    client._discover = fake_discover
    try:
        client.start()
        assert wait_until(lambda: is_active(client, ip="10.0.0.1"), 2)
        assert len(calls) == 1
        snap = client.status_snapshot()
        assert snap["target_source"] == "cli" and snap["discovered_ip"] is None
    finally:
        client.stop()
        server.stop()


def test_default_reconnect_backoff_is_short():
    from sirit_client import DEFAULT_BACKOFF_S

    assert DEFAULT_BACKOFF_S[0] <= 1.0
    assert max(DEFAULT_BACKOFF_S) <= 2.0


# ---------------------------------------------------------------------------
# Liveness probe and clock resync
# ---------------------------------------------------------------------------

def test_unanswered_liveness_probe_marks_connection_lost():
    server = FakeSiritServer(answer_time_probe=False).start()
    client = make_client(server.control_port, server.event_port)
    client._probe_interval_s = 0.1
    client._probe_timeout_s = 0.2
    updates = record_updates(client)
    try:
        client.start()
        assert wait_until(lambda: "lost" in states_of(updates), 5)
        errors = [u["error"] for u in updates if u.get("error")]
        assert any("liveness probe" in e for e in errors), errors
        assert server.commands.count("info.time") >= client._probe_max_misses
    finally:
        client.stop()
        server.stop()


def test_short_link_outage_keeps_connection_and_delivers_buffered_passes():
    """A cable wobble: probes go unanswered for a moment while the reader
    keeps emitting passes. Fewer than _probe_max_misses silent probes must
    not tear the sockets down; TCP then delivers the passes."""
    server = FakeSiritServer().start()
    control_proxy = PausableProxy(server.control_port)
    event_proxy = PausableProxy(server.event_port)
    client = make_client(control_proxy.port, event_proxy.port)
    client._probe_interval_s = 0.05
    client._probe_timeout_s = 0.15
    client._probe_max_misses = 4
    results: List[str] = []
    original_probe = client._liveness_probe

    def recording_probe():
        result = original_probe()
        results.append(result)
        return result

    client._liveness_probe = recording_probe
    try:
        client.start()
        assert wait_until(lambda: is_active(client), 5)
        assert wait_until(lambda: "ok" in results, 2)
        control_proxy.paused.set()
        event_proxy.paused.set()
        server.send_event("event.tag.arrive tag_id=0xE2000011, first=2026-09-13T10:00:01.000, antenna=1")
        server.send_event("event.tag.arrive tag_id=0xE2000012, first=2026-09-13T10:00:02.000, antenna=2")
        assert wait_until(lambda: "missed" in results, 2)
        control_proxy.paused.clear()
        event_proxy.paused.clear()

        assert wait_until(lambda: len(client._backend.collected()) == 2, 3)
        assert wait_until(lambda: results[-1] == "ok", 2)
        assert "failed" not in results
        assert is_active(client) and server.event_accepts == 1
        assert client._probe_misses == 0
    finally:
        client.stop()
        control_proxy.stop()
        event_proxy.stop()
        server.stop()


def test_answered_liveness_probe_keeps_connection():
    server = FakeSiritServer().start()
    client = make_client(server.control_port, server.event_port)
    client._probe_interval_s = 0.05
    try:
        client.start()
        assert wait_until(lambda: is_active(client), 5)
        assert wait_until(lambda: server.commands.count("info.time") >= 3, 3)
        assert is_active(client) and server.event_accepts == 1
    finally:
        client.stop()
        server.stop()


def test_clock_is_resynced_periodically_while_active():
    server = FakeSiritServer(answer_all=True).start()
    client = make_client(server.control_port, server.event_port, clock_resync_interval_s=0.1)
    try:
        client.start()
        assert wait_until(lambda: is_active(client), 5)
        assert wait_until(lambda: server.count("info.time=") >= 3, 3)
        commands = server.commands
        # Every push sets the zone first (see the bind comment on ordering).
        for i, command in enumerate(commands):
            if command.startswith("info.time="):
                assert commands[i - 1] == "info.time_zone=UTC"
        assert is_active(client) and server.event_accepts == 1
    finally:
        client.stop()
        server.stop()


# ---------------------------------------------------------------------------
# Unit-level pieces
# ---------------------------------------------------------------------------

def test_send_control_raises_without_socket():
    from sirit_client import ControlSendError

    client = make_client()
    with pytest.raises(ControlSendError):
        client._send_control(["info.time"])
    client._send_control([])  # nothing to send is not an error


def test_failed_send_marks_connection_lost_and_blocks_bind():
    from sirit_client import ControlSendError, _Connection

    client = make_client()
    dead = MagicMock()
    dead.sendall.side_effect = OSError("broken pipe")
    client.control_sock = dead
    client._conn = _Connection(generation=client._generation, ip="127.0.0.1", control=dead, event=dead)

    with pytest.raises(ControlSendError):
        client._send_control(["info.time"])
    assert client._conn.lost.is_set()
    assert "CONTROL send failed" in client._conn.reason

    client._conn.lost.clear()
    client._conn.reason = None
    client.session.id = 4
    client._maybe_bind_and_config()
    assert client.session.bound is False, "a dead socket must never be marked bound"
    assert client._conn.lost.is_set()
    assert "CONTROL send failed" in client._conn.reason


def test_antenna_reads_count_raw_arrives_before_gating():
    client = make_client()
    from backend_client.mock import MockBackendClient
    client._backend = MockBackendClient()

    client._handle_message("EVENT", ARRIVE)
    client._handle_message("EVENT", ARRIVE.replace("antenna=1", "antenna=2"))  # same pass, gated
    client._handle_message("EVENT", "event.tag.arrive tag_id=0xE2000002, antenna=1")
    snap = client.status_snapshot()
    assert snap["antenna_reads"] == {"1": 2, "2": 1}
    assert len(client._backend.collected()) == 2
    assert snap["last_event_at"] is not None

    client._status.update(last_event_at=None)
    client._handle_message("EVENT", "event.tag.depart tag_id=0xE2000002, antenna=1")
    snap = client.status_snapshot()
    assert snap["last_event_at"] is not None
    assert snap["antenna_reads"] == {"1": 2, "2": 1}


def test_status_snapshot_has_contract_shape_before_start():
    client = make_client(ip="192.168.178.22", antenna_power=250)
    snap = client.status_snapshot()
    assert snap["state"] == "connecting"
    assert snap["ip"] == "192.168.178.22"
    assert snap["target_source"] == "cli"
    assert snap["antenna_power"] == 250
    assert set(snap) == {
        "state", "ip", "target_source", "serial", "antennas", "antenna_power",
        "antenna_reads", "last_event_at", "connected_since", "error",
        "consecutive_failures", "next_retry_s", "candidates", "discovered_ip",
        "discovery", "reader_service_version", "pid",
    }
    assert make_client(ip=None).status_snapshot()["state"] == "searching"
