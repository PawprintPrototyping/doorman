"""
Inter-process communication between the Flask server and the websocket client.

Uses a threading.Queue since both components run in the same process.
"""

import queue

# Flask pushes door_access events here; the websocket client consumes them.
door_access_queue: queue.Queue = queue.Queue()
