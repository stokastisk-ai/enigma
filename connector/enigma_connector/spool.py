"""Bounded on-disk spool for TELEMETRY batches that could not be uploaded (offline notebook network, API restart).

One JSON file per batch, written atomically. Oldest-first replay; oldest-first eviction when the byte cap is hit, so a long outage can
never fill the notebook disk. Batches carry an idempotency batch_id, so a replay after a half-delivered upload is harmless.
It never stores prompts or tokens: job events are not spooled (an interrupted job is re-queued by its lease instead)."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


class Spool:
    def __init__(self, directory: str | None, max_bytes: int = 20 * 1024 * 1024):
        self.auto = not directory                      # we created the directory, so we may tidy it up
        self.dir = Path(directory or tempfile.mkdtemp(prefix="enigma-spool-"))
        self.dir.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes
        self.dropped = 0
        self._n = max((int(p.stem) for p in self.dir.glob("*.json") if p.stem.isdigit()), default=0)

    def _files(self) -> list[Path]:
        return sorted(p for p in self.dir.glob("*.json") if p.stem.isdigit())

    def size(self) -> int:
        return sum(p.stat().st_size for p in self._files())

    def __len__(self) -> int:
        return len(self._files())

    def put(self, batch: dict) -> None:
        raw = json.dumps(batch, separators=(",", ":")).encode()
        if len(raw) > self.max_bytes:
            self.dropped += 1
            return
        self._n += 1
        tmp = self.dir / f".{self._n:012d}.tmp"
        tmp.write_bytes(raw)
        os.replace(tmp, self.dir / f"{self._n:012d}.json")
        files = self._files()
        total = sum(p.stat().st_size for p in files)
        while total > self.max_bytes and files:                     # evict oldest first
            victim = files.pop(0)
            total -= victim.stat().st_size
            victim.unlink(missing_ok=True)
            self.dropped += 1

    def peek(self) -> tuple[Path, dict] | None:
        for p in self._files():
            try:
                return p, json.loads(p.read_text())
            except (ValueError, OSError):
                p.unlink(missing_ok=True)                           # corrupt file: discard, never block the queue
                self.dropped += 1
        return None

    def ack(self, path: Path) -> None:
        path.unlink(missing_ok=True)

    def cleanup_if_empty(self) -> bool:
        """Remove an auto-created, empty spool directory after a clean shutdown. A directory still holding undelivered batches,
        or one the user chose, is never touched."""
        if self.auto and not self._files():
            try:
                self.dir.rmdir()
                return True
            except OSError:
                return False
        return False
