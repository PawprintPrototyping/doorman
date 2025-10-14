import os
from enum import Enum
import _thread
import time
import json
import logging

import websocket
import rel

from doorman.app import open_door

class AccessDeviceType(Enum):
    DOOR = "door"
    INTERLOCK = "interlock"
    def __str__(self):
        return f"{self.value}"

MM_ACCESS_URL = os.environ.get("DOORMAN_MM_ACCESS_URL", "ws://membermatters.local/ws/access")
MM_DEVICE_NAME = os.environ.get("DOORMAN_MM_DEVICE_NAME", "Unnamed")
MM_DEVICE_TYPE = AccessDeviceType[os.environ.get("DOORMAN_MM_DEVICE_TYPE", "DOOR").upper()]
MM_DATA_FILE = os.environ.get("DOORMAN_MM_DATA_FILE", "/tmp/mm-doorman.json")
MM_API_KEY = os.environ.get("DOORMAN_MM_API_KEY", "unset")

logger = logging.getLogger(__name__)

def save_tags(data):
    with open(MM_DATA_FILE, "w") as tags_file:
        tags_doc = {
            "tags": data["tags"],
            "hash": data["hash"],
        }
        json.dump(tags_doc, tags_file)


def parse_command(data):
    match data.get("command"):
        case "ping":
            websocket.send(json.dumps({"command": "pong"}))

        case "reboot":
            # Not implemented (lol)
            pass

        case "bump":
            # Bump the door open
            open_door()

        case "sync":
            # Save synced tags to a file
            save_tags(data)


def on_message(ws, message):
    print(f"> {message}")
    try:
        data = json.loads(message)
        parse_command(data)
    except json.decoder.JSONDecodeError as e:
        logger.exception(e)
        return


def on_error(ws, error):
    print(f"[ERROR]: {error}")

def on_close(ws, close_status_code, close_msg):
    print("### closed ###")
    print("Status: {close_status_code}, Message: {close_msg}")

def on_open(ws):
    print("Opened connection")
    # Send auth
    auth_packet = {
        "command": "authenticate",
        "secret_key": MM_API_KEY,
    }
    ws.send(json.dumps(auth_packet))

if __name__ == "__main__":
    #websocket.enableTrace(True)
    ws = websocket.WebSocketApp(f"{MM_ACCESS_URL}/{MM_DEVICE_TYPE}/{MM_DEVICE_NAME}",
                              on_open=on_open,
                              on_message=on_message,
                              on_error=on_error,
                              on_close=on_close)

    ws.run_forever(dispatcher=rel, reconnect=5)  # Set dispatcher to automatic reconnection, 5 second reconnect delay if connection closed unexpectedly
    rel.signal(2, rel.abort)  # Keyboard Interrupt
    rel.dispatch()
