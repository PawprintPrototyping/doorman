"""
Unified entrypoint that runs the Flask server and the MemberMatters
websocket client in the same process.

The Flask app is exposed as `app` for gunicorn/meinheld to pick up.
The websocket client runs in a daemon thread so it dies with the process.
"""

import threading

from doorman.app import app  # noqa: F401 — gunicorn imports `app` from this module
from websocket_client import DEBUG, MMAccessClient


def start_websocket_client():
    client = MMAccessClient(debug=DEBUG)
    client.run()


# Start the websocket client in a daemon thread
ws_thread = threading.Thread(
    target=start_websocket_client, daemon=True, name="mm-ws-client"
)
ws_thread.start()
