import json
import logging
import os
import queue
import time
from enum import Enum

import rel
import websocket
from rich.logging import RichHandler

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
DEBUG = env_bool(os.environ.get("DEBUG", False))

logging.basicConfig(
    level=logging.NOTSET, format="%(message)s", datefmt="[%X]", handlers=[RichHandler()]
)
logger = logging.getLogger("doorman_client")
logger.setLevel(logging.INFO)
if DEBUG:
    logger.setLevel(logging.DEBUG)


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
        self.run_forever(
            dispatcher=rel, reconnect=15
        )  # Set dispatcher to automatic reconnection, 15-second reconnect delay if connection closed unexpectedly
        # Poll the door_access queue every 0.5s and forward events to the server
        rel.timeout(0.5, self._poll_door_access_queue)
        rel.signal(2, rel.abort)  # Keyboard Interrupt
        rel.dispatch()

    def _poll_door_access_queue(self) -> bool:
        """Check the shared queue for door_access events and send them."""
        try:
            while True:
                event = door_access_queue.get_nowait()
                self.send_door_access(
                    id_number=event["id_number"],
                    success=event["success"],
                    method=event.get("method", "rfid"),
                )
        except queue.Empty:
            pass
        # Re-schedule ourselves — returning True keeps the rel timeout alive
        return True

    def send_door_access(
        self, id_number: str, success: bool, method: str = "rfid"
    ) -> None:
        """Send a door_access command to the MemberMatters server."""
        packet = {
            "command": "door_access",
            "payload": {
                "id_number": id_number,
                "time": str(int(time.time())),
                "success": success,
                "method": method,
            },
        }
        logger.info(
            f"Sending door_access: id={id_number} success={success} method={method}"
        )
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

        elif command == "reboot":
            # Not implemented (lol)
            pass

        elif command == "bump":
            # Bump the door open
            open_door()

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
            return

    def on_error(self, ws, error: Exception) -> None:
        logger.error(f"[SOCKET ERROR]: {error}")

    def on_close(self, ws, close_status_code: int, close_msg: str) -> None:
        logger.info("### closed ###")
        logger.info(f"Status: {close_status_code}, Message: {close_msg}")

    def on_open(self, data) -> None:
        logger.info(f"Opened connection to {self.url}, sending auth.")
        # Send auth
        auth_packet = {
            "command": "authenticate",
            "secret_key": MM_API_KEY,
        }
        self.send(json.dumps(auth_packet))


if __name__ == "__main__":
    # Standalone mode: run without Flask (useful for local testing)
    client = MMAccessClient(debug=DEBUG)
    client.run()
