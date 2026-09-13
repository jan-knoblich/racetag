"""Discovery module (plan C1, contract §3.3)."""
from __future__ import annotations

import ipaddress
import socket
import threading

import pytest

import discovery
from discovery import Candidate
from fake_sirit import FakeSiritServer, reserve_port


# Captured `arp -a` samples (addresses anonymised, format verbatim).
WINDOWS_ARP_EN = """
Interface: 192.168.178.20 --- 0x7
  Internet Address      Physical Address      Type
  192.168.178.1         dc-39-6f-12-34-56     dynamic
  192.168.178.22        00-17-9e-a1-b2-c3     dynamic
  192.168.178.255       ff-ff-ff-ff-ff-ff     static
  224.0.0.22            01-00-5e-00-00-16     static

Interface: 169.254.12.7 --- 0x12
  Internet Address      Physical Address      Type
  169.254.1.2           00-17-9e-a1-b2-c4     dynamic
"""

WINDOWS_ARP_DE = """
Schnittstelle: 192.168.178.20 --- 0x7
  Internetadresse       Physische Adresse     Typ
  192.168.178.1         dc-39-6f-12-34-56     dynamisch
  192.168.178.22        00-17-9E-A1-B2-C3     dynamisch
  192.168.178.255       ff-ff-ff-ff-ff-ff     statisch
"""

MACOS_ARP = """
fritz.box (192.168.178.1) at dc:39:6f:12:34:56 on en0 ifscope [ethernet]
? (192.168.178.22) at 0:17:9e:a1:b2:c3 on en0 ifscope [ethernet]
? (192.168.178.30) at (incomplete) on en0 ifscope [ethernet]
? (224.0.0.251) at 1:0:5e:0:0:fb on en0 ifscope permanent [ethernet]
"""

LINUX_IP_NEIGH = """
192.168.1.22 dev eth0 lladdr 00:17:9e:0a:0b:0c REACHABLE
192.168.1.1 dev eth0 lladdr 3c:a6:2f:00:11:22 STALE
fe80::1 dev eth0 lladdr 3c:a6:2f:00:11:22 router STALE
192.168.1.99 dev eth0 FAILED
"""

WINDOWS_IPCONFIG_EN = """
Windows IP Configuration


Ethernet adapter Ethernet:

   Connection-specific DNS Suffix  . : fritz.box
   Link-local IPv6 Address . . . . . : fe80::1c2b:3d4e:5f60:7182%7
   IPv4 Address. . . . . . . . . . . : 192.168.178.20(Preferred)
   Subnet Mask . . . . . . . . . . . : 255.255.255.0
   Default Gateway . . . . . . . . . : 192.168.178.1

Ethernet adapter Ethernet 2:

   Autoconfiguration IPv4 Address. . : 169.254.12.7(Preferred)
   Subnet Mask . . . . . . . . . . . : 255.255.0.0

Wireless LAN adapter WLAN:

   IPv4 Address. . . . . . . . . . . : 10.20.0.15
   Subnet Mask . . . . . . . . . . . : 255.255.0.0
"""

WINDOWS_IPCONFIG_DE = """
Windows-IP-Konfiguration


Ethernet-Adapter Ethernet:

   Verbindungsspezifisches DNS-Suffix: fritz.box
   Verbindungslokale IPv6-Adresse  . : fe80::1c2b:3d4e:5f60:7182%7
   IPv4-Adresse  . . . . . . . . . . : 192.168.178.20(Bevorzugt)
   Subnetzmaske  . . . . . . . . . . : 255.255.255.0
   Standardgateway . . . . . . . . . : 192.168.178.1

Ethernet-Adapter Ethernet 2:

   Autokonfigurations-IPv4-Adresse . : 169.254.12.7(Bevorzugt)
   Subnetzmaske  . . . . . . . . . . : 255.255.0.0
"""

