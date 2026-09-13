"""Sirit INfinity 510 discovery on the local network (plan C1, contract §3.3).

Standard library only: no admin rights, no raw sockets, bundles cleanly.

1. ARP table: entries with the Sirit OUI 00:17:9e (instant when the reader
   has already talked on the LAN).
2. Sweep: TCP connect to the CONTROL port on every host of each local IPv4
   /24 (wider masks are capped to the /24 around the local address).
3. Extra IPs, by default the factory address 169.254.1.2 the reader falls
   back to after a hard power loss.

Every open port is verified with ``info.serial_number``; only hosts that
answer like a Sirit are returned. Nothing in here raises: failures are
logged and yield an empty result.
"""
from __future__ import annotations

import ipaddress
import re
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from utils import get_logger

logger = get_logger("reader.discovery")

SIRIT_OUI = "00:17:9e"
FACTORY_IP = "169.254.1.2"
DEFAULT_CONTROL_PORT = 50007

COMMAND_TIMEOUT_S = 3.0
MAX_SWEEP_PREFIX = 24  # never sweep more than a /24 per interface
_REPLY_LIMIT_BYTES = 4096

_SOURCE_ORDER = {"connected": 0, "arp": 1, "sweep": 2, "linklocal": 3}


@dataclass(frozen=True)
class Candidate:
    ip: str
    serial: Optional[str]
    source: str  # "arp" | "sweep" | "linklocal" | "connected"

    def to_dict(self) -> Dict[str, Optional[str]]:
        return {"ip": self.ip, "serial": self.serial, "source": self.source}


# ---------------------------------------------------------------------------
# Command execution
# ---------------------------------------------------------------------------

def _run_command(args: Sequence[str]) -> str:
    """Run a system tool and return its stdout ("" on any failure).

    On Windows the child gets CREATE_NO_WINDOW; without it every arp/ipconfig
    call flashes a console window over the operator's app.
    """
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    try:
        completed = subprocess.run(
            list(args),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=COMMAND_TIMEOUT_S,
            check=False,
            **kwargs,
        )
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        logger.debug("[DISCOVERY] %s failed: %s", " ".join(args), exc)
        return ""
    # Localised Windows tools print in the OEM code page; every token we parse
    # (digits, dots, dashes, ASCII keywords) survives a lossy UTF-8 decode.
    return completed.stdout.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

_IPV4_RE = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d{1,3}){3})(?![\d.])")
_MAC_COLON_RE = re.compile(r"(?<![0-9A-Fa-f:])([0-9A-Fa-f]{1,2}(?::[0-9A-Fa-f]{1,2}){5})(?![0-9A-Fa-f:])")
_MAC_DASH_RE = re.compile(r"(?<![0-9A-Fa-f-])([0-9A-Fa-f]{1,2}(?:-[0-9A-Fa-f]{1,2}){5})(?![0-9A-Fa-f-])")


def _parse_ipv4(text: str) -> Optional[ipaddress.IPv4Address]:
    try:
        return ipaddress.IPv4Address(text)
    except ValueError:
        return None


def _normalise_mac(raw: str) -> str:
    return ":".join(part.zfill(2) for part in re.split(r"[:-]", raw.lower()))


def parse_arp_output(text: str) -> List[Tuple[str, str]]:
    """Parse ``arp -a`` output into ``[(ip, mac)]`` with lower-case colon MACs.

    Handles Windows (``192.168.178.22  00-17-9e-01-02-03  dynamic``, English
    and German headers), macOS (``? (192.168.178.22) at 0:17:9e:1:2:3 on en0``,
    unpadded octets) and Linux (``arp -a`` and ``ip neigh``). Incomplete
    entries and lines without both an IPv4 address and a MAC are skipped.
    """
    entries: List[Tuple[str, str]] = []
    for line in text.splitlines():
        ip_match = _IPV4_RE.search(line)
        if not ip_match or _parse_ipv4(ip_match.group(1)) is None:
            continue
        mac_match = _MAC_COLON_RE.search(line) or _MAC_DASH_RE.search(line)
        if not mac_match:
            continue
        entries.append((ip_match.group(1), _normalise_mac(mac_match.group(1))))
    return entries


def _network_for(address: ipaddress.IPv4Address, prefix: int = MAX_SWEEP_PREFIX) -> Optional[ipaddress.IPv4Network]:
    """The sweepable network around a local address, or None when the
    address is not worth sweeping (loopback, link-local, unspecified)."""
    if address.is_loopback or address.is_link_local or address.is_unspecified or address.is_multicast:
        return None
    prefix = max(prefix, MAX_SWEEP_PREFIX)
    return ipaddress.IPv4Network((address, prefix), strict=False)


def _prefix_from_mask(mask: str) -> int:
    """Prefix length from ``255.255.255.0`` or macOS ``0xffffff00``; /24 when unparseable."""
    mask = mask.strip()
    try:
        if mask.lower().startswith("0x"):
            return bin(int(mask, 16) & 0xFFFFFFFF).count("1")
        return ipaddress.IPv4Network(f"0.0.0.0/{mask}").prefixlen
    except ValueError:
        return MAX_SWEEP_PREFIX


