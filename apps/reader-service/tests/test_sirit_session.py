"""Unit tests for W-061: session rebind on reader reconnect."""
from __future__ import annotations

from unittest.mock import MagicMock, patch, call


def _make_client() -> "SiritClient":
    """Build a minimal SiritClient with no real sockets or backend."""
    from sirit_client import SiritClient
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
    # Attach a mock backend so _emit_event does not raise
    from backend_client.mock import MockBackendClient
    client._backend = MockBackendClient()
    # Prevent actual socket sends
    client.control_sock = None
    return client


class TestReconnectResetsSessionBind:

    def test_first_connection_id_sets_session_and_binds(self):
        """Receiving event.connection id for the first time sets session.id and triggers bind."""
        client = _make_client()
        assert client.session.id is None
        assert client.session.bound is False

        with patch.object(client, "_maybe_bind_and_config") as mock_bind:
            client._handle_message("EVENT", "event.connection id = 42\r\n")
            mock_bind.assert_called_once()

        assert client.session.id == 42

    def test_reconnect_with_new_id_resets_bound_and_rebinds(self):
        """Receiving a different connection id resets bound=False and re-triggers bind."""
        client = _make_client()
        # Simulate an already-bound session with id=1
        client.session.id = 1
        client.session.bound = True

        with patch.object(client, "_maybe_bind_and_config") as mock_bind:
            # Reader rebooted: new connection id = 2
            client._handle_message("EVENT", "event.connection id = 2\r\n")
            mock_bind.assert_called_once()

        assert client.session.id == 2
        assert client.session.bound is False

    def test_same_id_does_not_rebind(self):
        """Receiving the same connection id again does not reset bound or re-trigger bind."""
        client = _make_client()
        client.session.id = 5
        client.session.bound = True

        with patch.object(client, "_maybe_bind_and_config") as mock_bind:
            # Same id = 5 again (harmless duplicate)
            client._handle_message("EVENT", "event.connection id = 5\r\n")
            # _maybe_bind_and_config should NOT be called because id == self.session.id
            mock_bind.assert_not_called()

        assert client.session.id == 5
        assert client.session.bound is True
