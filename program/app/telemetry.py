"""Real-time Telemetry Bus for Agent OS / MetaCorp.

Broadcasts live state changes over WebSockets using async Queues:
- PIPELINE_STAGE_CHANGED: 9-stage pipeline progression
- ACP_USAGE_UPDATE: Single-call token and RMB spend
- BUDGET_ALERT_80: 80% budget threshold trigger & pause
- EXP_CRYSTAL_DROP: Experience crystal drop on rejection/consensus
- APPROVAL_REQUIRED: Decoupled human L1 approval cards
- MCP_EXCHANGE_EVENT: Cross-company MCP hiring and blueprints
"""
from __future__ import annotations

import asyncio
import collections
import json
import logging
import threading
import time
from typing import Any

log = logging.getLogger("metacorp.telemetry")


class TelemetryBus:
    """Thread-safe telemetry bus that buffers recent events and pushes to connected queues."""

    def __init__(self, max_buffer: int = 100) -> None:
        self._queues: set[asyncio.Queue] = set()
        self._lock = threading.Lock()
        self._buffer: collections.deque[dict[str, Any]] = collections.deque(maxlen=max_buffer)
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def register_queue(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._queues.add(q)
            recent = list(self._buffer)
        # Seed recent events
        for evt in recent[-15:]:
            msg = json.dumps(evt, ensure_ascii=False)
            try:
                q.put_nowait(msg)
            except Exception:
                pass

    def unregister_queue(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._queues.discard(q)

    def count_connections(self) -> int:
        with self._lock:
            return len(self._queues)

    def recent_events(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._buffer)[-limit:]

    def broadcast(self, event_type: str, data: dict[str, Any]) -> dict[str, Any]:
        """Broadcast an event to all connected WebSocket queues."""
        evt = {
            "type": event_type,
            "data": data,
            "timestamp": time.time(),
            "time_iso": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
        }
        with self._lock:
            self._buffer.append(evt)
            targets = list(self._queues)

        if not targets:
            return evt

        msg = json.dumps(evt, ensure_ascii=False)
        for q in targets:
            try:
                if self._loop and self._loop.is_running():
                    self._loop.call_soon_threadsafe(q.put_nowait, msg)
                else:
                    q.put_nowait(msg)
            except Exception as exc:  # noqa: BLE001
                log.debug("failed to deliver telemetry msg: %s", exc)

        return evt


# Global singleton instance
bus = TelemetryBus()


def emit(event_type: str, **kwargs) -> dict[str, Any]:
    """Convenience helper to emit an event."""
    return bus.broadcast(event_type, kwargs)
