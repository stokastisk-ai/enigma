"""Runs the REAL FastAPI app under uvicorn on 127.0.0.1:<random port> in a thread, so connector tests use genuine HTTP."""
from __future__ import annotations

import socket
import threading
import time

import uvicorn

from api_helpers import DEV_SECRET, Clock
from enigma_api.main import create_app
from enigma_api.realtime import RecordingBroadcaster
from enigma_api.settings import Settings
from enigma_api.store.memory import MemoryStore


class LiveApi:
    def __init__(self, real_clock: bool = False):
        self.clock = Clock()
        if real_clock:
            from datetime import datetime, timezone
            self.clock = lambda: datetime.now(timezone.utc)          # type: ignore[assignment]
        self.store = MemoryStore(self.clock)
        self.bus = RecordingBroadcaster()
        self.settings = Settings(env="test", dev_auth=True, dev_jwt_secret=DEV_SECRET, cron_secret="c" * 32)
        self.app = create_app(self.settings, store=self.store, broadcaster=self.bus, clock=self.clock)
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        self.port = s.getsockname()[1]
        s.close()
        self.url = f"http://127.0.0.1:{self.port}"
        self.server = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="error", access_log=False))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        for _ in range(100):
            if self.server.started:
                return self
            time.sleep(0.05)
        raise RuntimeError("uvicorn did not start")

    def __exit__(self, *a):
        self.server.should_exit = True
        self.thread.join(5)
