"""
Unified entrypoint that runs the FastAPI server and the MemberMatters
websocket client in the same process.

Run with:
    uvicorn main:app --host 0.0.0.0 --port 5000

The websocket client is started via the FastAPI lifespan handler.
"""

from doorman.app import app  # noqa: F401 — uvicorn imports `app` from this module