MACOS_IFCONFIG = """
lo0: flags=8049<UP,LOOPBACK,RUNNING,MULTICAST> mtu 16384
\tinet 127.0.0.1 netmask 0xff000000
\tinet6 ::1 prefixlen 128
en0: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500
\tinet6 fe80::10a4:5b6c:7d8e:9f01%en0 prefixlen 64 secured scopeid 0xb
\tinet 192.168.178.21 netmask 0xffffff00 broadcast 192.168.178.255
bridge100: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500
\tinet 10.211.55.2 netmask 0xfffffffc broadcast 10.211.55.3
"""

LINUX_IFCONFIG_LEGACY = """
eth0      Link encap:Ethernet  HWaddr 00:1c:42:aa:bb:cc
          inet addr:192.168.1.5  Bcast:192.168.1.255  Mask:255.255.0.0
lo        Link encap:Local Loopback
          inet addr:127.0.0.1  Mask:255.0.0.0
"""

LINUX_IP_ADDR = """
1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 qdisc noqueue state UNKNOWN group default qlen 1000
    inet 127.0.0.1/8 scope host lo
2: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc fq_codel state UP group default qlen 1000
    inet 172.17.8.30/20 brd 172.17.15.255 scope global dynamic eth0
"""


# ---------------------------------------------------------------------------
# ARP parsing
# ---------------------------------------------------------------------------

class TestParseArpOutput:
    def test_windows_english(self):
        entries = discovery.parse_arp_output(WINDOWS_ARP_EN)
        assert ("192.168.178.22", "00:17:9e:a1:b2:c3") in entries
        assert ("169.254.1.2", "00:17:9e:a1:b2:c4") in entries
        assert ("192.168.178.1", "dc:39:6f:12:34:56") in entries
        assert all(ip != "192.168.178.20" for ip, _ in entries), "interface header is not an entry"

    def test_windows_german(self):
        entries = discovery.parse_arp_output(WINDOWS_ARP_DE)
        assert ("192.168.178.22", "00:17:9e:a1:b2:c3") in entries
        assert len(entries) == 3

    def test_macos_unpadded_octets_and_incomplete_entries(self):
        entries = discovery.parse_arp_output(MACOS_ARP)
        assert ("192.168.178.22", "00:17:9e:a1:b2:c3") in entries
        assert ("224.0.0.251", "01:00:5e:00:00:fb") in entries
        assert all(ip != "192.168.178.30" for ip, _ in entries)

    def test_linux_ip_neigh(self):
        entries = discovery.parse_arp_output(LINUX_IP_NEIGH)
        assert entries == [("192.168.1.22", "00:17:9e:0a:0b:0c"), ("192.168.1.1", "3c:a6:2f:00:11:22")]

    def test_garbage_yields_nothing(self):
        assert discovery.parse_arp_output("") == []
        assert discovery.parse_arp_output("no entries found\n999.1.1.1 00-17-9e-00-00-01\n") == []


# ---------------------------------------------------------------------------
# Local network enumeration
# ---------------------------------------------------------------------------

def _nets(*cidrs):
    return [ipaddress.IPv4Network(c) for c in cidrs]


