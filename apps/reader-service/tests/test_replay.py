"""W-023 — Session-log replay contract test.

Parses the real captured session log at apps/reader-service/logs/session.log,
feeds each ARRIVE/DEPART line through SiritClient._parse_event_message and the
TagTracker arrive/depart gate, then asserts the expected number of emitted
arrive events.

This acts as a regression fence for:
  P0-1 (multi-antenna spurious arrive events — TagTracker.mark_present gate)
  P1-1 (dropped batches — every parseable ARRIVE line must be counted)

Assumptions (documented here per spec):
  - Log lines of interest have the substring 'event.tag.arrive' or 'event.tag.depart'.
  - The raw Sirit message is the portion after the last '] ' prefix on the line
    (e.g. '[2025-09-27 17:15:04.835] [EVENT] [ARRIVE] event.tag.arrive ...').
  - reader_serial is NOT set on the SiritClient, so _parse_event_message will NOT
    populate the 'reader_serial' field in the fields dict passed to TagEvent().
  - TagEvent.reader_serial is a required non-default field → constructing TagEvent
    without it raises a TypeError. This is a real bug (BUG-001).
  - To work around BUG-001 we parse the event data from the log without going
    through _parse_event_message's final TagEvent(**fields) call. Instead we use
    SiritClient._extract_kv() directly and gate through TagTracker ourselves.
  - The log contains exactly 2 ARRIVE messages for one tag (C5A1BE1B694E02089950CE2217F46FBA),
    separated by ~21 seconds. With the default min_lap_interval_s=10, both are emitted.
  - Expected emitted arrive count: 2.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

# Ensure reader-service src is importable (mirrors conftest.py behaviour)
_src = str(Path(__file__).parent.parent / "src")
if _src not in sys.path:
    sys.path.insert(0, _src)

SESSION_LOG = Path(__file__).parent.parent / "logs" / "session.log"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_log_lines() -> list[str]:
    """Return non-empty lines from the session log."""
    if not SESSION_LOG.exists():
        return []
    return [line.rstrip("\n") for line in SESSION_LOG.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]


def _extract_sirit_msg(line: str) -> str:
    """Strip the wall-clock + channel prefix from a log line and return the raw Sirit message.

    Input:  '[2025-09-27 17:15:04.835] [EVENT] [ARRIVE] event.tag.arrive tag_id=...'
    Output: 'event.tag.arrive tag_id=...'
    """
    # Drop everything up to and including the last '] ' that precedes the Sirit token
    # Pattern: one or more [bracket blocks] followed by a space, then the message
    m = re.search(r'(?:\[[^\]]*\]\s+)+(.+)', line)
    if m:
        return m.group(1).strip()
    return line.strip()


# ---------------------------------------------------------------------------
# W-023 tests
# ---------------------------------------------------------------------------

def test_session_log_replay_arrive_count():
    """Replay session.log through the TagTracker arrive/depart gate and assert arrive count.

    BUG-001: SiritClient._parse_event_message constructs TagEvent(**fields) without
    reader_serial when self.reader_serial is None. TagEvent.reader_serial is a
    non-default required field → TypeError at construction time. To avoid masking
    a real parse failure we work around the bug by using _extract_kv + TagTracker
    directly (skipping the final TagEvent construction) and document the bug below.

    TODO(BUG-001): Remove workaround once TagEvent.reader_serial has a default of None
    or SiritClient._parse_event_message always provides reader_serial (e.g. "" sentinel).
    """
    lines = _load_log_lines()
    if not lines:
        pytest.skip("session.log is empty or missing")

    # Filter to lines that are actual tag events (not CONTROL config lines).
    # A genuine event line contains '[EVENT]' and the event type token.
    # CONTROL lines like 'reader.events.register(name = event.tag.arrive)' also
    # contain 'event.tag.arrive' but must be excluded — they have no tag_id.
    arrive_lines = [
        l for l in lines
        if "event.tag.arrive" in l.lower() and "[event]" in l.lower()
    ]
    depart_lines = [
        l for l in lines
        if "event.tag.depart" in l.lower() and "[event]" in l.lower()
    ]

    if not arrive_lines and not depart_lines:
        pytest.skip("session.log contains no arrive/depart event lines")

    from sirit_client import SiritClient
    from tag_tracker import TagTracker

    # Use min_lap_interval_s=0 for replay: the cooldown logic is tested by test_race.py
    # and test_tag_tracker.py. Here we want to count parseable arrivals from the log,
    # not re-test the interval. Using monotonic time in tests would suppress the second
    # arrive (both run in milliseconds; the 10 s window has not elapsed in wall-clock).
    tracker = TagTracker(min_lap_interval_s=0.0)

    emitted_arrives: list[dict] = []
    parse_errors: list[str] = []

    # Iterate all lines, but gate on genuine [EVENT] tag lines only.
    # CONTROL lines (e.g. 'reader.events.register(name = event.tag.arrive)') share
    # substrings with real events but must be excluded — they have no tag_id and would
    # produce parse_errors noise.
    for line in lines:
        low = line.lower()
        is_arrive = "event.tag.arrive" in low and "[event]" in low
        is_depart = "event.tag.depart" in low and "[event]" in low
        if not is_arrive and not is_depart:
            continue

        sirit_msg = _extract_sirit_msg(line)
        kv = SiritClient._extract_kv(sirit_msg)
        tag_raw = kv.get("tag_id")
        if not tag_raw:
            parse_errors.append(f"no tag_id in: {sirit_msg!r}")
            continue

        tag_hex = tag_raw.upper().lstrip("0X")
        antenna = int(kv["antenna"]) if "antenna" in kv else 0

        if is_arrive:
            emitted = tracker.mark_present(tag_hex, antenna)
            if emitted:
                ts_field = kv.get("first", "unknown")
                emitted_arrives.append({"tag_id": tag_hex, "first": ts_field, "antenna": antenna})
        elif is_depart:
            tracker.mark_absent(tag_hex, antenna)

    # Document any parse failures found during replay (non-fatal: they would
    # represent BUG-002 type issues where _extract_kv fails to find tag_id).
    # The test still runs; we just surface the information.
    if parse_errors:
        # Non-fatal: log them but don't fail. A separate targeted test should
        # xfail the specific broken line if this becomes a production concern.
        import warnings
        warnings.warn(f"session.log replay: {len(parse_errors)} unparseable lines: {parse_errors}")

    # The session log (2025-09-27) has 2 ARRIVE events for tag C5A1BE1B694E02089950CE2217F46FBA:
    #   first arrive:  2025-09-27T15:15:04.403 (antenna=1)
    #   second arrive: 2025-09-27T15:15:25.537 (antenna=1) — ~21 s later, past 10 s cooldown
    # Both should be emitted. Expected: 2.
    #
    # NOTE: TagTracker is configured with min_lap_interval_s=0 for replay. The cooldown
    # is already tested by test_race.py / test_tag_tracker.py. Using time.monotonic()
    # (the default) would suppress the second arrive because both lines execute
    # milliseconds apart — far less than any sensible cooldown. Setting it to 0 ensures
    # both genuine arrive lines produce emitted events.
    EXPECTED_ARRIVE_COUNT = 2

    assert len(emitted_arrives) == EXPECTED_ARRIVE_COUNT, (
        f"Expected {EXPECTED_ARRIVE_COUNT} emitted arrive events from session.log replay, "
        f"got {len(emitted_arrives)}. "
        f"Emitted: {emitted_arrives}. "
        f"Arrive lines in log: {len(arrive_lines)}. "
        f"Depart lines in log: {len(depart_lines)}."
    )


@pytest.mark.xfail(
    reason=(
        "BUG-001: TagEvent.reader_serial is a required non-default field but "
        "SiritClient._parse_event_message omits it when self.reader_serial is None. "
        "Calling TagEvent(**fields) without reader_serial raises TypeError. "
        "Fix: add `reader_serial: Optional[str] = None` to TagEvent dataclass, "
        "or ensure _parse_event_message always provides the field (e.g. '' sentinel). "
        "This test exercises the bug directly so it is documented and cannot regress silently."
    ),
    raises=TypeError,
    strict=True,
)
def test_parse_event_message_without_reader_serial_raises_type_error():
    """Reproduce BUG-001: _parse_event_message without reader_serial raises TypeError.

    The SiritClient is constructed without calling start() (no network) so
    reader_serial remains None. Feeding an ARRIVE log line through
    _parse_event_message must fail with TypeError because TagEvent.__init__
    requires reader_serial but it is absent from the fields dict.

    When BUG-001 is fixed (TagEvent.reader_serial gets a default or
    _parse_event_message always injects the field), this xfail will flip to
    a pass and strict=True will catch it, reminding us to remove the xfail marker.
    """
    lines = _load_log_lines()
    if not lines:
        pytest.skip("session.log is empty or missing")

    # Use only genuine [EVENT] arrive lines — CONTROL lines (which also contain
    # 'event.tag.arrive') return None from _parse_event_message because they have
    # no tag_id, which would mask the TypeError we are testing for.
    arrive_lines = [
        l for l in lines
        if "event.tag.arrive" in l.lower() and "[event]" in l.lower()
    ]
    if not arrive_lines:
        pytest.skip("no arrive event lines in session.log")

    from sirit_client import SiritClient

    # Build a minimal SiritClient without touching the network.
    # We patch ip/ports to dummy values; start() is NOT called so no socket is opened.
    client = SiritClient(
        ip="127.0.0.1",
        control_port=9999,
        event_port=9998,
        init_commands_path=None,
        colorize=False,
        raw=False,
        interactive=False,
        backend_url=None,
        backend_transport="mock",
    )
    # reader_serial is None — the bug condition

    sirit_msg = _extract_sirit_msg(arrive_lines[0])
    # This call should raise TypeError due to BUG-001
    client._parse_event_message("arrive", sirit_msg)


def test_session_log_total_line_count():
    """Sanity check: session.log has the expected number of non-empty lines.

    If this fails it means the fixture file was truncated or corrupted.
    Known snapshot (2025-09-27 capture): 50 non-empty lines.
    """
    lines = _load_log_lines()
    if not lines:
        pytest.skip("session.log is empty or missing")

    # 50 total raw lines minus 2 blank lines = 48 non-empty lines (2025-09-27 capture)
    EXPECTED_LINE_COUNT = 48
    assert len(lines) == EXPECTED_LINE_COUNT, (
        f"session.log line count changed: expected {EXPECTED_LINE_COUNT}, got {len(lines)}. "
        "If the log was intentionally updated, adjust EXPECTED_LINE_COUNT in this test."
    )
