"""Unit tests for the host-clock-push behaviour in _maybe_bind_and_config.

The Sirit INfinity 510 has no battery-backed RTC; on fresh power-up it reports
events stamped with its manufacturer epoch (~1999). Racetag's race-time math
trusts the reader's clock, so a stale RTC would yield year-1999 last_pass_time
values and negative total_time_ms. The fix: as part of session bind, push the
host's UTC clock to the reader via `info.time=<ISO>` BEFORE any init_commands
fire.
"""
from __future__ import annotations

import re
from unittest.mock import patch


def _make_client():
    from sirit_client import SiritClient
    from backend_client.mock import MockBackendClient

    client = SiritClient(
        ip="127.0.0.1",
        control_port=50007,
        event_port=50008,
        init_commands_path=None,
        colorize=False,
        raw=False,
        interactive=False,
        backend_transport="mock",
    )
    client._backend = MockBackendClient()
    client.control_sock = None
    return client


def _flatten(call_args_list):
    """Collapse _send_control's call_args (each a list) into one flat list of strings."""
    out: list[str] = []
    for c in call_args_list:
        # c.args[0] is the cmds list passed to _send_control
        out.extend(c.args[0])
    return out


def test_bind_pushes_host_utc_clock_before_init_commands():
    """info.time=<ISO> must appear in the post-bind command stream."""
    client = _make_client()
    client.session.id = 42

    with patch.object(client, "_send_control") as send:
        client._maybe_bind_and_config()

    sent = _flatten(send.call_args_list)

    bind_idx = next(i for i, c in enumerate(sent) if c.startswith("reader.events.bind"))
    time_idx = next(
        (i for i, c in enumerate(sent) if c.startswith("info.time=")), None
    )
    assert time_idx is not None, f"no info.time= command sent. commands: {sent}"
    assert time_idx > bind_idx, (
        "info.time must be sent AFTER reader.events.bind so the bound channel "
        f"is the one that gets the corrected clock. commands: {sent}"
    )


def test_bind_pushes_well_formed_iso_timestamp():
    """The value of info.time= must look like a millisecond-precision ISO 8601 string."""
    client = _make_client()
    client.session.id = 7

    with patch.object(client, "_send_control") as send:
        client._maybe_bind_and_config()

    sent = _flatten(send.call_args_list)
    time_cmds = [c for c in sent if c.startswith("info.time=")]
    assert len(time_cmds) == 1
    value = time_cmds[0].split("=", 1)[1]
    # Expected shape: 2026-05-12T20:05:00.123 (no timezone suffix; reader is set to UTC)
    assert re.match(
        r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}$",
        value,
    ), f"info.time value not in expected ISO format: {value!r}"


def test_bind_clock_push_is_idempotent_on_already_bound():
    """If session.bound is already True, the whole config path is skipped."""
    client = _make_client()
    client.session.id = 5
    client.session.bound = True

    with patch.object(client, "_send_control") as send:
        client._maybe_bind_and_config()

    assert send.call_args_list == [], "no commands should be sent when already bound"
