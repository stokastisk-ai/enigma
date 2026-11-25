"""Broadcast hook for conversation updates. Production uses NullBroadcaster: the durable truth is inference_events and browsers poll it
(through the authenticated BFF) from their last seen event_seq. A failed or absent broadcast never fails ingestion."""
from __future__ import annotations

import logging
from typing import Any, Protocol

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
