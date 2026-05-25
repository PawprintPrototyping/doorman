import json
import logging
import os
import queue
import threading
import time
from enum import Enum

import sentry_sdk
import websocket

from doorman.app import env_bool, open_door
from doorman.ipc import door_access_queue


class AccessDeviceType(Enum):
    DOOR = "door"
    INTERLOCK = "interlock"

    def __str__(self):
        return f"{self.value}"


MM_ACCESS_URL = os.environ.get(
    "DOORMAN_MM_ACCESS_URL", "ws://membermatters.local/ws/access"
)
MM_DEVICE_NAME = os.environ.get("DOORMAN_MM_DEVICE_NAME", "Unnamed")
MM_DEVICE_TYPE = AccessDeviceType[
    os.environ.get("DOORMAN_MM_DEVICE_TYPE", "DOOR").upper()
]
MM_DATA_FILE = os.environ.get("DOORMAN_MM_DATA_FILE", "/tmp/mm-doorman.json")
MM_API_KEY = os.environ.get("DOORMAN_MM_API_KEY", "unset")
MM_PING_INTERVAL = int(os.environ.get("DOORMAN_MM_PING_INTERVAL", "30"))
DEBUG = env_bool(os.environ.get("DEBUG", False))

logger = logging.getLogger("doorman_client")


class MMAccessClient(websocket.WebSocketApp):
    def __init__(self, *args, **kwargs):
        websocket.enableTrace(kwargs.get("debug", False))
        self.locked_out = False
        super().__init__(
            f"{MM_ACCESS_URL}/{MM_DEVICE_TYPE}/{MM_DEVICE_NAME}",
            on_open=self.on_open,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close,
        )

    def run(self) -> None:
        # rel's dispatcher installs a SIGINT handler at construction time,
        # which fails off the main thread. Use run_forever()'s default select
        # loop and pump work from daemon threads.
        self._pump_stop = threading.Event()
        self._last_pong = time.monotonic()
        pump = threading.Thread(
            target=self._pump_door_access_queue,
            daemon=True,
            name="mm-ws-queue-pump",
        )
        keepalive = threading.Thread(
            target=self._pump_keepalive,
            daemon=True,
            name="mm-ws-keepalive",
        )
        pump.start()
        keepalive.start()
        try:
            self.run_forever(reconnect=15)
        finally:
            self._pump_stop.set()

    def _pump_door_access_queue(self) -> None:
        """Forward queued door_access events to the server until stopped."""
        while not self._pump_stop.is_set():
            try:
                event = door_access_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self.send_log_access(
                    card_id=event["id_number"],
                    success=event["success"],
                )
            except Exception as e:
                logger.exception("Error sending door_access event")
                sentry_sdk.capture_exception(e)

    def _pump_keepalive(self) -> None:
        """Send periodic client→server pings; force reconnect on pong timeout."""
        timeout_seconds = MM_PING_INTERVAL * 3
        while not self._pump_stop.wait(MM_PING_INTERVAL):
            sock = self.sock
            if sock is None or not sock.connected:
                # Reset so a freshly reopened socket isn't immediately stale.
                self._last_pong = time.monotonic()
                continue

            if time.monotonic() - self._last_pong > timeout_seconds:
                logger.warning(
                    "MemberMatters pong timeout (%ss), forcing reconnect",
                    timeout_seconds,
                )
                self._last_pong = time.monotonic()
                try:
                    self.close()
                except Exception as e:
                    logger.exception("Error closing socket on pong timeout")
                    sentry_sdk.capture_exception(e)
                continue

            try:
                logger.debug("sending keep-alive ping")
                self.send(json.dumps({"command": "ping"}))
            except Exception as e:
                logger.exception("Error sending keep-alive ping")
                sentry_sdk.capture_exception(e)

    def send_log_access(self, card_id: str, success: bool) -> None:
        """Send a log_access or log_access_denied command to MemberMatters."""
        command = "log_access" if success else "log_access_denied"
        packet = {
            "command": command,
            "card_id": card_id,
        }
        logger.info(f"Sending {command}: card_id={card_id}")
        self.send(json.dumps(packet))

    def save_tags(self, data: dict) -> None:
        with open(MM_DATA_FILE, "w") as tags_file:
            tags_doc = {
                "tags": data["tags"],
                "hash": data["hash"],
                "locked_out": self.locked_out,
            }
            json.dump(tags_doc, tags_file)

    def load_tags(self) -> dict:
        with open(MM_DATA_FILE) as tags_file:
            data = json.load(tags_file)
        self.locked_out = data.get("locked_out", False)
        return data

    def update_device_locked_out(self, locked_out: bool) -> None:
        logger.info(f"Update device lockout: locked_out = {locked_out}")
        tags_doc = self.load_tags()
        self.locked_out = locked_out
        self.save_tags(tags_doc)

    def parse_command(self, data: dict) -> None:
        command = data.get("command")
        if command == "ping":
            self.send(json.dumps({"command": "pong"}))

        elif command == "pong":
            self._last_pong = time.monotonic()

        elif command == "reboot":
            # Not implemented (lol)
            pass

        elif command == "bump":
            # Bump the door open. open_door is synchronous so we don't need
            # to spin up an event loop in this websocket worker thread.
            try:
                open_door()
            except Exception as e:
                logger.exception("Error opening door from bump command")
                sentry_sdk.capture_exception(e)

        elif command == "sync":
            # Save synced tags to a file
            self.save_tags(data)

        elif command == "update_device_locked_out":
            self.update_device_locked_out(data.get("locked_out", False))

    def on_message(self, ws, message: str) -> None:
        logger.debug(f"> {message}")
        try:
            data = json.loads(message)
            self.parse_command(data)
        except json.decoder.JSONDecodeError as e:
            logger.exception(e)
            sentry_sdk.capture_exception(e)
            return

    def on_error(self, ws, error: Exception) -> None:
        logger.error(f"[SOCKET ERROR]: {error}")
        sentry_sdk.capture_exception(error)

    def on_close(self, ws, close_status_code: int, close_msg: str) -> None:
        logger.info("### closed ###")
        logger.info(f"Status: {close_status_code}, Message: {close_msg}")

    def on_open(self, data) -> None:
        self._last_pong = time.monotonic()
        logger.info(f"Opened connection to {self.url}, sending auth.")
        # Send auth
        auth_packet = {
            "command": "authenticate",
            "secret_key": MM_API_KEY,
        }
        self.send(json.dumps(auth_packet))


if __name__ == "__main__":
    # Standalone mode: run without FastAPI (useful for local testing).
    from doorman.app import setup_logging

    setup_logging()
    client = MMAccessClient(debug=DEBUG)
    client.run()