_WIN_ARP_INTERFACE_RE = re.compile(r"^\s*(?:Interface|Schnittstelle)\s*:\s*(\d{1,3}(?:\.\d{1,3}){3})", re.IGNORECASE | re.MULTILINE)
_WIN_IPCONFIG_ADDR_RE = re.compile(r"IPv4[- ]Ad(?:dress|resse)[ .]*:\s*(\d{1,3}(?:\.\d{1,3}){3})", re.IGNORECASE)
_WIN_IPCONFIG_MASK_RE = re.compile(r"(?:Subnet Mask|Subnetzmaske)[ .]*:\s*(\d{1,3}(?:\.\d{1,3}){3})", re.IGNORECASE)


def parse_windows_networks(arp_text: str, ipconfig_text: str) -> List[ipaddress.IPv4Network]:
    """Local networks from Windows ``arp -a`` interface headers and
    ``ipconfig`` IPv4 lines (English and German)."""
    networks: List[ipaddress.IPv4Network] = []
    pending_address: Optional[ipaddress.IPv4Address] = None
    for line in ipconfig_text.splitlines():
        addr_match = _WIN_IPCONFIG_ADDR_RE.search(line)
        if addr_match:
            if pending_address is not None:
                networks.append(_network_for(pending_address))
            pending_address = _parse_ipv4(addr_match.group(1))
            continue
        mask_match = _WIN_IPCONFIG_MASK_RE.search(line)
        if mask_match and pending_address is not None:
            networks.append(_network_for(pending_address, _prefix_from_mask(mask_match.group(1))))
            pending_address = None
    if pending_address is not None:
        networks.append(_network_for(pending_address))
    for match in _WIN_ARP_INTERFACE_RE.finditer(arp_text):
        address = _parse_ipv4(match.group(1))
        # The arp header carries no mask: only a fallback for an address
        # ipconfig did not list. Otherwise a /28 tethering adapter would be
        # swept a second time as the whole /24 around it.
        if address is not None and not any(n is not None and address in n for n in networks):
            networks.append(_network_for(address))
    return [n for n in networks if n is not None]


_IFCONFIG_INET_RE = re.compile(
    r"\binet\s+(?:addr:)?(\d{1,3}(?:\.\d{1,3}){3})"
    r"(?:.*?\b(?:netmask|Mask:)\s*(0x[0-9A-Fa-f]{8}|\d{1,3}(?:\.\d{1,3}){3}))?",
    re.IGNORECASE,
)
_IP_ADDR_RE = re.compile(r"\binet\s+(\d{1,3}(?:\.\d{1,3}){3})/(\d{1,2})")


def parse_unix_networks(text: str) -> List[ipaddress.IPv4Network]:
    """Local networks from ``ifconfig`` (macOS hex masks, Linux dotted and
    legacy ``inet addr:`` forms) or ``ip -4 addr`` (CIDR) output."""
    networks: List[Optional[ipaddress.IPv4Network]] = []
    for line in text.splitlines():
        cidr = _IP_ADDR_RE.search(line)
        if cidr:
            address = _parse_ipv4(cidr.group(1))
            if address is not None:
                networks.append(_network_for(address, int(cidr.group(2))))
            continue
        inet = _IFCONFIG_INET_RE.search(line)
        if inet:
            address = _parse_ipv4(inet.group(1))
            if address is not None:
                prefix = _prefix_from_mask(inet.group(2)) if inet.group(2) else MAX_SWEEP_PREFIX
                networks.append(_network_for(address, prefix))
    return [n for n in networks if n is not None]