class TestLocalNetworks:
    def test_windows_english_ipconfig_caps_wide_masks(self):
        networks = discovery.parse_windows_networks("", WINDOWS_IPCONFIG_EN)
        assert networks == _nets("192.168.178.0/24", "10.20.0.0/24"), "link-local skipped, /16 capped"

    def test_windows_german_ipconfig_and_arp_interfaces(self):
        networks = discovery.parse_windows_networks(WINDOWS_ARP_DE, WINDOWS_IPCONFIG_DE)
        assert set(networks) == set(_nets("192.168.178.0/24"))

    def test_windows_arp_interface_lines_alone(self):
        assert discovery.parse_windows_networks(WINDOWS_ARP_EN, "") == _nets("192.168.178.0/24")

    def test_windows_arp_header_does_not_widen_narrow_ipconfig_network(self):
        # iPhone USB/hotspot tethering (/28) next to the FritzBox Ethernet.
        ipconfig = (
            "Ethernet-Adapter Ethernet:\n"
            "   IPv4-Adresse  . . . . . . . . . . : 192.168.178.30\n"
            "   Subnetzmaske  . . . . . . . . . . : 255.255.255.0\n"
            "Ethernet-Adapter Apple Mobile Device Ethernet:\n"
            "   IPv4-Adresse  . . . . . . . . . . : 172.20.10.2\n"
            "   Subnetzmaske  . . . . . . . . . . : 255.255.255.240\n"
        )
        arp = (
            "Schnittstelle: 192.168.178.30 --- 0x7\n"
            "  Internetadresse       Physische Adresse     Typ\n"
            "  192.168.178.1         dc-39-6f-12-34-56     dynamisch\n"
            "Schnittstelle: 172.20.10.2 --- 0x12\n"
            "  Internetadresse       Physische Adresse     Typ\n"
            "  172.20.10.1           3e-22-fb-00-11-22     dynamisch\n"
            "Schnittstelle: 10.99.0.7 --- 0x15\n"
        )
        networks = discovery.parse_windows_networks(arp, ipconfig)
        assert networks == _nets("192.168.178.0/24", "172.20.10.0/28", "10.99.0.0/24"), (
            "arp headers only add addresses ipconfig did not list"
        )

    def test_macos_ifconfig_hex_masks(self):
        networks = discovery.parse_unix_networks(MACOS_IFCONFIG)
        assert networks == _nets("192.168.178.0/24", "10.211.55.0/30")

    def test_linux_legacy_ifconfig(self):
        assert discovery.parse_unix_networks(LINUX_IFCONFIG_LEGACY) == _nets("192.168.1.0/24")

    def test_linux_ip_addr(self):
        assert discovery.parse_unix_networks(LINUX_IP_ADDR) == _nets("172.17.8.0/24")

    def test_live_enumeration_never_raises(self):
        networks = discovery.local_ipv4_networks()
        assert isinstance(networks, list)
        assert all(not n.network_address.is_loopback and n.prefixlen >= 24 for n in networks)


def test_run_command_hides_console_window_on_windows(monkeypatch):
    captured = {}

    class _Completed:
        stdout = b"ok"

    def fake_run(args, **kwargs):
        captured.update(kwargs)
        return _Completed()

    monkeypatch.setattr(discovery.subprocess, "run", fake_run)
    monkeypatch.setattr(discovery.sys, "platform", "win32")
    assert discovery._run_command(["arp", "-a"]) == "ok"
    assert captured["creationflags"] == 0x08000000
    assert captured["timeout"] == discovery.COMMAND_TIMEOUT_S


@pytest.mark.parametrize(
    "platform, expected",
    [("win32", ["arp", "-a"]), ("darwin", ["arp", "-an"]), ("linux", ["arp", "-an"])],
)
def test_arp_table_skips_name_lookups_outside_windows(monkeypatch, platform, expected):
    calls = []

    def fake_run_command(args):
        calls.append(list(args))
        return "? (10.0.0.9) at 0:17:9e:1:2:3 on en0\n"

    monkeypatch.setattr(discovery, "_run_command", fake_run_command)
    monkeypatch.setattr(discovery.sys, "platform", platform)
    assert discovery._read_arp_table().startswith("? (10.0.0.9)")
    assert calls == [expected]


def test_linux_arp_falls_back_to_ip_neigh(monkeypatch):
    calls = []

    def fake_run_command(args):
        calls.append(list(args))
        return LINUX_IP_NEIGH if args[0] == "ip" else ""

    monkeypatch.setattr(discovery, "_run_command", fake_run_command)
    monkeypatch.setattr(discovery.sys, "platform", "linux")
    assert discovery._read_arp_table() == LINUX_IP_NEIGH
    assert calls == [["arp", "-an"], ["ip", "neigh"]]


def test_run_command_failure_returns_empty(monkeypatch):
    def boom(args, **kwargs):
        raise FileNotFoundError("arp")

    monkeypatch.setattr(discovery.subprocess, "run", boom)
    assert discovery._run_command(["arp", "-a"]) == ""


# ---------------------------------------------------------------------------
# Probe
# ---------------------------------------------------------------------------

