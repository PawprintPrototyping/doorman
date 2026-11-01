import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, mock_open, patch

from tests.support import websocket_client


class AccessDeviceTypeTests(unittest.TestCase):
    def test_string_value_matches_membermatters_protocol(self):
        self.assertEqual(str(websocket_client.AccessDeviceType.DOOR), "door")
        self.assertEqual(str(websocket_client.AccessDeviceType.INTERLOCK), "interlock")


class ClientConstructionTests(unittest.TestCase):
    def test_constructor_wires_membermatters_callbacks(self):
        with (
            patch.object(websocket_client.websocket, "enableTrace") as enable_trace,
            patch.object(
                websocket_client.websocket.WebSocketApp,
                "__init__",
                return_value=None,
            ) as websocket_init,
        ):
            client = websocket_client.MMAccessClient(debug=True)

        enable_trace.assert_called_once_with(True)
        websocket_init.assert_called_once()
        (url,) = websocket_init.call_args.args
        self.assertEqual(
            url,
            "ws://unit-test.invalid/ws/access/door/Unnamed",
        )
        self.assertIs(websocket_init.call_args.kwargs["on_open"].__self__, client)
        self.assertIs(websocket_init.call_args.kwargs["on_message"].__self__, client)
        self.assertIs(websocket_init.call_args.kwargs["on_error"].__self__, client)
        self.assertIs(websocket_init.call_args.kwargs["on_close"].__self__, client)
        self.assertFalse(client.locked_out)


class SerializationAndStorageTests(unittest.TestCase):
    def test_send_log_access_serializes_successful_access(self):
        client = MagicMock()

        websocket_client.MMAccessClient.send_log_access(client, "card-123", True)

        self.assertEqual(
            json.loads(client.send.call_args.args[0]),
            {"command": "log_access", "card_id": "card-123"},
        )

    def test_send_log_access_serializes_denied_access(self):
        client = MagicMock()

        websocket_client.MMAccessClient.send_log_access(client, "card-123", False)

        self.assertEqual(
            json.loads(client.send.call_args.args[0]),
            {"command": "log_access_denied", "card_id": "card-123"},
        )

    def test_save_tags_persists_tags_hash_and_lockout_state(self):
        client = MagicMock()
        client.locked_out = True
        data = {"tags": ["card-123"], "hash": "hash-123"}
        with (
            patch("builtins.open", mock_open()) as open_file,
            patch.object(websocket_client.json, "dump") as json_dump,
        ):
            websocket_client.MMAccessClient.save_tags(client, data)

        open_file.assert_called_once_with(websocket_client.MM_DATA_FILE, "w")
        json_dump.assert_called_once_with(
            {
                "tags": ["card-123"],
                "hash": "hash-123",
                "locked_out": True,
            },
            open_file.return_value.__enter__.return_value,
        )

    def test_load_tags_defaults_missing_lockout_to_false(self):
        client = MagicMock()
        client.locked_out = True
        data = {"tags": ["card-123"], "hash": "hash-123"}
        with (
            patch("builtins.open", mock_open()) as open_file,
            patch.object(websocket_client.json, "load", return_value=data),
        ):
            result = websocket_client.MMAccessClient.load_tags(client)

        self.assertEqual(result, data)
        self.assertFalse(client.locked_out)
        open_file.assert_called_once_with(websocket_client.MM_DATA_FILE)

    def test_update_device_locked_out_rewrites_saved_tags(self):
        client = MagicMock()
        client.load_tags.return_value = {"tags": ["card-123"], "hash": "hash-123"}

        websocket_client.MMAccessClient.update_device_locked_out(client, True)

        self.assertTrue(client.locked_out)
        client.load_tags.assert_called_once_with()
        client.save_tags.assert_called_once_with(client.load_tags.return_value)


class CommandHandlingTests(unittest.TestCase):
    def test_ping_command_sends_pong(self):
        client = MagicMock()

        websocket_client.MMAccessClient.parse_command(client, {"command": "ping"})

        self.assertEqual(json.loads(client.send.call_args.args[0]), {"command": "pong"})

    def test_pong_command_refreshes_timestamp(self):
        client = MagicMock()
        with patch.object(websocket_client.time, "monotonic", return_value=123.0):
            websocket_client.MMAccessClient.parse_command(client, {"command": "pong"})

        self.assertEqual(client._last_pong, 123.0)

    def test_bump_command_opens_door(self):
        client = MagicMock()
        with patch.object(websocket_client, "open_door") as open_door:
            websocket_client.MMAccessClient.parse_command(client, {"command": "bump"})

        open_door.assert_called_once_with()

    def test_bump_command_reports_door_errors(self):
        client = MagicMock()
        error = RuntimeError("door relay failed")
        with (
            patch.object(websocket_client, "open_door", side_effect=error),
            patch.object(
                websocket_client.sentry_sdk, "capture_exception"
            ) as capture_exception,
        ):
            websocket_client.MMAccessClient.parse_command(client, {"command": "bump"})

        capture_exception.assert_called_once_with(error)

    def test_sync_command_saves_tags(self):
        client = MagicMock()
        data = {"command": "sync", "tags": ["card-123"], "hash": "hash-123"}

        websocket_client.MMAccessClient.parse_command(client, data)

        client.save_tags.assert_called_once_with(data)

    def test_lockout_command_uses_missing_value_default(self):
        client = MagicMock()

        websocket_client.MMAccessClient.parse_command(
            client, {"command": "update_device_locked_out"}
        )

        client.update_device_locked_out.assert_called_once_with(False)


