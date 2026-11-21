"""Server-authorised Realtime broadcast to PRIVATE channels (topic ``conv:<conversation_id>``).

Browsers can only *receive*: the RLS policy on realtime.messages (migration 0001) lets workspace members subscribe and nobody publish.
Broadcast is best-effort. The durable truth is inference_events; if a broadcast is lost the browser's polling fallback catches up from
its last seen event_seq. A failed broadcast therefore never fails ingestion.
"""
from __future__ import annotations

import logging
from typing import Any, Protocol

import httpx

log = logging.getLogger("enigma.realtime")


class Broadcaster(Protocol):
    def send(self, conversation_id: str, event: str, payload: dict[str, Any]) -> bool: ...


class NullBroadcaster:
    def send(self, conversation_id: str, event: str, payload: dict[str, Any]) -> bool:
        return False


class RecordingBroadcaster:
    """Test/dev double that records what would have been published."""
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, dict[str, Any]]] = []

    def send(self, conversation_id: str, event: str, payload: dict[str, Any]) -> bool:
        self.sent.append((conversation_id, event, payload))
        return True


class SupabaseBroadcaster:
    def __init__(self, url: str, service_key: str, transport: httpx.BaseTransport | None = None):
        self._c = httpx.Client(base_url=url.rstrip("/") + "/realtime/v1", timeout=3.0, transport=transport,
                               headers={"apikey": service_key, "Authorization": f"Bearer {service_key}", "Content-Type": "application/json"})

    def send(self, conversation_id: str, event: str, payload: dict[str, Any]) -> bool:
        try:
            r = self._c.post("/api/broadcast", json={"messages": [{"topic": f"conv:{conversation_id}", "event": event, "payload": payload, "private": True}]})
            return r.status_code < 300
        except httpx.HTTPError as e:
            log.warning("realtime broadcast failed: %s", type(e).__name__)
            return False