class _OneShotServer:
    """Accepts one connection, sends ``reply`` (after reading a line) and closes."""

    def __init__(self, reply: bytes):
        self.reply = reply
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        self.listener.settimeout(2)
        try:
            conn, _ = self.listener.accept()
        except OSError:
            return
        with conn:
            conn.settimeout(2)
            try:
                conn.recv(1024)
                conn.sendall(self.reply)
            except OSError:
                pass

    def close(self):
        self.listener.close()
        self.thread.join(timeout=2)


class TestProbe:
    def test_fake_sirit_returns_serial(self):
        with FakeSiritServer() as server:
            assert discovery.probe("127.0.0.1", server.control_port) == "DEADBEEF01"

    def test_closed_port_returns_none(self):
        assert discovery.probe("127.0.0.1", reserve_port()) is None

    def test_non_sirit_service_returns_none(self):
        server = _OneShotServer(b"HTTP/1.1 400 Bad Request\r\n\r\n")
        try:
            assert discovery.probe("127.0.0.1", server.port) is None
        finally:
            server.close()

    def test_sirit_error_reply_without_serial_returns_empty(self):
        server = _OneShotServer(b"error.permission_denied\r\n\r\n")
        try:
            assert discovery.probe("127.0.0.1", server.port) == ""
        finally:
            server.close()

    def test_silent_peer_times_out_quickly(self):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        try:
            assert discovery.probe("127.0.0.1", listener.getsockname()[1], read_timeout_s=0.1) is None
        finally:
            listener.close()


# ---------------------------------------------------------------------------
# discover()
# ---------------------------------------------------------------------------

LOOPBACK_NET = [ipaddress.IPv4Network("127.0.0.0/30")]


class TestDiscover:
    def test_sweep_finds_fake_reader(self):
        with FakeSiritServer() as server:
            found = discovery.discover(port=server.control_port, networks=LOOPBACK_NET, include_arp=False, extra_ips=())
        assert found == [Candidate(ip="127.0.0.1", serial="DEADBEEF01", source="sweep")]

    def test_skip_ips_are_not_probed(self):
        with FakeSiritServer() as server:
            found = discovery.discover(
                port=server.control_port, networks=LOOPBACK_NET, include_arp=False,
                extra_ips=(), skip_ips=["127.0.0.1"],
            )
            assert server.control_accepts == 0
        assert found == []

    def test_arp_hit_wins_over_sweep_and_order_is_arp_sweep_linklocal(self, monkeypatch):
        probed = []

        def fake_probe(ip, port, timeout_s):
            probed.append(ip)
            return {"10.0.0.9": "AA01", "10.0.0.1": "", "169.254.1.2": "BB02"}.get(ip)

        arp = "? (10.0.0.9) at 0:17:9e:1:2:3 on en0\n? (10.0.0.2) at dc:39:6f:12:34:56 on en0\n"
        monkeypatch.setattr(discovery, "_run_command", lambda args: arp)
        monkeypatch.setattr(discovery, "probe", fake_probe)

        found = discovery.discover(networks=[ipaddress.IPv4Network("10.0.0.8/29")] + _nets("10.0.0.0/30"))

        assert found == [
            Candidate("10.0.0.9", "AA01", "arp"),
            Candidate("10.0.0.1", None, "sweep"),  # "" serial normalised to None
            Candidate("169.254.1.2", "BB02", "linklocal"),
        ]
        assert sorted(probed) == sorted(set(probed)), "each IP is probed once"
        assert "10.0.0.2" in probed  # non-Sirit OUI is only swept, not an ARP hit

    def test_never_raises(self, monkeypatch):
        def boom(*args, **kwargs):
            raise RuntimeError("unexpected")

        monkeypatch.setattr(discovery, "probe", boom)
        assert discovery.discover(networks=LOOPBACK_NET, include_arp=False, extra_ips=()) == []

    def test_candidate_to_dict(self):
        assert Candidate("1.2.3.4", None, "arp").to_dict() == {"ip": "1.2.3.4", "serial": None, "source": "arp"}


@pytest.mark.parametrize("mask,prefix", [("255.255.255.0", 24), ("0xffffff00", 24), ("0xfffffffc", 30), ("bogus", 24)])
def test_prefix_from_mask(mask, prefix):
    assert discovery._prefix_from_mask(mask) == prefix