class CallbackTests(unittest.TestCase):
    def test_on_message_reports_json_decode_error(self):
        client = MagicMock()
        with patch.object(
            websocket_client.sentry_sdk, "capture_exception"
        ) as capture_exception:
            websocket_client.MMAccessClient.on_message(client, None, "not-json")

        self.assertIsInstance(capture_exception.call_args.args[0], json.JSONDecodeError)
        client.parse_command.assert_not_called()

    def test_on_open_sends_authentication_and_sets_timestamp(self):
        client = MagicMock()
        client.url = "ws://unit-test.invalid/ws/access/door/Unnamed"
        with patch.object(websocket_client.time, "monotonic", return_value=123.0):
            websocket_client.MMAccessClient.on_open(client, None)

        self.assertEqual(client._last_pong, 123.0)
        self.assertEqual(
            json.loads(client.send.call_args.args[0]),
            {"command": "authenticate", "secret_key": "unit-test-api-key"},
        )


class PumpTests(unittest.TestCase):
    def test_queue_pump_forwards_one_access_event(self):
        client = MagicMock()
        stop = MagicMock()
        stop.is_set.side_effect = [False, True]
        client._pump_stop = stop
        event = {"id_number": "card-123", "success": True}
        with patch.object(websocket_client, "door_access_queue") as access_queue:
            access_queue.get.return_value = event
            websocket_client.MMAccessClient._pump_door_access_queue(client)

        access_queue.get.assert_called_once_with(timeout=0.5)
        client.send_log_access.assert_called_once_with(card_id="card-123", success=True)

    def test_keepalive_pump_resets_timestamp_while_disconnected(self):
        client = MagicMock()
        stop = MagicMock()
        stop.wait.side_effect = [False, True]
        client._pump_stop = stop
        client.sock = None
        with patch.object(websocket_client.time, "monotonic", return_value=123.0):
            websocket_client.MMAccessClient._pump_keepalive(client)

        stop.wait.assert_called_with(websocket_client.MM_PING_INTERVAL)
        self.assertEqual(client._last_pong, 123.0)
        client.send.assert_not_called()

    def test_keepalive_pump_sends_ping_when_pong_is_fresh(self):
        client = MagicMock()
        stop = MagicMock()
        stop.wait.side_effect = [False, True]
        client._pump_stop = stop
        client.sock = SimpleNamespace(connected=True)
        client._last_pong = 100.0
        with patch.object(websocket_client.time, "monotonic", return_value=123.0):
            websocket_client.MMAccessClient._pump_keepalive(client)

        self.assertEqual(json.loads(client.send.call_args.args[0]), {"command": "ping"})

    def test_keepalive_pump_closes_on_pong_timeout(self):
        client = MagicMock()
        stop = MagicMock()
        stop.wait.side_effect = [False, True]
        client._pump_stop = stop
        client.sock = SimpleNamespace(connected=True)
        client._last_pong = 0.0
        with patch.object(
            websocket_client.time, "monotonic", side_effect=[100.0, 101.0]
        ):
            websocket_client.MMAccessClient._pump_keepalive(client)

        client.close.assert_called_once_with()
        self.assertEqual(client._last_pong, 101.0)
        client.send.assert_not_called()

    def test_keepalive_pump_reports_send_error(self):
        client = MagicMock()
        stop = MagicMock()
        stop.wait.side_effect = [False, True]
        client._pump_stop = stop
        client.sock = SimpleNamespace(connected=True)
        client._last_pong = 100.0
        error = RuntimeError("socket send failed")
        client.send.side_effect = error
        with (
            patch.object(websocket_client.time, "monotonic", return_value=123.0),
            patch.object(
                websocket_client.sentry_sdk, "capture_exception"
            ) as capture_exception,
        ):
            websocket_client.MMAccessClient._pump_keepalive(client)

        capture_exception.assert_called_once_with(error)


class RunTests(unittest.TestCase):
    def test_run_starts_pumps_reconnects_and_stops_them(self):
        client = MagicMock()
        stop = MagicMock()
        queue_thread = MagicMock()
        keepalive_thread = MagicMock()
        with (
            patch.object(websocket_client.threading, "Event", return_value=stop),
            patch.object(
                websocket_client.threading,
                "Thread",
                side_effect=[queue_thread, keepalive_thread],
            ) as thread,
            patch.object(websocket_client.time, "monotonic", return_value=123.0),
        ):
            websocket_client.MMAccessClient.run(client)

        self.assertEqual(thread.call_count, 2)
        queue_thread.start.assert_called_once_with()
        keepalive_thread.start.assert_called_once_with()
        client.run_forever.assert_called_once_with(reconnect=15)
        stop.set.assert_called_once_with()
