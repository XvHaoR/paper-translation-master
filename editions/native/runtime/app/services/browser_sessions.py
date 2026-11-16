"""Browser connections for the desktop launcher's process lifetime."""
from __future__ import annotations

import threading
import time


class BrowserSessions:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._clients: set[str] = set()
        self._seen = False
        self._empty_since = time.monotonic()

    def connect(self, client: str) -> None:
        with self._lock:
            self._seen = True
            self._clients.add(client)

    def disconnect(self, client: str) -> None:
        with self._lock:
            self._clients.discard(client)
            if not self._clients:
                self._empty_since = time.monotonic()

    def status(self) -> dict:
        with self._lock:
            return {"clients": len(self._clients), "seen_browser": self._seen,
                    "idle_seconds": 0 if self._clients else time.monotonic() - self._empty_since}