def _default_route_address() -> Optional[ipaddress.IPv4Address]:
    """Local address of the default route. A UDP connect() only selects a
    route; no packet is sent."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))  # TEST-NET-1, never actually contacted
        return _parse_ipv4(s.getsockname()[0])
    except OSError:
        return None
    finally:
        s.close()


def _dedupe(networks: Iterable[ipaddress.IPv4Network]) -> List[ipaddress.IPv4Network]:
    out: List[ipaddress.IPv4Network] = []
    for net in networks:
        if net not in out:
            out.append(net)
    return out


def local_ipv4_networks() -> List[ipaddress.IPv4Network]:
    """Non-loopback local IPv4 networks, each capped at /24, deduplicated."""
    try:
        if sys.platform == "win32":
            networks = parse_windows_networks(_run_command(["arp", "-a"]), _run_command(["ipconfig"]))
        else:
            text = ""
            if sys.platform.startswith("linux"):
                text = _run_command(["ip", "-4", "addr"])
            if not text:
                text = _run_command(["ifconfig"])
            networks = parse_unix_networks(text)
        default_address = _default_route_address()
        if default_address is not None:
            default_net = _network_for(default_address)
            if default_net is not None and not any(default_address in n for n in networks):
                networks.append(default_net)
        return _dedupe(networks)
    except Exception as exc:  # never raise out of discovery
        logger.warning("[DISCOVERY] could not enumerate local networks: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Probe
# ---------------------------------------------------------------------------

_SERIAL_RE = re.compile(r"^ok\s+([0-9A-Fa-f]+)\b")


def probe(ip: str, port: int = DEFAULT_CONTROL_PORT, timeout_s: float = 0.3, read_timeout_s: float = 1.0) -> Optional[str]:
    """Check whether ``ip:port`` is a Sirit CONTROL channel.

    Returns the upper-case hex serial, ``""`` when the host answered like a
    Sirit (``ok``/``error``) without a serial, and ``None`` when nothing
    answered or the answer is not Sirit-like. The connection is closed right
    away; the 510 accepts several CONTROL clients, so an active session is
    not disturbed.
    """
    received = b""
    try:
        with socket.create_connection((ip, port), timeout=timeout_s) as sock:
            sock.sendall(b"info.serial_number\r\n")
            deadline = time.monotonic() + read_timeout_s
            while b"\r\n\r\n" not in received and len(received) < _REPLY_LIMIT_BYTES:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                sock.settimeout(remaining)
                chunk = sock.recv(1024)
                if not chunk:
                    break
                received += chunk
    except OSError:
        if not received:
            return None
    for line in received.decode("ascii", errors="replace").split("\r\n"):
        line = line.strip()
        low = line.lower()
        if low.startswith("ok"):
            match = _SERIAL_RE.match(line)
            return match.group(1).upper() if match else ""
        if low.startswith("error"):
            return ""
    return None


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def _arp_command() -> List[str]:
    """Windows ``arp -a`` never resolves names (and rejects ``-n``). macOS and
    Linux net-tools ``arp -a`` look up a hostname per entry, multicast ones
    included, which outlasts COMMAND_TIMEOUT_S; ``-n`` prints the same table
    without lookups."""
    return ["arp", "-a"] if sys.platform == "win32" else ["arp", "-an"]


def _read_arp_table() -> str:
    """ARP table text; on Linux without net-tools (slim containers) the
    equivalent ``ip neigh`` table, which parse_arp_output also understands."""
    text = _run_command(_arp_command())
    if not text and sys.platform.startswith("linux"):
        text = _run_command(["ip", "neigh"])
    if not text:
        logger.info("[DISCOVERY] ARP table not readable; relying on the network sweep")
    return text


def _candidate_sort_key(candidate: Candidate) -> Tuple[int, int, str]:
    address = _parse_ipv4(candidate.ip)
    return (_SOURCE_ORDER.get(candidate.source, 99), int(address) if address is not None else 0, candidate.ip)


def _sweep_hosts(network: ipaddress.IPv4Network) -> List[str]:
    if network.prefixlen < MAX_SWEEP_PREFIX:
        network = ipaddress.IPv4Network((network.network_address, MAX_SWEEP_PREFIX))
    return [str(h) for h in network.hosts()]


def discover(
    port: int = DEFAULT_CONTROL_PORT,
    networks: Optional[List[ipaddress.IPv4Network]] = None,
    include_arp: bool = True,
    extra_ips: Sequence[str] = (FACTORY_IP,),
    skip_ips: Sequence[str] = (),
    timeout_s: float = 0.3,
    max_workers: int = 64,
) -> List[Candidate]:
    """Find Sirit readers. ARP hits first, then the sweep, then ``extra_ips``;
    each IP is probed once, ``skip_ips`` never. Sorted arp, sweep, linklocal.
    Never raises."""
    started = time.monotonic()
    try:
        sources: Dict[str, str] = {}
        if include_arp:
            for ip, mac in parse_arp_output(_read_arp_table()):
                if mac.startswith(SIRIT_OUI):
                    sources.setdefault(ip, "arp")
        if networks is None:
            networks = local_ipv4_networks()
        for network in networks:
            for ip in _sweep_hosts(network):
                sources.setdefault(ip, "sweep")
        for ip in extra_ips:
            sources.setdefault(ip, "linklocal")
        skip = set(skip_ips)
        targets = [(ip, source) for ip, source in sources.items() if ip not in skip]
        logger.info(
            "[DISCOVERY] probing %d address(es) on port %d (networks: %s)",
            len(targets), port, ", ".join(str(n) for n in networks) or "none",
        )
        candidates: List[Candidate] = []
        if targets:
            with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(targets)))) as pool:
                serials = list(pool.map(lambda t: probe(t[0], port, timeout_s), targets))
            for (ip, source), serial in zip(targets, serials):
                if serial is not None:
                    candidates.append(Candidate(ip=ip, serial=serial or None, source=source))
        candidates.sort(key=_candidate_sort_key)
        logger.info(
            "[DISCOVERY] found %d reader(s) in %.1fs: %s",
            len(candidates), time.monotonic() - started,
            ", ".join(f"{c.ip} ({c.serial or '?'}, {c.source})" for c in candidates) or "none",
        )
        return candidates
    except Exception as exc:
        logger.error("[DISCOVERY] failed: %s", exc)
        return []
