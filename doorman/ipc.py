"""
Inter-process communication between the FastAPI server and the websocket client.

Uses a threading.Queue since the websocket client runs in a separate thread.
Both asyncio (FastAPI) and synchronous (websocket client) code can safely
interact with threading.Queue.
"""

import queue

# FastAPI pushes door_access events here; the websocket client consumes them.
door_access_queue: queue.Queue = queue.Queue()
