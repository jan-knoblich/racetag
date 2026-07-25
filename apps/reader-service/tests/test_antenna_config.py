"""Tests for auto-antenna configuration (mux_sequence + power) in SiritClient.

The reader-service detects connected antenna ports via the guest-readable
antennas.detected variable and configures mux_sequence + per-port power on
every connect, with a fallback to configured ports when detection is empty.
"""
from __future__ import annotations

import threading
from unittest.mock import MagicMock


def _make_client(power=300, fallback="1 2"):
    from sirit_client import SiritClient
    from backend_client.mock import MockBackendClient
    client = SiritClient(
        ip="127.0.0.1", control_port=50007, event_port=50008,
        init_commands_path=None, colorize=False, raw=False, interactive=False,
        backend_transport="mock", antenna_power=power, antenna_ports_fallback=fallback,
    )
    client._backend = MockBackendClient()
    client.control_sock = None
    return client


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

class TestParsing:
    def test_parse_detected_ports_ok_list(self):
        from sirit_client import SiritClient
        assert SiritClient._parse_detected_ports("ok 1 2") == [1, 2]
        assert SiritClient._parse_detected_ports("ok 1 3 2") == [1, 3, 2]

    def test_parse_detected_ports_bare_ok_is_empty(self):
        from sirit_client import SiritClient
        assert SiritClient._parse_detected_ports("ok") == []

    def test_parse_detected_ports_error_is_empty(self):
        from sirit_client import SiritClient
        assert SiritClient._parse_detected_ports("error.something") == []
        assert SiritClient._parse_detected_ports(None) == []

    def test_parse_ports_str_variants(self):
        from sirit_client import SiritClient
        assert SiritClient._parse_ports_str("1 2") == [1, 2]
        assert SiritClient._parse_ports_str("1,2") == [1, 2]
        assert SiritClient._parse_ports_str(" 1 ,  2 ") == [1, 2]
        assert SiritClient._parse_ports_str("1 1 2") == [1, 2]  # dedup
        assert SiritClient._parse_ports_str("5 0 2") == [2]     # only 1..4
        assert SiritClient._parse_ports_str("") == []


# ---------------------------------------------------------------------------
# Query/response capture
# ---------------------------------------------------------------------------

class TestQueryControl:
    def test_query_captures_ok_reply(self):
        client = _make_client()
        client.control_sock = MagicMock()  # make _query_control proceed

        # Simulate the CONTROL recv thread delivering the reply shortly after send.
        def deliver():
            # Wait until the query is armed, then feed the reply.
            client._query_pending.wait(timeout=1)
            client._handle_message("CONTROL", "ok 1 2")

        t = threading.Thread(target=deliver, daemon=True)
        t.start()
        reply = client._query_control("antennas.detected", timeout=2)
        t.join(timeout=1)
        assert reply == "ok 1 2"

    def test_query_times_out_without_reply(self):
        client = _make_client()
        client.control_sock = MagicMock()
        reply = client._query_control("antennas.detected", timeout=0.2)
        assert reply is None

    def test_query_no_socket_returns_none(self):
        client = _make_client()
        client.control_sock = None
        assert client._query_control("antennas.detected", timeout=0.2) is None


# ---------------------------------------------------------------------------
# _configure_antennas end to end (mocked sends + query)
# ---------------------------------------------------------------------------

class TestConfigureAntennas:
    def _capture_sends(self, client):
        sent = []
        client._send_control = lambda cmds: sent.extend(cmds)  # type: ignore
        return sent

    def test_detected_ports_are_configured(self, monkeypatch):
        client = _make_client(power=300)
        sent = self._capture_sends(client)
        monkeypatch.setattr(client, "_query_control", lambda cmd, timeout=2.0: "ok 1 2")
        monkeypatch.setattr("sirit_client.time.sleep", lambda s: None)

        client._configure_antennas()

        # Final config: ports 1,2 powered, 3,4 off, mux=1 2
        assert "antennas.1.conducted_power=300" in sent
        assert "antennas.2.conducted_power=300" in sent
        assert "antennas.3.conducted_power=0" in sent
        assert "antennas.4.conducted_power=0" in sent
        assert "antennas.mux_sequence=1 2" in sent
        assert sent[-1] == "setup.operating_mode=active"

    def test_falls_back_when_detection_empty(self, monkeypatch):
        client = _make_client(power=250, fallback="1 2")
        sent = self._capture_sends(client)
        monkeypatch.setattr(client, "_query_control", lambda cmd, timeout=2.0: "ok")  # nothing detected
        monkeypatch.setattr("sirit_client.time.sleep", lambda s: None)

        client._configure_antennas()

        assert "antennas.mux_sequence=1 2" in sent
        assert "antennas.1.conducted_power=250" in sent
        assert "antennas.2.conducted_power=250" in sent
        assert "antennas.3.conducted_power=0" in sent

    def test_falls_back_when_query_times_out(self, monkeypatch):
        client = _make_client(power=300, fallback="1")
        sent = self._capture_sends(client)
        monkeypatch.setattr(client, "_query_control", lambda cmd, timeout=2.0: None)  # timeout
        monkeypatch.setattr("sirit_client.time.sleep", lambda s: None)

        client._configure_antennas()

        assert "antennas.mux_sequence=1" in sent
        assert "antennas.1.conducted_power=300" in sent
        assert "antennas.2.conducted_power=0" in sent

    def test_detection_exception_falls_back(self, monkeypatch):
        client = _make_client(power=300, fallback="1 2")
        sent = self._capture_sends(client)

        def boom(cmd, timeout=2.0):
            raise RuntimeError("socket died")
        monkeypatch.setattr(client, "_query_control", boom)
        monkeypatch.setattr("sirit_client.time.sleep", lambda s: None)

        client._configure_antennas()  # must not raise
        assert "antennas.mux_sequence=1 2" in sent
        assert sent[-1] == "setup.operating_mode=active"

    def test_probe_phase_lights_all_ports_before_detection(self, monkeypatch):
        client = _make_client(power=300)
        sent = self._capture_sends(client)
        order = []
        monkeypatch.setattr(client, "_query_control",
                            lambda cmd, timeout=2.0: (order.append("query"), "ok 1")[1])
        monkeypatch.setattr("sirit_client.time.sleep", lambda s: None)

        client._configure_antennas()

        # The probe mux (all 4 ports) is sent before the query,
        # the final mux (detected) after.
        assert "antennas.mux_sequence=1 2 3 4" in sent
        assert "antennas.mux_sequence=1" in sent
        assert sent.index("antennas.mux_sequence=1 2 3 4") < sent.index("antennas.mux_sequence=1")
