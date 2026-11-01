import importlib
import os
from types import SimpleNamespace
from typing import Optional
from unittest.mock import AsyncMock, patch

TEST_ENVIRONMENT: dict[str, str] = {
    "DOORMAN_SENTRY_DSN": "",
    "DOORMAN_ACCESS_PINS": "{}",
    "DOORMAN_MM_DEVICE_TYPE": "DOOR",
    "DOORMAN_MM_ACCESS_URL": "ws://unit-test.invalid/ws/access",
    "DOORMAN_MM_DATA_FILE": "/tmp/unit-test-mm-doorman.json",
    "DOORMAN_MM_API_KEY": "unit-test-api-key",
    "DOORMAN_MM_PING_INTERVAL": "30",
}

with patch.dict(os.environ, TEST_ENVIRONMENT, clear=True):
    app = importlib.import_module("doorman.app")
    websocket_client = importlib.import_module("websocket_client")


class FakeHttpClient:
    def __init__(self) -> None:
        self.post = AsyncMock()


class FakeRequest:
    def __init__(
        self,
        method: str = "POST",
        body: bytes = b"",
        http: Optional[FakeHttpClient] = None,
    ) -> None:
        self.method = method
        self.body = AsyncMock(return_value=body)
        self.app = SimpleNamespace(
            state=SimpleNamespace(http=http if http is not None else FakeHttpClient())
        )
